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
enum CanStyle {
    static let background = Color(red: 0.07, green: 0.08, blue: 0.09)
    static let panel = Color(red: 0.13, green: 0.15, blue: 0.16)
    static let accent = Color(red: 0.74, green: 0.91, blue: 0.43)
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
            .alert("Can", isPresented: Binding(get: { store.message != nil }, set: { if !$0 { store.message = nil } })) {
                Button("Понятно", role: .cancel) { store.message = nil }
            } message: { Text(store.message ?? "") }
        }.id(store.navigationScope)
    }
}
struct WelcomeView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            Spacer()
            Text("МОЖНО").font(.system(size: 17, weight: .bold, design: .rounded)).tracking(5).foregroundStyle(CanStyle.accent)
            Text("Один общий\nбюджет.").font(.system(size: 48, weight: .semibold, design: .rounded)).minimumScaleFactor(0.6)
            Text("Сколько можно сегодня?").font(.title2).foregroundStyle(.secondary)
            Spacer()
            AppleButton().frame(height: 54)
            Text("Вход связывает ваши устройства и общий бюджет. Суммы и имя хранятся на вашем сервере. Камера используется только для приглашения и не передаёт кадры.")
                .font(.footnote).foregroundStyle(.secondary)
            if store.challenge == nil { Button("Подготовить вход") { Task { await store.prepareApple() } } }
        }.padding(28).task { await store.prepareApple() }
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
    @State private var name = "Наш бюджет"
    @State private var code = ""
    @State private var preview: InvitationPreview?
    @State private var showScanner = false
    @State private var busy = false
    var body: some View {
        Form {
            Section {
                Text("Ваш общий ритм").font(.largeTitle.bold())
                Text("В кабинете может быть от одного до десяти равноправных участников.").foregroundStyle(.secondary)
            }
            Section("Создать кабинет") {
                TextField("Название", text: $name).onChange(of: name) { _, value in name = String(value.prefix(40)) }
                AsyncButton("Создать", busy: $busy) { try await store.createCabinet(name: name) }.disabled(name.isEmpty)
            }
            Section("Присоединиться") {
                TextField("Код приглашения", text: $code).textInputAutocapitalization(.characters).autocorrectionDisabled()
                    .onChange(of: code) { _, _ in preview = nil }
                Button("Сканировать QR", systemImage: "qrcode.viewfinder") { showScanner = true }
                AsyncButton("Проверить код", busy: $busy) {
                    let normalized = try InvitationCode.normalize(code)
                    code = normalized; preview = try await store.previewInvitation(normalized)
                }
                if let preview {
                    Text(preview.cabinet_name).font(.headline)
                    Text("\(preview.member_count) из 10 участников. У всех одинаковые права.")
                    Text("Приглашение до \(preview.expires_at)").font(.caption).foregroundStyle(.secondary)
                    AsyncButton("Присоединиться к кабинету", busy: $busy) { try await store.join(code) }
                        .disabled(preview.available_slots == 0)
                }
            }
            Section {
                NavigationLink("Синхронизация", destination: QueueView())
                NavigationLink("Настройки аккаунта", destination: SettingsView())
            }
        }.navigationTitle("Can")
            .sheet(isPresented: $showScanner) {
                QRScannerSheet { scanned in
                    do { code = try InvitationCode.fromQR(scanned); showScanner = false }
                    catch { store.message = error.localizedDescription }
                }
            }
    }
}

struct BudgetHomeView: View {
    @EnvironmentObject private var store: AppStore
    @State private var sprintEditor = false
    @State private var carry = false
    @State private var showSettings = false
    @State private var showHistory = false
    @Environment(\.dynamicTypeSize) private var textSize
    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: 22) {
                    HStack {
                        Button { showHistory = true } label: { Image(systemName: "clock.arrow.circlepath").font(.title2) }.accessibilityLabel("История")
                        Spacer()
                        VStack(spacing: 3) {
                            Text(store.snapshot?.cabinet.name ?? "Can").font(.headline)
                            if let p = store.projection {
                                Text("\(Money.format(p.bank_minor)) на \(p.days_remaining) дн.").font(.subheadline).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Button { showSettings = true } label: { Image(systemName: "slider.horizontal.3").font(.title2) }.accessibilityLabel("Настройки")
                    }
                    if let p = store.projection, p.status == "active" {
                        VStack(spacing: 8) {
                            Text("На сегодня").font(.title3).foregroundStyle(.secondary)
                            Text(p.today_remaining_minor.map(Money.format) ?? "—")
                                .font(.system(size: textSize.isAccessibilitySize ? 60 : 78, weight: .medium, design: .rounded))
                                .minimumScaleFactor(0.35).lineLimit(1).monospacedDigit()
                                .accessibilityLabel("На сегодня \(p.today_remaining_minor.map(Money.format) ?? "неизвестно")")
                            if let status = store.statusText {
                                NavigationLink(destination: QueueView()) { Text(status).font(.caption).multilineTextAlignment(.center) }
                            }
                        }.frame(maxWidth: .infinity).padding(.vertical, 22)
                        if p.carry_status == "pending" {
                            Button { carry = true } label: {
                                HStack { Text("Вчера осталось \(Money.format(p.carry_minor))"); Spacer(); Image(systemName: "chevron.right") }
                                    .font(.subheadline).padding(15).background(CanStyle.panel, in: RoundedRectangle(cornerRadius: 16))
                            }
                        }
                        keypad
                    } else {
                        Spacer(minLength: 40)
                        Image(systemName: store.projection == nil ? "sun.max" : "checkmark.circle").font(.system(size: 55)).foregroundStyle(CanStyle.accent)
                        Text(store.projection == nil ? "Начнём бюджет?" : ((store.projection?.bank_minor ?? 0) >= 0 ? "У вас получилось!" : "Спринт завершён"))
                            .font(.largeTitle.bold()).multilineTextAlignment(.center)
                        if store.pendingCount > 0 { Text("Итог предварительный · есть записи на телефоне").foregroundStyle(.secondary) }
                        Button(store.projection == nil ? "Создать спринт" : "Начать новый спринт") { sprintEditor = true }
                            .buttonStyle(.borderedProminent).controlSize(.large)
                        Text("Сумму нового спринта вы задаёте сами.").font(.footnote).foregroundStyle(.secondary)
                        Spacer(minLength: 40)
                    }
                }.padding(22).frame(minHeight: geometry.size.height, alignment: .top)
            }.scrollIndicators(.hidden)
        }
        .toolbar(.hidden, for: .navigationBar)
        .navigationDestination(isPresented: $showSettings) { SettingsView() }
        .navigationDestination(isPresented: $showHistory) { HistoryView() }
        .sheet(isPresented: $sprintEditor) { SprintEditor(editing: false) }
        .sheet(isPresented: $carry) { CarryView() }
        .onChange(of: store.focusExpense) { _, focus in
            if focus { showHistory = false; showSettings = false; carry = false; sprintEditor = false; store.focusExpense = false }
        }
    }
    private var keypad: some View {
        VStack(spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Button { store.setDraft(kind: store.state?.draftKind == "expense" ? "topup" : "expense") } label: {
                    Text(store.state?.draftKind == "topup" ? "+" : "−").font(.system(size: 38, weight: .medium))
                        .frame(width: 54, height: 58).background(CanStyle.panel, in: RoundedRectangle(cornerRadius: 15))
                }.accessibilityLabel(store.state?.draftKind == "topup" ? "Пополнение. Переключить на расход" : "Расход. Переключить на пополнение")
                Text(store.state?.draftAmount.isEmpty == false ? store.state!.draftAmount : "0")
                    .font(.system(size: 42, weight: .regular, design: .rounded)).minimumScaleFactor(0.4).lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .trailing).monospacedDigit().accessibilityLabel("Введённая сумма")
            }.padding(.bottom, 8)
            ForEach([["7", "8", "9"], ["4", "5", "6"], ["1", "2", "3"], [",", "0", "⌫"]], id: \.self) { row in
                HStack(spacing: 10) {
                    ForEach(row, id: \.self) { key in
                        Button { tap(key) } label: {
                            Text(key).font(.system(size: 29, weight: .medium, design: .rounded))
                                .frame(maxWidth: .infinity, minHeight: 57).contentShape(Rectangle())
                        }.buttonStyle(.plain).background(CanStyle.panel.opacity(0.55), in: RoundedRectangle(cornerRadius: 16))
                            .accessibilityLabel(key == "⌫" ? "Удалить цифру" : key == "," ? "Десятичный разделитель" : key)
                            .simultaneousGesture(LongPressGesture(minimumDuration: 0.5).onEnded { _ in if key == "⌫" { store.setDraft("") } })
                    }
                }
            }
            Button {
                do { try store.enqueueEntry(); UIImpactFeedbackGenerator(style: .light).impactOccurred() }
                catch { store.message = error.localizedDescription }
            } label: {
                Label(store.state?.draftKind == "topup" ? "Пополнить" : "Записать расход", systemImage: "checkmark")
                    .font(.headline).frame(maxWidth: .infinity, minHeight: 57)
            }.buttonStyle(.borderedProminent).foregroundStyle(.black).clipShape(RoundedRectangle(cornerRadius: 16))
                .disabled((Money.parse(store.state?.draftAmount ?? "") ?? 0) == 0)
        }
    }
    private func tap(_ key: String) {
        var text = store.state?.draftAmount ?? ""
        if key == "⌫" { if !text.isEmpty { text.removeLast() }; store.setDraft(text); return }
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
