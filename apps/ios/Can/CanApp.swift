import SwiftUI
import UIKit
import AuthenticationServices

extension Notification.Name {
    static let canBackgroundRefresh = Notification.Name("can.background.refresh")
    static let canPushRegistered = Notification.Name("can.push.registered")
}
final class AppDelegate: NSObject, UIApplicationDelegate {
    weak var store: AppStore?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        application.registerForRemoteNotifications()
        return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken token: Data) {
        do {
            try SecureStorage.save(token.map { String(format: "%02x", $0) }.joined(), account: "app-push")
            NotificationCenter.default.post(name: .canPushRegistered, object: nil)
        } catch { }
    }
    func application(_ application: UIApplication, didReceiveRemoteNotification userInfo: [AnyHashable: Any],
                     fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void) {
        Task { @MainActor [weak self] in
            guard let store = self?.store else { completionHandler(.noData); return }
            let before = store.state?.lastSyncAt
            await store.syncNow(force: true)
            completionHandler(before != store.state?.lastSyncAt ? .newData : .noData)
        }
    }
}

@main
struct CanApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = AppStore()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(store)
                .preferredColorScheme(.dark).tint(CanStyle.accent)
                .task { delegate.store = store; await store.start() }
                .onChange(of: scenePhase) { _, phase in store.setActive(phase == .active) }
                .onOpenURL { url in
                    guard url.scheme == "can", url.host == "expense" else { return }
                    store.focusExpense = true
                    Task { await store.syncNow(force: true) }
                }
                .onReceive(NotificationCenter.default.publisher(for: .canBackgroundRefresh)) { _ in Task { await store.syncNow(force: true) } }
                .onReceive(NotificationCenter.default.publisher(for: .canPushRegistered)) { _ in Task { await store.registerDevice(force: true) } }
                .onReceive(NotificationCenter.default.publisher(for: ASAuthorizationAppleIDProvider.credentialRevokedNotification)) { _ in
                    Task { await store.appleCredentialRevoked() }
                }
        }
    }
}
struct RootView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        NavigationStack {
            Group {
                if let issue = AppConfiguration.issue {
                    ContentUnavailableView("Can ещё не подключён", systemImage: "network.slash", description: Text(issue))
                } else if let failure = store.localRecoveryFailure {
                    VStack(spacing: 24) {
                        ContentUnavailableView("Разблокируйте iPhone", systemImage: "lock.shield", description: Text(failure))
                        Button("Повторить") { Task { await store.start() } }
                    }
                } else if store.deletionIntent != nil { DeletionProgressView() }
                else if store.deletionReceipt != nil || store.deletionConfirmedElsewhere { DeletionCompletionView() }
                else if store.user == nil || store.needsSignIn { WelcomeView() }
                else if store.user?.active_cabinet_id == nil { CabinetStartView() }
                else if store.snapshot == nil {
                    VStack(spacing: 24) {
                        ContentUnavailableView("Загружаем ваш бюджет", systemImage: "arrow.triangle.2.circlepath", description: Text("Для первого открытия кабинета нужна сеть."))
                        Button("Повторить") { Task { await store.syncNow(force: true) } }
                        NavigationLink("Настройки", destination: SettingsView())
                    }
                } else { BudgetHomeView() }
            }
            .background(CanStyle.background.ignoresSafeArea())
            .alert("Можно", isPresented: Binding(get: { store.message != nil }, set: { if !$0 { store.message = nil } })) {
                Button("Понятно", role: .cancel) { store.message = nil }
            } message: { Text(store.message ?? "") }
        }.id(store.navigationScope)
    }
}
struct WelcomeView: View {
    @EnvironmentObject private var store: AppStore
    @State private var privacy = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Spacer()
            Text("Можно").font(.system(size: 56, weight: .semibold)).tracking(-2)
            Text("Один бюджет.\nНа каждый день.").font(.title2).foregroundStyle(.secondary)
            Spacer()
            AppleButton().frame(height: 54)
            Button("Об использовании данных") { privacy = true }
                .font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 44)
            if store.challenge == nil {
                Button("Подготовить вход") { Task { await store.prepareApple() } }.font(.footnote)
            }
        }.padding(28).task { await store.prepareApple() }
            .sheet(isPresented: $privacy) { PrivacyView() }
    }
}
struct AppleButton: View {
    @EnvironmentObject private var store: AppStore
    var reauthenticate = false
    var body: some View {
        SignInWithAppleButton(.continue, onRequest: store.configureApple) { result in
            Task { await store.completeApple(result, reauthenticate: reauthenticate) }
        }
        .signInWithAppleButtonStyle(.white).clipShape(RoundedRectangle(cornerRadius: 14))
        .disabled(store.challenge == nil || store.isAuthorizingApple)
        .task {
            while !Task.isCancelled {
                if let challenge = store.challenge,
                   (BusinessTime.parse(challenge.expires_at) ?? .distantPast) < Date().addingTimeInterval(45) { await store.prepareApple() }
                try? await Task.sleep(for: .seconds(20))
            }
        }
    }
}

struct CabinetStartView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Начнём?").font(.system(size: 42, weight: .semibold)).tracking(-1)
            Text("Создайте кабинет или присоединитесь к общему.").foregroundStyle(.secondary)
            VStack(spacing: 0) {
                NavigationLink(destination: NewCabinetView()) { choice("Создать кабинет", note: "Начать одному или вместе") }
                Divider()
                NavigationLink(destination: JoinCabinetView()) { choice("Присоединиться", note: "По коду или QR") }
            }.padding(.top, 20)
            Spacer()
            Text("До 10 человек · одинаковые права").font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity)
        }.padding(24).canScreen().navigationTitle("")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { NavigationLink(destination: SettingsView()) { Image(systemName: "gearshape") }.accessibilityLabel("Настройки аккаунта") } }
    }
    private func choice(_ title: String, note: String) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 6) { Text(title).font(.headline); Text(note).font(.subheadline).foregroundStyle(.secondary) }
            Spacer()
            Image(systemName: "chevron.right").font(.footnote).foregroundStyle(.secondary)
        }.foregroundStyle(.primary).frame(minHeight: 92)
    }
}

struct NewCabinetView: View {
    @EnvironmentObject private var store: AppStore
    @State private var name = "Наш бюджет"
    @State private var busy = false
    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            Text("Как назовём?").font(.largeTitle.weight(.semibold)).tracking(-0.8)
            VStack(alignment: .leading, spacing: 12) {
                Text("Название").font(.footnote).foregroundStyle(.secondary)
                TextField("Наш бюджет", text: $name).font(.title2).onChange(of: name) { _, value in name = String(value.prefix(40)) }
                Divider()
            }
            Text("Пригласить людей можно позже.").font(.footnote).foregroundStyle(.secondary)
            Spacer()
            AsyncButton("Создать", busy: $busy) { try await store.createCabinet(name: name.trimmingCharacters(in: .whitespacesAndNewlines)) }
                .buttonStyle(CanPrimaryButtonStyle()).disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }.padding(24).canScreen().navigationTitle("Новый кабинет")
    }
}

struct JoinCabinetView: View {
    @EnvironmentObject private var store: AppStore
    @State private var code = ""
    @State private var preview: InvitationPreview?
    @State private var showPreview = false
    @State private var showScanner = false
    @State private var busy = false
    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            Text("Войдём вместе").font(.largeTitle.weight(.semibold)).tracking(-0.8)
            Text("Введите код, который вам отправили.").foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 12) {
                Text("Код приглашения").font(.footnote).foregroundStyle(.secondary)
                TextField("XXXX-XXXX-XX", text: $code).font(.title2.monospaced()).textInputAutocapitalization(.characters).autocorrectionDisabled()
                    .onChange(of: code) { _, _ in preview = nil }
                Divider()
            }
            AsyncButton("Продолжить", busy: $busy) {
                code = try InvitationCode.normalize(code)
                preview = try await store.previewInvitation(code)
                showPreview = true
            }.buttonStyle(CanPrimaryButtonStyle()).disabled(code.isEmpty)
            Button("Сканировать QR", systemImage: "qrcode.viewfinder") { showScanner = true }.buttonStyle(CanSecondaryButtonStyle())
            Spacer()
        }.padding(24).canScreen().navigationTitle("Приглашение")
            .navigationDestination(isPresented: $showPreview) {
                if let preview {
                    VStack(alignment: .leading, spacing: 20) {
                        Spacer()
                        Text("ВАС ПРИГЛАШАЮТ").font(.caption2).tracking(2).foregroundStyle(.secondary)
                        Text(preview.cabinet_name).font(.largeTitle.weight(.semibold))
                        Text("\(preview.member_count) из 10 участников.\nУ всех одинаковые права.").foregroundStyle(.secondary)
                        if preview.available_slots == 0 { Text("Мест пока нет. Попросите новое приглашение позже.").foregroundStyle(CanStyle.danger) }
                        Spacer()
                        AsyncButton("Присоединиться", busy: $busy) { try await store.join(code) }
                            .buttonStyle(CanPrimaryButtonStyle()).disabled(preview.available_slots == 0)
                    }.padding(24).canScreen().navigationTitle("Приглашение")
                }
            }
            .sheet(isPresented: $showScanner) {
                QRScannerSheet { scanned in
                    do { code = try InvitationCode.fromQR(scanned); showScanner = false }
                    catch { store.message = error.localizedDescription }
                }
            }
    }
}

enum BudgetEditorPresentation: String, Identifiable {
    case create, edit
    var id: String { rawValue }
}

struct BudgetHomeView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dynamicTypeSize) private var textSize
    @State private var sprintEditor: BudgetEditorPresentation?
    @State private var carry = false
    @State private var showSettings = false
    @State private var showHistory = false
    private var projection: Projection? { store.projection }
    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    header
                    if let p = projection, p.status == "active" {
                        hero(store.draftProjection ?? p)
                        Spacer(minLength: 24)
                        keypad(height: textSize.isAccessibilitySize ? 320 : min(290, max(248, geometry.size.height * 0.36)))
                    } else {
                        inactive
                    }
                }.padding(.horizontal, 24).padding(.bottom, 10)
                    .frame(minHeight: geometry.size.height, alignment: .top)
            }.scrollIndicators(.hidden)
        }.background(CanStyle.background.ignoresSafeArea())
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(isPresented: $showSettings) { SettingsView() }
            .navigationDestination(isPresented: $showHistory) { HistoryView() }
            .sheet(item: $sprintEditor) { mode in SprintEditor(editing: mode == .edit) }
            .sheet(isPresented: $carry) { CarryView() }
            .onChange(of: store.focusExpense) { _, focus in
                if focus { showHistory = false; showSettings = false; carry = false; sprintEditor = nil; store.focusExpense = false }
            }
    }
    private var header: some View {
        HStack(alignment: .center) {
            Button { showHistory = true } label: { Text("История").font(.subheadline).frame(minHeight: 44) }.foregroundStyle(.primary)
                .accessibilityIdentifier("budget.history")
            Spacer(minLength: 12)
            Button {
                if projection?.status == "active" { sprintEditor = .edit }
                else { showSettings = true }
            } label: {
                HStack(spacing: 8) {
                    if let p = projection, p.status == "active" {
                        Text("\(Money.format(p.bank_minor)) на \(BudgetDisplay.days(p.days_remaining))")
                            .font(.caption).monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
                    }
                    Image(systemName: "gearshape").font(.system(size: 18, weight: .regular))
                }.foregroundStyle(CanStyle.secondary).frame(minHeight: 44)
            }.accessibilityLabel(projection?.status == "active" ? "Изменить остаток и срок" : "Кабинет")
                .accessibilityIdentifier("budget.edit")
        }.frame(minHeight: 52)
    }
    private func hero(_ p: Projection) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(p.today_remaining_minor.map(Money.format) ?? "—")
                .font(.system(size: textSize.isAccessibilitySize ? 68 : 82, weight: .medium))
                .tracking(-3).monospacedDigit().lineLimit(1).minimumScaleFactor(0.33)
                .foregroundStyle((p.today_remaining_minor ?? 0) < 0 ? CanStyle.danger : .white)
                .accessibilityLabel("На сегодня \(p.today_remaining_minor.map(Money.format) ?? "неизвестно")")
                .accessibilityIdentifier("budget.today")
                .accessibilityValue(p.today_remaining_minor.map(Money.format) ?? "—")
            Text("На сегодня").font(.system(size: 18)).foregroundStyle(.secondary)
            Rectangle().fill(CanStyle.separator).frame(height: 0.5).padding(.top, 20).padding(.bottom, 12)
            if let amount = p.today_remaining_minor, amount < 0 {
                VStack(alignment: .leading, spacing: 4) {
                    if let next = BudgetDisplay.nextDay(bank: p.bank_minor, days: p.days_remaining) {
                        Text("Дальше \(Money.format(next)) в день").foregroundStyle(CanStyle.danger)
                        Text("Если сегодня больше не тратить").foregroundStyle(.secondary)
                    } else { Text("Последний день").foregroundStyle(CanStyle.danger) }
                }.font(.caption)
            }
            if p.carry_status == "pending" {
                Button { carry = true } label: {
                    HStack(spacing: 5) {
                        Text("Вчера осталось \(Money.format(p.carry_minor)) · выбрать")
                        Image(systemName: "chevron.right").font(.system(size: 9, weight: .semibold))
                    }.font(.caption).foregroundStyle(CanStyle.secondary).frame(minHeight: 44, alignment: .leading)
                }.buttonStyle(.plain).accessibilityIdentifier("budget.carry")
            }
            if let status = store.statusText, p.carry_status != "pending" || store.offline || store.pendingCount > 0 {
                NavigationLink(destination: QueueView()) {
                    Text(status).font(.caption).foregroundStyle(.secondary).frame(minHeight: 30)
                }
            }
        }.padding(.top, 17)
    }
    private var inactive: some View {
        VStack(alignment: .leading, spacing: 16) {
            Spacer(minLength: 55)
            if let p = projection, p.status == "completed" {
                Text("СРОК ЗАКОНЧИЛСЯ").font(.caption2.weight(.medium)).tracking(2).foregroundStyle(.secondary)
                Text(p.bank_minor >= 0 ? "Ура,\nполучилось!" : "Бюджет\nзавершён").font(.system(size: 48, weight: .semibold)).tracking(-1.5)
                Text(p.bank_minor >= 0 ? "И ещё осталось" : "Итоговый остаток").foregroundStyle(.secondary)
                Text(Money.format(p.bank_minor)).font(.system(size: 60, weight: .regular)).monospacedDigit().foregroundStyle(CanStyle.accent)
            } else {
                Text("Сколько\nи до когда?").font(.system(size: 48, weight: .semibold)).tracking(-1.5)
                Text("Задайте сумму и срок.\nМы посчитаем на сегодня.").foregroundStyle(.secondary)
            }
            if store.pendingCount > 0 { Text("Итог предварительный · есть записи на телефоне").font(.footnote).foregroundStyle(.secondary) }
            Spacer(minLength: 55)
            Button(projection?.status == "completed" ? "Новый бюджет" : "Задать бюджет") { sprintEditor = .create }
                .buttonStyle(CanPrimaryButtonStyle())
            Button("Кабинет и участники") { showSettings = true }.buttonStyle(CanSecondaryButtonStyle())
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func keypad(height: CGFloat) -> some View {
        VStack(spacing: 12) {
            HStack(spacing: 14) {
                Spacer(minLength: 0)
                Button { store.setDraft(kind: store.state?.draftKind == "expense" ? "topup" : "expense") } label: {
                    Image(systemName: store.state?.draftKind == "topup" ? "plus" : "minus")
                        .font(.system(size: 27, weight: .light)).foregroundStyle(CanStyle.accent).frame(width: 44, height: 54)
                        .contentShape(Rectangle())
                }.accessibilityLabel(store.state?.draftKind == "topup" ? "Пополнение. Переключить на расход" : "Расход. Переключить на пополнение")
                    .accessibilityIdentifier("expense.sign")
                Text(store.state?.draftAmount.isEmpty == false ? store.state!.draftAmount : "0")
                    .font(.system(size: 44, weight: .regular)).minimumScaleFactor(0.4).lineLimit(1).monospacedDigit()
                    .foregroundStyle(store.state?.draftAmount.isEmpty == false ? .white : CanStyle.secondary)
                    .accessibilityLabel("Введённая сумма").accessibilityIdentifier("expense.amount")
                    .accessibilityValue(store.state?.draftAmount.isEmpty == false ? store.state!.draftAmount : "0")
            }.frame(minHeight: 86, alignment: .trailing)
            GeometryReader { geometry in
                let gap: CGFloat = 6
                let rail: CGFloat = max(54, min(68, geometry.size.width * 0.18))
                let width = (geometry.size.width - rail - gap * 3) / 3
                let rowHeight = (height - gap * 3) / 4
                HStack(spacing: gap) {
                    VStack(spacing: gap) {
                        ForEach([["7", "8", "9"], ["4", "5", "6"], ["1", "2", "3"]], id: \.self) { row in
                            HStack(spacing: gap) { ForEach(row, id: \.self) { key in keyButton(key).frame(width: width, height: rowHeight) } }
                        }
                        HStack(spacing: gap) {
                            keyButton("0").frame(width: width * 2 + gap, height: rowHeight)
                            keyButton(",").frame(width: width, height: rowHeight)
                        }
                    }
                    VStack(spacing: gap) {
                        Button { tap("delete") } label: { Image(systemName: "delete.left").font(.system(size: 22)).frame(maxWidth: .infinity, maxHeight: .infinity).contentShape(Rectangle()) }
                            .foregroundStyle(.secondary).background(CanStyle.panel.opacity(0.7), in: RoundedRectangle(cornerRadius: 11))
                            .frame(height: rowHeight).accessibilityLabel("Удалить цифру").accessibilityIdentifier("keypad.delete")
                            .simultaneousGesture(TapGesture(count: 2).onEnded { store.setDraft("") })
                            .simultaneousGesture(LongPressGesture(minimumDuration: 0.6).onEnded { _ in store.setDraft("") })
                            .accessibilityAction(named: Text("Очистить сумму")) { store.setDraft("") }
                        ExpenseSubmitButton(enabled: (Money.parse(store.state?.draftAmount ?? "") ?? 0) > 0) {
                            do { try store.enqueueEntry(); UIImpactFeedbackGenerator(style: .light).impactOccurred() }
                            catch { store.message = error.localizedDescription }
                        }
                    }.frame(width: rail)
                }.buttonStyle(.plain)
            }.frame(height: height)
        }
    }
    private func keyButton(_ key: String) -> some View {
        Button { tap(key) } label: {
            Text(key).font(.system(size: 28, weight: .regular))
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: key == "0" ? .leading : .center)
                .padding(.leading, key == "0" ? 28 : 0).contentShape(Rectangle())
        }.foregroundStyle(.white).background(CanStyle.panel, in: RoundedRectangle(cornerRadius: 11))
            .accessibilityLabel(key == "," ? "Десятичный разделитель" : key).accessibilityIdentifier("keypad.\(key)")
    }
    private func tap(_ key: String) {
        var text = store.state?.draftAmount ?? ""
        if key == "delete" { if !text.isEmpty { text.removeLast() }; store.setDraft(text); return }
        if key == "," { if !text.contains(",") { store.setDraft(text.isEmpty ? "0," : text + ",") }; return }
        if text == "0" { text = "" }
        let proposed = text + key
        if Money.parse(proposed) != nil { store.setDraft(proposed) }
    }
}

struct AsyncButton: View {
    @EnvironmentObject private var store: AppStore
    let title: String
    @Binding var busy: Bool
    let action: () async throws -> Void
    init(_ title: String, busy: Binding<Bool>, action: @escaping () async throws -> Void) {
        self.title = title; _busy = busy; self.action = action
    }
    var body: some View {
        Button {
            busy = true
            Task { do { try await action() } catch { store.message = error.localizedDescription }; busy = false }
        } label: { HStack { Text(title); if busy { Spacer(); ProgressView() } } }.disabled(busy)
    }
}
