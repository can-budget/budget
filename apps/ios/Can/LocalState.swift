import Foundation

struct PendingCommand: Codable, Identifiable {
    var command: Command
    let cabinetID: String
    let membershipID: String
    let createdAt: Date
    var state: String = "queued"
    var error: APIError?
    var resultRevision: Int64?
    var id: String { command.command_id }
    var canRecover: Bool { state == "needs_attention" && ["clock_skew", "entry_outside_sprint", "completed_confirmation_required"].contains(error?.code ?? "") }

    func recovery(correctedDate: Date? = nil, commandID: String = UUID().uuidString.lowercased()) throws -> PendingCommand {
        guard canRecover, command.type == "add_entry", commandID != id else {
            throw APIError.local("Для восстановления нужен подтверждённый отказ сервера.")
        }
        var payload = command.payload
        payload["allow_completed"] = .bool(true)
        payload["recovery_of_command_id"] = .string(id)
        if let correctedDate {
            payload["original_occurred_at"] = payload["occurred_at"]
            payload["occurred_at"] = .string(BusinessTime.timestamp(correctedDate))
        }
        return PendingCommand(command: Command("add_entry", payload, id: commandID), cabinetID: cabinetID,
                              membershipID: membershipID, createdAt: Date())
    }
}
struct OnlineIntent: Codable {
    let cabinetID: String
    let membershipID: String
    let command: Command
}
struct LocalState: Codable {
    var schemaVersion = 1
    let userID: String
    var snapshot: Snapshot?
    var pending: [PendingCommand] = []
    var draftAmount = ""
    var draftKind = "expense"
    var lastSyncAt: Date?
    var serverOffset: TimeInterval = 0
    var onlineIntents: [String: OnlineIntent] = [:]
    var cabinetIntent: [String: JSONValue]?
    var restoreReviewRequested: Bool?
}
enum LocalStorage {
    private static func root(_ directory: URL?) throws -> URL {
        let base: URL
        if let directory { base = directory }
        else { base = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                  appropriateFor: nil, create: true).appendingPathComponent("Can", isDirectory: true) }
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        var excluded = base
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        try excluded.setResourceValues(values)
        return base
    }
    private static func validate(_ userID: String) throws {
        guard UUID(uuidString: userID) != nil else { throw APIError.local("Некорректная учётная запись.") }
    }
    static func url(_ userID: String, directory: URL? = nil) throws -> URL {
        try validate(userID)
        let base = try root(directory)
        let scope = base.appendingPathComponent("production-\(userID)", isDirectory: true)
        try FileManager.default.createDirectory(at: scope, withIntermediateDirectories: true)
        let location = scope.appendingPathComponent("state.json")
        let legacy = base.appendingPathComponent("production-\(userID).json")
        if !FileManager.default.fileExists(atPath: location.path), FileManager.default.fileExists(atPath: legacy.path) {
            try FileManager.default.moveItem(at: legacy, to: location)
        }
        return location
    }
    static func load(_ userID: String) throws -> LocalState {
        let location = try url(userID)
        if !FileManager.default.fileExists(atPath: location.path) { return LocalState(userID: userID) }
        let state = try JSONDecoder().decode(LocalState.self, from: Data(contentsOf: location))
        guard state.schemaVersion == 1, state.userID == userID else {
            throw APIError.local("Локальные данные требуют совместимой версии приложения. Они сохранены.")
        }
        return state
    }
    static func save(_ state: LocalState) throws {
        try JSONEncoder().encode(state).write(to: url(state.userID), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
    static func remove(_ userID: String, directory: URL? = nil) throws {
        try validate(userID)
        let base = try root(directory)
        // Each user's atomic-write temporaries live in this directory, so cleanup is complete and scoped.
        for name in ["production-\(userID)", "production-\(userID).json"] {
            let location = base.appendingPathComponent(name)
            if FileManager.default.fileExists(atPath: location.path) { try FileManager.default.removeItem(at: location) }
        }
    }
}

enum Reconciliation {
    static func prepareForServerRestore(_ state: LocalState) -> LocalState {
        var next = state
        next.snapshot = nil; next.lastSyncAt = nil; next.restoreReviewRequested = true
        return next
    }
    static func validate(_ receipt: Receipt) throws {
        let valid: Bool
        switch receipt.status {
        case "applied":
            valid = receipt.error == nil && receipt.result?.status == "applied" && receipt.result?.command_id == receipt.command_id
                && receipt.result?.result_revision == receipt.result_revision
        case "rejected": valid = receipt.error != nil && receipt.result == nil && receipt.result_revision == nil
        case "unknown": valid = receipt.error == nil && receipt.result == nil && receipt.result_revision == nil
        default: valid = false
        }
        guard valid else { throw APIError.local("Сервер прислал противоречивый результат. Очередь сохранена.") }
    }
    static func accepts(_ new: Snapshot, over old: Snapshot?) -> Bool {
        guard let old, new.cabinet.id == old.cabinet.id else { return true }
        return new.cabinet_revision >= old.cabinet_revision && new.business_date >= old.business_date
    }
    static func apply(_ response: SyncResponse, requestedIDs: Set<String>, to current: LocalState) throws -> LocalState {
        let receiptIDs = response.receipts.map(\.command_id)
        guard Set(receiptIDs) == requestedIDs, receiptIDs.count == requestedIDs.count else {
            throw APIError.local("Неполный ответ синхронизации. Очередь сохранена.")
        }
        try response.receipts.forEach(validate)
        guard accepts(response.snapshot, over: current.snapshot) else { return current }
        var next = current
        next.snapshot = response.snapshot
        if let previous = current.snapshot?.sprint?.id, previous != response.snapshot.sprint?.id {
            next.draftAmount = ""; next.draftKind = "expense"
        }
        for receipt in response.receipts {
            guard let index = next.pending.firstIndex(where: { $0.id == receipt.command_id }) else { continue }
            switch receipt.status {
            case "applied":
                guard let revision = receipt.result_revision, revision <= response.snapshot.cabinet_revision else {
                    throw APIError.local("Ответ команды новее снимка. Повторяем проверку.")
                }
                next.pending.remove(at: index)
            case "rejected":
                next.pending[index].state = "needs_attention"
                next.pending[index].error = receipt.error
            case "unknown":
                // An acknowledged command must never be overlaid on its committed snapshot.
                if next.pending[index].state == "acknowledged" {
                    if current.restoreReviewRequested == true { next.pending[index].state = "restore_review" }
                    continue
                }
                if next.pending[index].state == "restore_review" { continue }
                next.pending[index].state = "unknown"
            default: throw APIError.local("Неизвестное состояние команды.")
            }
        }
        next.lastSyncAt = Date()
        next.restoreReviewRequested = false
        if let server = BusinessTime.parse(response.snapshot.server_time) { next.serverOffset = server.timeIntervalSinceNow }
        return next
    }
    static func draftExceedsBank(_ state: LocalState, projection: Projection?) -> Bool {
        guard state.draftKind == "expense", let amount = Money.parse(state.draftAmount), let projection else { return false }
        return amount > max(0, projection.bank_minor)
    }
    static func projection(_ state: LocalState, membershipID: String, now: Date = Date(), includingDraft: Bool = false) throws -> Projection? {
        guard let snapshot = state.snapshot, let sprint = snapshot.sprint else { return nil }
        var additions: [DayInput] = state.pending.compactMap { item in
            guard item.cabinetID == snapshot.cabinet.id, item.membershipID == membershipID,
                  item.command.type == "add_entry",
                  (["queued", "unknown", "sending"].contains(item.state) ||
                   (item.state == "acknowledged" && (item.resultRevision ?? Int64.max) > snapshot.cabinet_revision)),
                  item.command.payload["sprint_id"]?.text == sprint.id,
                  let timestamp = item.command.payload["occurred_at"]?.text,
                  let occurred = BusinessTime.parse(timestamp),
                  let amount = item.command.payload["amount_minor"]?.number else { return nil }
            return DayInput(day: BusinessTime.day(occurred), topups_minor: item.command.payload["kind"]?.text == "topup" ? amount : 0,
                            expenses_minor: item.command.payload["kind"]?.text == "expense" ? amount : 0)
        }
        let day = max(snapshot.business_date, BusinessTime.day(now.addingTimeInterval(state.serverOffset)))
        let base = try snapshot.project(asOf: day, additional: additions)
        if includingDraft, day <= sprint.end_date, ["expense", "topup"].contains(state.draftKind),
           let amount = Money.parse(state.draftAmount), amount > 0, !draftExceedsBank(state, projection: base) {
            additions.append(DayInput(day: day, topups_minor: state.draftKind == "topup" ? amount : 0,
                                      expenses_minor: state.draftKind == "expense" ? amount : 0))
            return try snapshot.project(asOf: day, additional: additions)
        }
        return base
    }
}
