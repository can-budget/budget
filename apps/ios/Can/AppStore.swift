import SwiftUI
import AuthenticationServices
import CryptoKit
import WidgetKit
import Network

@MainActor
final class AppStore: ObservableObject {
    @Published private(set) var user: User?
    @Published private(set) var state: LocalState?
    @Published private(set) var isSyncing = false
    @Published private(set) var offline = false
    @Published var needsSignIn = false
    @Published var message: String?
    @Published var challenge: Challenge?
    @Published var invitation: Invitation?
    @Published var focusExpense = false
    @Published private(set) var deletionIntent: AccountDeletionIntent?
    @Published private(set) var deletionReceipt: DeletionAccepted?
    @Published private(set) var deletionConfirmedElsewhere = false
    @Published private(set) var deletionNeedsReauthentication = false
    @Published private(set) var deletionAuthorizationReady = false
    @Published private(set) var deletionBusy = false
    @Published private(set) var localRecoveryFailure: String?
    @Published private(set) var isAuthorizingApple = false
    @Published private(set) var deletionHasReplacementAccount = false
    @Published private(set) var previousLocalAccounts: [PreviousLocalAccount] = []
    let api = APIClient()
    private var polling: Task<Void, Never>?
    private var syncTask: Task<Void, Never>?
    private let network = NWPathMonitor()
    private var failures = 0
    private var nextAttempt = Date.distantPast
    private var onlineBusy = false
    private var authorizingChallenge: Challenge?
    private var deletionAccessToken: String?
    private var deletionAccessUserID: String?
    private var verifiedAppleUserID: String?
    private var reauthToken: String?
    private var reauthExpiresAt: Date?
    private var lastDeviceRegistration = Date.distantPast
    private var sessionGeneration = UUID()
    var deletionReauthenticationIsFresh: Bool { reauthToken != nil && (reauthExpiresAt ?? .distantPast) > Date() }
    var previousCleanupReady: Bool { deletionReauthenticationIsFresh && verifiedAppleUserID != nil }
    var navigationScope: String {
        if localRecoveryFailure != nil { return "local-recovery" }
        if let intent = deletionIntent { return "deletion-\(intent.commandID)" }
        if deletionReceipt != nil || deletionConfirmedElsewhere { return "deletion-accepted" }
        return "\(user?.id ?? "anonymous"):\(user?.active_membership_id ?? "none"):\(needsSignIn)"
    }

    var snapshot: Snapshot? { state?.snapshot }
    var membershipID: String? {
        user?.active_membership_id ?? snapshot?.cabinet.members.first(where: { $0.user_id == user?.id })?.membership_id
    }
    var activePending: [PendingCommand] {
        state?.pending.filter { $0.cabinetID == user?.active_cabinet_id && $0.membershipID == membershipID } ?? []
    }
    var pendingCount: Int { activePending.count }
    var totalPendingCount: Int { state?.pending.count ?? 0 }
    var projection: Projection? {
        guard let state, let membershipID else { return snapshot?.projection }
        return try? Reconciliation.projection(state, membershipID: membershipID)
    }
    var draftProjection: Projection? {
        guard let state, let membershipID else { return projection }
        return (try? Reconciliation.projection(state, membershipID: membershipID, includingDraft: true)) ?? projection
    }
    var draftExceedsBank: Bool {
        guard let state else { return false }
        return Reconciliation.draftExceedsBank(state, projection: projection)
    }
    var statusText: String? {
        if needsSignIn { return "Войдите снова · записи на телефоне сохранены" }
        if activePending.contains(where: { $0.state == "needs_attention" }) { return "Есть записи, требующие внимания" }
        if offline { return "Без связи\(pendingCount > 0 ? " · \(pendingCount) ожидают отправки" : "")" }
        if isSyncing { return "Синхронизация" }
        if pendingCount > 0 { return "Предварительно · \(pendingCount) на телефоне" }
        if let last = state?.lastSyncAt, Date().timeIntervalSince(last) > 900 { return "Данные могли устареть · откройте синхронизацию" }
        if projection?.carry_status == "pending" { return "Предварительно · выберите перенос" }
        return nil
    }
    init() {
        network.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in
                self?.offline = path.status != .satisfied
                if path.status == .satisfied { await self?.syncNow(force: true) }
            }
        }
        network.start(queue: DispatchQueue(label: "can.network"))
    }
    func start() async {
        guard AppConfiguration.issue == nil else { return }
        do { deletionIntent = try DeletionStorage.load(); localRecoveryFailure = nil }
        catch { localRecoveryFailure = "Не удалось прочитать защищённое состояние удаления. Разблокируйте iPhone и повторите. \(error.localizedDescription)"; return }
        do {
            #if DEBUG && targetEnvironment(simulator)
            if ProcessInfo.processInfo.arguments.contains("--can-import-simulator-session") {
                guard deletionIntent == nil else { throw APIError.local("Сначала завершите удаление прежнего аккаунта.") }
                try await SimulatorSessionImport.install(using: api)
            }
            #endif
            if let restored = try await api.restore() {
                user = restored
                if deletionIntent != nil {
                    if restored.id != deletionIntent?.userID { state = try LocalStorage.load(restored.id) }
                    let appleID: String? = try SecureStorage.load("apple-user")
                    deletionHasReplacementAccount = restored.id != deletionIntent?.userID && appleID == deletionIntent?.appleUserID
                    if deletionHasReplacementAccount { deletionNeedsReauthentication = true }
                    await resumeDeletion(); setActive(true); return
                }
                state = try LocalStorage.load(restored.id)
                try refreshPreviousLocalAccounts()
                await checkAppleCredential()
                await syncNow(force: true)
            }
            if deletionIntent != nil { await resumeDeletion(); setActive(true); return }
        } catch {
            guard !RequestFailure.isCancellation(error) else { return }
            message = error.localizedDescription
        }
        setActive(true)
    }
    func setActive(_ active: Bool) {
        polling?.cancel(); polling = nil
        guard active else { return }
        polling = Task { [weak self] in
            await self?.checkAppleCredential()
            while !Task.isCancelled {
                await self?.syncNow()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }
    func appleCredentialRevoked() async {
        // An accepted DELETE revokes Apple/session credentials as part of its normal work.
        // Its receipt proof remains usable independently, so do not discard that request.
        if deletionIntent != nil { return }
        sessionGeneration = UUID()
        syncTask?.cancel(); syncTask = nil
        needsSignIn = true; isSyncing = false
        reauthToken = nil; reauthExpiresAt = nil
        do { try SharedWidgetStorage.clear() }
        catch { message = error.localizedDescription }
        WidgetCenter.shared.reloadAllTimelines()
        do { try await api.forget() }
        catch { message = error.localizedDescription }
        // Keep the protected, user-scoped outbox for a subsequent sign-in by its owner.
    }
    private func checkAppleCredential() async {
        guard user != nil, !needsSignIn,
              let appleUser: String = try? SecureStorage.load("apple-user") else { return }
        let generation = sessionGeneration
        let credentialState: ASAuthorizationAppleIDProvider.CredentialState? = await withCheckedContinuation { continuation in
            ASAuthorizationAppleIDProvider().getCredentialState(forUserID: appleUser) { value, error in
                continuation.resume(returning: error == nil ? value : nil)
            }
        }
        guard generation == sessionGeneration else { return }
        if credentialState == .revoked || credentialState == .notFound { await appleCredentialRevoked() }
    }
    private func mutate(_ body: (inout LocalState) throws -> Void) throws {
        guard deletionIntent == nil else { throw APIError.local("Сначала проверьте результат удаления аккаунта.") }
        guard var next = state else { throw APIError.local("Сначала войдите в приложение.") }
        try body(&next)
        try LocalStorage.save(next)
        state = next
    }
    func setDraft(_ amount: String? = nil, kind: String? = nil) {
        do { try mutate { if let amount { $0.draftAmount = amount }; if let kind { $0.draftKind = kind } } }
        catch { message = error.localizedDescription }
    }
    func prepareApple() async {
        do { challenge = try await api.request("/v1/auth/apple/challenge", method: "POST", authenticated: false) }
        catch { if !RequestFailure.isCancellation(error) { message = error.localizedDescription } }
    }
    func configureApple(_ request: ASAuthorizationAppleIDRequest) {
        guard let challenge else { return }
        isAuthorizingApple = true
        authorizingChallenge = challenge
        request.requestedScopes = [.fullName]
        request.nonce = SHA256.hash(data: Data(challenge.raw_nonce.utf8)).map { String(format: "%02x", $0) }.joined()
    }
    func completeApple(_ result: Result<ASAuthorization, Error>, reauthenticate: Bool = false) async {
        let generation = sessionGeneration
        var knownDeletionIdentity: (userID: String, appleUserID: String)?
        defer { authorizingChallenge = nil; isAuthorizingApple = false }
        do {
            let authorization = try result.get()
            guard let credential = authorization.credential as? ASAuthorizationAppleIDCredential,
                  let token = credential.identityToken.flatMap({ String(data: $0, encoding: .utf8) }),
                  let code = credential.authorizationCode.flatMap({ String(data: $0, encoding: .utf8) }),
                  let challenge = authorizingChallenge else { throw APIError.local("Не получено подтверждение Apple. Повторите вход.") }
            if let intent = deletionIntent {
                guard !intent.needsCleanup, deletionNeedsReauthentication, credential.user == intent.appleUserID else {
                    throw APIError.local("Для проверки удаления подтвердите тот же аккаунт Apple.")
                }
            }
            let storedApple: String? = try SecureStorage.load("apple-user")
            if reauthenticate, deletionIntent == nil, let storedApple, credential.user != storedApple {
                throw APIError.local("Подтвердите тот же аккаунт Apple.")
            }
            if storedApple == credential.user, let userID = user?.id {
                knownDeletionIdentity = (userID, credential.user)
            } else if let userID = try LocalAccountIdentity.load(credential.user) {
                knownDeletionIdentity = (userID, credential.user)
            }
            var body: [String: JSONValue] = ["challenge_id": .string(challenge.challenge_id), "id_token": .string(token),
                                            "authorization_code": .string(code)]
            if let name = credential.fullName {
                let display = PersonNameComponentsFormatter().string(from: name).trimmingCharacters(in: .whitespacesAndNewlines)
                if !display.isEmpty { body["display_name"] = .string(String(display.prefix(40))) }
            }
            let login: LoginResult = try await api.request("/v1/auth/apple", method: "POST", body: JSONEncoder().encode(body), authenticated: false)
            guard generation == sessionGeneration else { throw CancellationError() }
            if let intent = deletionIntent {
                if login.user.id != intent.userID {
                    try LocalAccountIdentity.rememberPrevious(userID: intent.userID, appleUserID: credential.user)
                    let local = try LocalStorage.load(login.user.id)
                    try SharedWidgetStorage.clear()
                    WidgetCenter.shared.reloadAllTimelines()
                    try await api.install(login)
                    guard generation == sessionGeneration else { throw CancellationError() }
                    try SecureStorage.save(credential.user, account: "apple-user")
                    try LocalAccountIdentity.save(userID: login.user.id, appleUserID: credential.user)
                    user = login.user; state = local; needsSignIn = false
                    verifiedAppleUserID = credential.user
                    reauthToken = login.reauth_token; reauthExpiresAt = BusinessTime.parse(login.reauth_expires_at)
                    deletionAccessToken = nil; deletionAccessUserID = nil; deletionAuthorizationReady = false
                    deletionHasReplacementAccount = true
                    try refreshPreviousLocalAccounts()
                    self.challenge = nil
                    return
                }
                deletionAccessToken = login.session.access_token
                deletionAccessUserID = login.user.id
                verifiedAppleUserID = credential.user
                reauthToken = login.reauth_token; reauthExpiresAt = BusinessTime.parse(login.reauth_expires_at)
                deletionAuthorizationReady = true
                self.challenge = nil
                return
            }
            if reauthenticate, login.user.id != user?.id { throw APIError.local("Подтвердите тот же аккаунт Apple.") }
            if let previous = knownDeletionIdentity?.userID, previous != login.user.id {
                try LocalAccountIdentity.rememberPrevious(userID: previous, appleUserID: credential.user)
            }
            let local = try LocalStorage.load(login.user.id)
            if user?.id != login.user.id {
                try SharedWidgetStorage.clear()
                WidgetCenter.shared.reloadAllTimelines()
            }
            sessionGeneration = UUID(); syncTask?.cancel(); syncTask = nil
            try await api.install(login)
            try SecureStorage.save(credential.user, account: "apple-user")
            try LocalAccountIdentity.save(userID: login.user.id, appleUserID: credential.user)
            user = login.user; state = local; needsSignIn = false
            verifiedAppleUserID = credential.user
            try refreshPreviousLocalAccounts()
            reauthToken = login.reauth_token; reauthExpiresAt = BusinessTime.parse(login.reauth_expires_at)
            deletionAuthorizationReady = reauthenticate
            self.challenge = nil
            await syncNow(force: true)
            await registerDevice()
        } catch is CancellationError { }
        catch let error as ASAuthorizationError where error.code == .canceled { }
        catch let error as APIError where error.code == "account_deletion_pending" {
            guard generation == sessionGeneration else { return }
            if let identity = knownDeletionIdentity {
                do {
                    let cleanup = try AccountDeletionIntent.confirmedDeletion(userID: identity.userID, appleUserID: identity.appleUserID)
                    try DeletionStorage.save(cleanup)
                    deletionIntent = cleanup
                    try await finishAcceptedDeletion(cleanup)
                } catch { message = error.localizedDescription }
            } else { message = error.localizedDescription }
        }
        catch {
            message = error.localizedDescription
            challenge = nil
            await prepareApple()
        }
    }
    func enqueueEntry() throws {
        guard let state, let snapshot, let sprint = snapshot.sprint, let membershipID,
              projection?.status == "active", let amount = Money.parse(state.draftAmount), amount > 0 else {
            throw APIError.local("Укажите сумму и действующий спринт.")
        }
        guard state.pending.count < 1000 else { throw APIError.local("В очереди 1000 записей. Ввод сохранён; сначала синхронизируйте очередь.") }
        guard !draftExceedsBank else { throw APIError(code: "insufficient_funds", message: "", retryable: false) }
        let now = Date().addingTimeInterval(state.serverOffset)
        let command = Command("add_entry", ["entry_id": .string(UUID().uuidString.lowercased()), "sprint_id": .string(sprint.id),
                                           "membership_id": .string(membershipID), "kind": .string(state.draftKind),
                                           "amount_minor": .integer(amount), "occurred_at": .string(BusinessTime.timestamp(now)),
                                           "allow_completed": .bool(false)])
        try mutate {
            $0.pending.append(PendingCommand(command: command, cabinetID: snapshot.cabinet.id,
                                             membershipID: membershipID, createdAt: now))
            // This single atomic write is the local Entry + PendingCommand transaction.
            $0.draftAmount = ""; $0.draftKind = "expense"
        }
        updateWidget()
        Task { await syncNow(force: true) }
    }
    func syncNow(force: Bool = false) async {
        if let syncTask { await syncTask.value; return }
        guard deletionIntent == nil, user != nil, AppConfiguration.issue == nil, !needsSignIn,
              force || Date() >= nextAttempt else { return }
        let task = Task { await self.performSync() }
        let generation = sessionGeneration
        syncTask = task
        await task.value
        if generation == sessionGeneration { syncTask = nil }
    }
    private func performSync() async {
        let generation = sessionGeneration
        isSyncing = true
        defer { if generation == sessionGeneration { isSyncing = false } }
        do {
            let me: User = try await api.request("/v1/me")
            guard generation == sessionGeneration else { return }
            guard me.id == user?.id else { throw APIError.local("Сменился аккаунт. Войдите заново.") }
            if me.active_cabinet_id != user?.active_cabinet_id || me.active_membership_id != user?.active_membership_id {
                try mutate { $0.snapshot = nil }
                try SharedWidgetStorage.clear()
                WidgetCenter.shared.reloadAllTimelines()
                lastDeviceRegistration = .distantPast
            }
            try await api.updateUser(me)
            guard generation == sessionGeneration else { return }
            user = me
            guard let cabinetID = me.active_cabinet_id else {
                try mutate { $0.snapshot = nil }
                try SharedWidgetStorage.clear()
                WidgetCenter.shared.reloadAllTimelines()
                failures = 0; return
            }
            try await readSync(cabinetID)
            guard generation == sessionGeneration else { return }
            guard let membershipID else { throw APIError.local("Сервер не передал членство. Отправка остановлена; записи сохранены.") }
            let toSend = state?.pending.filter { $0.cabinetID == cabinetID && $0.membershipID == membershipID &&
                ["queued", "unknown", "sending"].contains($0.state) } ?? []
            for item in toSend {
                try mutate { next in
                    if let i = next.pending.firstIndex(where: { $0.id == item.id }) { next.pending[i].state = "sending" }
                }
                do {
                    let result: CommandResult = try await api.request("/v1/cabinets/\(cabinetID)/commands", method: "POST", body: JSONEncoder().encode(item.command))
                    guard generation == sessionGeneration else { return }
                    try mutate { next in
                        if let i = next.pending.firstIndex(where: { $0.id == item.id }) {
                            next.pending[i].state = "acknowledged"; next.pending[i].resultRevision = result.result_revision
                        }
                    }
                } catch let error as APIError where !error.retryable && error.code != "unauthorized" {
                    // Confirm terminal rejection via the coherent receipt before enabling recovery.
                    try await readSync(cabinetID)
                    throw error
                }
            }
            if !toSend.isEmpty { try await readSync(cabinetID) }
            offline = false; failures = 0; nextAttempt = .distantPast
            updateWidget()
            await registerDevice()
        } catch {
            guard generation == sessionGeneration, !RequestFailure.isCancellation(error) else { return }
            failures = min(failures + 1, 6)
            nextAttempt = Date().addingTimeInterval(min(pow(2, Double(failures - 1)), 60) + Double.random(in: 0...0.8))
            if let apiError = error as? APIError, apiError.code == "unauthorized" { await appleCredentialRevoked() }
            else if error is URLError { offline = true }
            else { message = error.localizedDescription }
        }
    }
    private func readSync(_ cabinetID: String) async throws {
        let generation = sessionGeneration
        let ids = Set(state?.pending.filter { $0.cabinetID == cabinetID }.map(\.id) ?? [])
        let body: [String: JSONValue] = ["unresolved_command_ids": .array(ids.sorted().map(JSONValue.string))]
        let response: SyncResponse = try await api.request("/v1/cabinets/\(cabinetID)/sync", method: "POST", body: JSONEncoder().encode(body))
        guard generation == sessionGeneration else { throw CancellationError() }
        try mutate { $0 = try Reconciliation.apply(response, requestedIDs: ids, to: $0) }
    }
    func onlineCommand(_ type: String, payload: [String: JSONValue], key: String? = nil) async throws -> CommandResult {
        guard !onlineBusy else { throw APIError.local("Предыдущая команда ещё проверяется.") }
        onlineBusy = true; defer { onlineBusy = false }
        guard let cabinet = user?.active_cabinet_id, let membershipID else { throw APIError.local("Нет активного кабинета.") }
        let intentKey = "\(cabinet):\(membershipID):\(key ?? type)"
        let command: Command
        if let existing = state?.onlineIntents[intentKey] {
            guard existing.command.payload == payload else {
                throw APIError.local("Сначала проверьте прежнее действие в разделе «Синхронизация». Его ответ мог потеряться.")
            }
            command = existing.command
        } else {
            command = Command(type, payload)
            try mutate { $0.onlineIntents[intentKey] = OnlineIntent(cabinetID: cabinet, membershipID: membershipID, command: command) }
        }
        return try await sendIntent(command, key: intentKey, cabinetID: cabinet)
    }
    private func sendIntent(_ command: Command, key: String, cabinetID: String) async throws -> CommandResult {
        let generation = sessionGeneration
        do {
            let result: CommandResult = try await api.request("/v1/cabinets/\(cabinetID)/commands", method: "POST", body: JSONEncoder().encode(command))
            guard generation == sessionGeneration else { throw CancellationError() }
            try mutate {
                if command.type == "create_sprint" {
                    if $0.snapshot?.sprint?.id != result.entity_id { $0.draftAmount = ""; $0.draftKind = "expense" }
                } else { $0.onlineIntents.removeValue(forKey: key) }
            }
            if let invitation = result.invitation { self.invitation = invitation }
            await syncNow(force: true)
            if command.type == "create_sprint" {
                guard let confirmed = result.result_revision,
                      let revision = state?.snapshot?.cabinet_revision, revision >= confirmed else {
                    throw APIError(code: "budget_sync_pending", message: "", retryable: true)
                }
                try mutate { $0.onlineIntents.removeValue(forKey: key) }
            }
            return result
        } catch let error as APIError where !error.retryable && error.code != "unauthorized" {
            guard generation == sessionGeneration else { throw CancellationError() }
            try mutate { $0.onlineIntents.removeValue(forKey: key) }
            await syncNow(force: true)
            throw error
        }
    }
    func retryIntent(_ key: String) async {
        guard let intent = state?.onlineIntents[key] else { return }
        let generation = sessionGeneration
        do {
            if intent.cabinetID != user?.active_cabinet_id || intent.membershipID != membershipID {
                let receipt: Receipt = try await api.request("/v1/commands/\(intent.command.command_id)/receipt")
                guard generation == sessionGeneration else { return }
                try Reconciliation.validate(receipt)
                if receipt.status == "applied" || receipt.status == "rejected" {
                    try mutate { $0.onlineIntents.removeValue(forKey: key) }
                    message = receipt.status == "applied" ? "Прежнее действие было выполнено." : "Прежнее действие не было выполнено."
                } else { message = "Сервер пока не подтвердил исход прежнего действия. Оно сохранено и не будет отправлено от нового членства." }
            } else { _ = try await sendIntent(intent.command, key: key, cabinetID: intent.cabinetID) }
        }
        catch { message = error.localizedDescription }
    }
    func checkQuarantined(_ item: PendingCommand) async {
        let generation = sessionGeneration
        do {
            let receipt: Receipt = try await api.request("/v1/commands/\(item.id)/receipt")
            guard generation == sessionGeneration else { return }
            try Reconciliation.validate(receipt)
            try mutate { next in
                guard let index = next.pending.firstIndex(where: { $0.id == item.id }) else { return }
                if receipt.status == "applied" { next.pending.remove(at: index) }
                else if receipt.status == "rejected" { next.pending[index].state = "needs_attention"; next.pending[index].error = receipt.error }
            }
            if receipt.status == "unknown" { message = "Исход прежней записи пока неизвестен. Она сохранена и не переносится в новый кабинет." }
        } catch { message = error.localizedDescription }
    }
    func createCabinet(name: String) async throws {
        let body: [String: JSONValue] = ["command_id": .string(UUID().uuidString.lowercased()),
                                       "name": .string(name), "timezone": .string("Asia/Shanghai")]
        try await cabinetRequest(path: "/v1/cabinets", proposed: body)
    }
    func previewInvitation(_ code: String) async throws -> InvitationPreview {
        try await api.request("/v1/invitations/preview", method: "POST", body: JSONEncoder().encode(["code": code]))
    }
    func join(_ code: String) async throws {
        try await cabinetRequest(path: "/v1/invitations/accept", proposed: ["command_id": .string(UUID().uuidString.lowercased()), "code": .string(code)])
    }
    private func cabinetRequest(path: String, proposed: [String: JSONValue]) async throws {
        let generation = sessionGeneration
        var body = proposed
        if let prior = state?.cabinetIntent {
            guard prior["path"]?.text == path else { throw APIError.local("Повторите прежнее создание или вступление: его ответ ещё не выяснен.") }
            body = prior; body.removeValue(forKey: "path")
            guard body.filter({ $0.key != "command_id" }) == proposed.filter({ $0.key != "command_id" }) else {
                throw APIError.local("Повторите прежние данные: результат первого запроса неизвестен.")
            }
        } else {
            var saved = body; saved["path"] = .string(path)
            try mutate { $0.cabinetIntent = saved }
        }
        do {
            let _: CabinetResult = try await api.request(path, method: "POST", body: JSONEncoder().encode(body))
            guard generation == sessionGeneration else { throw CancellationError() }
            try mutate { $0.cabinetIntent = nil }
            await syncNow(force: true)
        } catch let error as APIError where !error.retryable && error.code != "unauthorized" {
            guard generation == sessionGeneration else { throw CancellationError() }
            try mutate { $0.cabinetIntent = nil }; throw error
        }
    }
    func requireSettled() async throws {
        await syncNow(force: true)
        guard !offline, !needsSignIn, let last = state?.lastSyncAt, Date().timeIntervalSince(last) < 30 else {
            throw APIError.local("Для этого действия нужна свежая синхронизация.")
        }
        guard activePending.isEmpty else { throw APIError.local("Сначала разберите ожидающие записи в разделе «Синхронизация».") }
        guard state?.onlineIntents.values.contains(where: { $0.cabinetID == user?.active_cabinet_id && $0.membershipID == membershipID }) != true else {
            throw APIError.local("Сначала проверьте исход предыдущих действий в разделе «Синхронизация».")
        }
    }
    func refreshAfterServerRestore() async {
        sessionGeneration = UUID(); syncTask?.cancel(); syncTask = nil; isSyncing = false
        do {
            try mutate { $0 = Reconciliation.prepareForServerRestore($0) }
            try SharedWidgetStorage.clear()
            WidgetCenter.shared.reloadAllTimelines()
            lastDeviceRegistration = .distantPast
            await syncNow(force: true)
        } catch { message = error.localizedDescription }
    }
    func retryAfterServerRestore(_ item: PendingCommand) throws {
        try mutate { next in
            guard let index = next.pending.firstIndex(where: { $0.id == item.id }),
                  next.pending[index].state == "restore_review", next.pending[index].membershipID == membershipID,
                  next.pending[index].cabinetID == user?.active_cabinet_id else {
                throw APIError.local("Сначала нужно подтвердить отсутствие квитанции после восстановления сервера.")
            }
            next.pending[index].state = "unknown"
            next.pending[index].resultRevision = nil
        }
        updateWidget()
        Task { await syncNow(force: true) }
    }
    func recover(_ item: PendingCommand, correctedDate: Date? = nil) throws {
        try mutate { next in
            guard let current = next.pending.first(where: { $0.id == item.id }),
                  current.cabinetID == user?.active_cabinet_id, current.membershipID == membershipID else {
                throw APIError.local("Эту запись нельзя перенести в другое членство.")
            }
            let replacement = try current.recovery(correctedDate: correctedDate)
            // The original terminal receipt remains in the server audit, linked by recovery_of_command_id.
            next.pending.removeAll { $0.id == item.id }
            next.pending.append(replacement)
        }
        Task { await syncNow(force: true) }
    }
    func discardUnsent(_ item: PendingCommand) throws {
        try mutate { next in
            guard let index = next.pending.firstIndex(where: { $0.id == item.id }), next.pending[index].state == "queued" else {
                throw APIError.local("Отправленную запись сначала нужно сверить с сервером.")
            }
            next.pending.remove(at: index)
        }
        updateWidget()
    }
    func dismissRejected(_ item: PendingCommand) throws {
        try mutate { next in
            guard let index = next.pending.firstIndex(where: { $0.id == item.id }),
                  next.pending[index].state == "needs_attention", next.pending[index].error != nil else {
                throw APIError.local("Сначала нужно подтвердить отказ сервера.")
            }
            next.pending.remove(at: index)
        }
        updateWidget()
    }
    func renameProfile(_ name: String) async throws {
        let generation = sessionGeneration
        let result: User = try await api.request("/v1/me", method: "PATCH", body: JSONEncoder().encode(["display_name": name]))
        guard generation == sessionGeneration else { throw CancellationError() }
        try await api.updateUser(result)
        guard generation == sessionGeneration else { throw CancellationError() }
        user = result
    }
    func logout() async throws {
        let generation = sessionGeneration
        let _: EmptyResponse = try await api.request("/v1/auth/logout", method: "POST")
        guard generation == sessionGeneration else { throw CancellationError() }
        sessionGeneration = UUID(); syncTask?.cancel(); syncTask = nil
        try await api.forget()
        try SharedWidgetStorage.clear()
        WidgetCenter.shared.reloadAllTimelines()
        // The private per-user file retains pending commands for this same account only.
        user = nil; state = nil; needsSignIn = false
    }
    func deleteAccount() async throws {
        guard deletionIntent == nil, !deletionBusy, let user, let reauthToken,
              deletionAuthorizationReady, deletionReauthenticationIsFresh,
              let appleUserID: String = try SecureStorage.load("apple-user") else {
            throw APIError.local("Сначала подтвердите личность кнопкой Apple ниже.")
        }
        let intent = try AccountDeletionIntent(userID: user.id, appleUserID: appleUserID, reauthToken: reauthToken)
        // Persist the exact replay proof before the destructive HTTP request can leave the device.
        try DeletionStorage.save(intent)
        deletionIntent = intent
        sessionGeneration = UUID(); syncTask?.cancel(); syncTask = nil; isSyncing = false
        deletionBusy = true; defer { deletionBusy = false }
        await api.invalidateRequests()
        do { try await sendDeletion(intent, authenticated: true) }
        catch {
            // A lost 202 may already have revoked every session. The saved proof can still replay it.
            if let error = error as? APIError, error.code == "unauthorized" {
                do { try await replayDeletion(intent); return }
                catch let replayError as APIError where replayError.code == "unauthorized" { deletionNeedsReauthentication = true }
                catch { }
            }
            message = "Результат удаления пока не подтверждён. Запрос сохранён. Проверьте его результат повторно."
        }
    }
    func prepareDeletionConfirmation() async {
        deletionAuthorizationReady = false
        await prepareApple()
    }
    func resumeDeletion() async {
        guard let intent = deletionIntent, !deletionBusy else { return }
        deletionBusy = true; defer { deletionBusy = false }
        do {
            if intent.needsCleanup { try await finishAcceptedDeletion(intent) }
            else { try await replayDeletion(intent) }
        } catch {
            if let error = error as? APIError, error.code == "unauthorized" {
                deletionNeedsReauthentication = true
                message = "Принятие удаления не подтверждено. Сначала повторно проверьте запрос; если подтверждения нет, войдите тем же Apple Account и подтвердите повтор."
            } else { message = error.localizedDescription }
        }
    }
    func retryDeletionWithFreshAuthorization() async throws {
        guard let intent = deletionIntent, !intent.needsCleanup, !deletionBusy,
              !deletionHasReplacementAccount, deletionAccessUserID == intent.userID,
              deletionNeedsReauthentication, deletionAuthorizationReady,
              deletionReauthenticationIsFresh, let token = reauthToken, let access = deletionAccessToken else {
            throw APIError.local("Сначала подтвердите тот же аккаунт Apple.")
        }
        deletionBusy = true; defer { deletionBusy = false }
        // Always resolve the original proof first; an accepted request must keep its original proof.
        do {
            try await replayDeletion(intent)
            return
        } catch let error as APIError where error.code == "unauthorized" { }
        let refreshed = try intent.replacingAuthorization(token)
        try DeletionStorage.save(refreshed)
        deletionIntent = refreshed
        try await sendDeletion(refreshed, authenticated: false, accessToken: access)
    }
    private func replayDeletion(_ intent: AccountDeletionIntent) async throws {
        var lastError: Error = APIError.local("Сохранённое подтверждение удаления недоступно.")
        for proof in intent.replayProofs {
            do { try await sendDeletion(intent, authenticated: false, proof: proof); return }
            catch let error as APIError where error.code == "unauthorized" { lastError = error }
        }
        throw lastError
    }
    private func sendDeletion(_ intent: AccountDeletionIntent, authenticated: Bool, accessToken: String? = nil, proof: String? = nil) async throws {
        guard !intent.needsCleanup, deletionIntent?.commandID == intent.commandID, let proof = proof ?? intent.reauthToken else {
            throw APIError.local("Сохранённое подтверждение удаления недоступно.")
        }
        let generation = sessionGeneration
        var headers = ["X-Reauth-Token": proof]
        if let accessToken { headers["Authorization"] = "Bearer \(accessToken)" }
        let accepted: DeletionAccepted = try await api.request("/v1/me", method: "DELETE", body: JSONEncoder().encode(intent.body),
            authenticated: authenticated, headers: headers, expectedStatus: 202)
        guard generation == sessionGeneration, deletionIntent?.commandID == intent.commandID else { throw CancellationError() }
        let next = try intent.accepting(accepted)
        // This durable marker resumes local erasure after termination or a protected-file error.
        try DeletionStorage.save(next)
        deletionIntent = next
        try await finishAcceptedDeletion(next)
    }
    private func finishAcceptedDeletion(_ intent: AccountDeletionIntent) async throws {
        guard intent.needsCleanup, deletionIntent?.commandID == intent.commandID else { return }
        if let receipt = intent.accepted { try receipt.validate() }
        let ownsVisibleSession = AccountDeletionCleanupScope.clearsSharedCredentials(deletingUserID: intent.userID, currentUserID: user?.id)
        sessionGeneration = UUID(); syncTask?.cancel(); syncTask = nil; isSyncing = false
        let cleanupGeneration = sessionGeneration
        try await api.forget(userID: intent.userID)
        guard cleanupGeneration == sessionGeneration, deletionIntent?.commandID == intent.commandID else { throw CancellationError() }
        try LocalStorage.remove(intent.userID)
        try SecureStorage.remove("device-\(intent.userID)")
        try DeletionStorage.clearArchive(intent.userID)
        try LocalAccountIdentity.remove(userID: intent.userID, appleUserID: intent.appleUserID)
        let appleUser: String? = try SecureStorage.load("apple-user")
        if ownsVisibleSession, appleUser == intent.appleUserID { try SecureStorage.remove("apple-user") }
        if ownsVisibleSession {
            try SharedWidgetStorage.clear()
            try SecureStorage.remove("app-push")
            try SecureStorage.remove("widget-push", shared: true)
            user = nil; state = nil; needsSignIn = false
            invitation = nil; challenge = nil; authorizingChallenge = nil
            WidgetCenter.shared.reloadAllTimelines()
        }
        reauthToken = nil; reauthExpiresAt = nil
        deletionAccessToken = nil
        deletionAccessUserID = nil; verifiedAppleUserID = nil
        deletionHasReplacementAccount = false
        deletionNeedsReauthentication = false; deletionAuthorizationReady = false
        lastDeviceRegistration = .distantPast
        try DeletionStorage.clear()
        deletionIntent = nil
        deletionReceipt = intent.accepted
        deletionConfirmedElsewhere = intent.confirmedByAppleLogin == true
        try refreshPreviousLocalAccounts()
    }
    func dismissDeletionReceipt() {
        deletionReceipt = nil; deletionConfirmedElsewhere = false
        UIApplication.shared.registerForRemoteNotifications()
        setActive(true)
    }
    private func refreshPreviousLocalAccounts() throws {
        guard let user, let appleID: String = try SecureStorage.load("apple-user"),
              try LocalAccountIdentity.load(appleID) == user.id else { previousLocalAccounts = []; return }
        previousLocalAccounts = try LocalAccountIdentity.previous(appleID).filter { $0 != user.id }.map { id in
            let local = try? LocalStorage.load(id)
            let count = local.map { $0.pending.count + $0.onlineIntents.count + ($0.cabinetIntent == nil ? 0 : 1) }
            return PreviousLocalAccount(id: id, pendingCount: count, hasDeletionRequest: try DeletionStorage.archived(id) != nil)
        }
    }
    func continueWithReplacementAccount() throws {
        guard let intent = deletionIntent, !intent.needsCleanup, deletionHasReplacementAccount,
              !deletionBusy, let user, user.id != intent.userID else { throw APIError.local("Сначала подтвердите тот же Apple Account.") }
        try DeletionStorage.archive(intent)
        try DeletionStorage.clear()
        deletionIntent = nil
        deletionNeedsReauthentication = false; deletionAuthorizationReady = false; deletionHasReplacementAccount = false
        deletionAccessToken = nil; deletionAccessUserID = nil
        try refreshPreviousLocalAccounts()
        message = "Прежние локальные данные и результат прежнего запроса сохранены. Они доступны в настройках и не меняют новый бюджет."
        Task { await syncNow(force: true) }
    }
    func removePreviousLocalAccount(_ id: String) throws {
        guard let user, let appleID: String = try SecureStorage.load("apple-user"),
              previousCleanupReady, !deletionBusy else { throw APIError.local("Сначала подтвердите текущий Apple Account.") }
        let previous = try LocalAccountIdentity.previous(appleID)
        try PreviousAccountCleanup.validate(previousUserID: id, currentUserID: user.id, expectedAppleUserID: appleID,
                                            verifiedAppleUserID: verifiedAppleUserID, knownPreviousIDs: previous)
        deletionBusy = true; defer { deletionBusy = false }
        try LocalStorage.remove(id)
        try SecureStorage.remove("device-\(id)")
        try DeletionStorage.clearArchive(id)
        try LocalAccountIdentity.removePrevious(userID: id, appleUserID: appleID)
        if deletionIntent?.userID == id {
            try DeletionStorage.clear()
            deletionIntent = nil
            deletionNeedsReauthentication = false; deletionAuthorizationReady = false; deletionHasReplacementAccount = false
            deletionAccessToken = nil; deletionAccessUserID = nil
        }
        try refreshPreviousLocalAccounts()
        message = "Прежние данные удалены только с этого iPhone. Это не подтверждает удаление аккаунта на сервере. Текущий аккаунт сохранён."
        Task { await syncNow(force: true) }
    }
    func checkPreviousDeletion(_ id: String) async {
        guard !deletionBusy, deletionIntent == nil, previousLocalAccounts.contains(where: { $0.id == id }),
              let user, user.id != id else { return }
        let generation = sessionGeneration
        deletionBusy = true; defer { deletionBusy = false }
        do {
            guard let intent = try DeletionStorage.archived(id), intent.userID == id,
                  let appleID: String = try SecureStorage.load("apple-user"), intent.appleUserID == appleID else { return }
            for proof in intent.replayProofs {
                do {
                    let response: DeletionAccepted = try await api.request("/v1/me", method: "DELETE", body: JSONEncoder().encode(intent.body),
                        authenticated: false, headers: ["X-Reauth-Token": proof], expectedStatus: 202)
                    guard generation == sessionGeneration, user.id == self.user?.id else { throw CancellationError() }
                    let accepted = try intent.accepting(response)
                    try DeletionStorage.save(accepted); deletionIntent = accepted
                    try await finishAcceptedDeletion(accepted)
                    return
                } catch let error as APIError where error.code == "unauthorized" { continue }
            }
            message = "Сервер не подтвердил принятие прежнего удаления. Прежние локальные данные сохранены."
        } catch { message = error.localizedDescription }
    }
    func updateWidget() {
        guard let snapshot, let state, let last = state.lastSyncAt else { return }
        let p = projection
        let shared = SharedWidgetState(cabinetID: snapshot.cabinet.id, revision: snapshot.cabinet_revision,
                                      businessDate: max(snapshot.business_date, BusinessTime.day(Date().addingTimeInterval(state.serverOffset))),
                                      calculatedAt: Date(), lastSyncAt: last, todayMinor: p?.today_remaining_minor,
                                      bankMinor: p?.bank_minor, pendingCount: pendingCount,
                                      carryPending: p?.carry_status == "pending", status: p?.status ?? "no_sprint",
                                      daysRemaining: p?.days_remaining, isOffline: offline)
        do { try SharedWidgetStorage.save(shared); WidgetCenter.shared.reloadAllTimelines() }
        catch { message = "Запись сохранена. Не удалось обновить виджет: \(error.localizedDescription)" }
    }
    func registerDevice(force: Bool = false) async {
        let generation = sessionGeneration
        guard deletionIntent == nil, let user, user.active_cabinet_id != nil, force || Date().timeIntervalSince(lastDeviceRegistration) > 300 else { return }
        do {
            let account = "device-\(user.id)"
            let deviceID = try SecureStorage.load(account, as: String.self) ?? UUID().uuidString.lowercased()
            try SecureStorage.save(deviceID, account: account)
            let token: WidgetToken? = try SecureStorage.load("widget-read", shared: true)
            let appPush: String? = try SecureStorage.load("app-push")
            let widgetPush: String? = try SecureStorage.load("widget-push", shared: true)
            let registration: [String: JSONValue] = ["device_id": .string(deviceID),
                "app_apns_token": appPush.map(JSONValue.string) ?? .null,
                "widget_push_token": widgetPush.map(JSONValue.string) ?? .null,
                "apns_environment": .string(AppConfiguration.value("CanAPNSEnvironment")),
                "app_version": .string(AppConfiguration.value("CFBundleShortVersionString")),
                "os_version": .string(UIDevice.current.systemVersion)]
            let _: EmptyResponse = try await api.request("/v1/devices", method: "POST", body: JSONEncoder().encode(registration))
            guard generation == sessionGeneration, user.active_cabinet_id == self.user?.active_cabinet_id,
                  user.active_membership_id == self.user?.active_membership_id else { return }
            if token == nil || (BusinessTime.parse(token!.expires_at) ?? .distantPast) < Date().addingTimeInterval(86400) {
                let new: WidgetToken = try await api.request("/v1/devices/\(deviceID)/widget-token", method: "POST")
                guard generation == sessionGeneration, user.active_cabinet_id == self.user?.active_cabinet_id,
                      user.active_membership_id == self.user?.active_membership_id else { return }
                try SecureStorage.save(new, account: "widget-read", shared: true)
                WidgetCenter.shared.reloadAllTimelines()
            }
            lastDeviceRegistration = Date()
        } catch {
            // Financial sync remains successful; the widget stays on its timestamped cache.
        }
    }
}
