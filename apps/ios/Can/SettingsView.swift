import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var busy = false
    @State private var editSprint = false
    @State private var showInvitation = false
    @State private var renaming = false
    @State private var leaving = false
    @State private var privacy = false
    var body: some View {
        List {
            if let snapshot = store.snapshot {
                Section {
                    VStack(alignment: .leading, spacing: 7) {
                        Text(snapshot.cabinet.name).font(.largeTitle.weight(.semibold)).tracking(-0.7)
                        Text("\(snapshot.cabinet.members.count) из 10 участников · равные права")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }.padding(.vertical, 16)
                }.listRowBackground(Color.clear).listRowSeparator(.hidden)
                Section {
                    NavigationLink("Участники", destination: MembersView())
                    if snapshot.cabinet.members.count < 10 {
                        AsyncButton("Пригласить", busy: $busy) {
                            _ = try await store.onlineCommand("create_invitation", payload: [:])
                            showInvitation = store.invitation != nil
                        }
                    }
                    Button("Название") { renaming = true }.foregroundStyle(.primary)
                }
                Section("Бюджет") {
                    Button("Новый бюджет") { editSprint = true }.foregroundStyle(.primary)
                    NavigationLink("Прошлые бюджеты", destination: ArchiveView())
                    NavigationLink(destination: QueueView()) {
                        HStack { Text("Синхронизация"); Spacer(); Text(store.offline ? "Нет связи" : store.totalPendingCount > 0 ? "\(store.totalPendingCount) в очереди" : "").font(.caption).foregroundStyle(.secondary) }
                    }
                }
            }
            Section("Личное") {
                NavigationLink("Профиль", destination: ProfileView())
                if !store.previousLocalAccounts.isEmpty { NavigationLink("Прежние данные на этом iPhone", destination: PreviousLocalDataView()) }
                Button("Об использовании данных") { privacy = true }.foregroundStyle(.primary)
                LabeledContent("Версия", value: "\(AppConfiguration.value("CFBundleShortVersionString")) (\(AppConfiguration.value("CFBundleVersion")))").foregroundStyle(.secondary).font(.footnote)
            }
            if store.user?.active_cabinet_id != nil {
                Section {
                    Button("Выйти из кабинета", role: .destructive) { leaving = true }
                } footer: { Text("Ваши операции останутся в общей истории.") }
            }
        }.listStyle(.plain).canScreen().navigationTitle("Кабинет")
            .sheet(isPresented: $editSprint) { SprintEditor(restarting: store.projection?.status == "active") }
            .sheet(isPresented: $showInvitation) { if let invitation = store.invitation { InvitationView(invitation: invitation) } }
            .sheet(isPresented: $privacy) { PrivacyView() }
            .sheet(isPresented: $renaming) {
                NameEditor(title: "Название кабинета", initial: store.snapshot?.cabinet.name ?? "") { name in
                    guard let snapshot = store.snapshot else { throw APIError.local("Откройте кабинет ещё раз.") }
                    _ = try await store.onlineCommand("rename_cabinet", payload: ["name": .string(name), "expected_cabinet_revision": .integer(snapshot.cabinet_revision)])
                }
            }
            .confirmationDialog("Выйти из кабинета?", isPresented: $leaving, titleVisibility: .visible) {
                Button("Выйти из кабинета", role: .destructive) {
                    Task {
                        do {
                            try await store.requireSettled()
                            guard let snapshot = store.snapshot else { return }
                            _ = try await store.onlineCommand("leave_cabinet", payload: ["expected_cabinet_revision": .integer(snapshot.cabinet_revision)])
                        } catch { store.message = error.localizedDescription }
                    }
                }
            } message: { Text("История и банк сохранятся. Если вы последний участник, кабинет будет архивирован без обычного доступа. Для возвращения понадобится новое приглашение. Сначала нужно отправить ожидающие записи.") }
    }
}

struct MembersView: View {
    @EnvironmentObject private var store: AppStore
    @State private var busy = false
    @State private var showingInvitation = false
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Наши люди").font(.largeTitle.weight(.semibold)).tracking(-0.7)
                    Text("\(store.snapshot?.cabinet.members.count ?? 0) из 10").foregroundStyle(.secondary)
                }.padding(.vertical, 12)
            }.listRowBackground(Color.clear).listRowSeparator(.hidden)
            Section {
                ForEach(store.snapshot?.cabinet.members ?? []) { member in
                    HStack {
                        Text(member.display_name + (member.user_id == store.user?.id ? " · вы" : ""))
                        Spacer()
                        Text("Участник").font(.caption).foregroundStyle(.secondary)
                    }.padding(.vertical, 7)
                }
            } footer: { Text("Все управляют одним бюджетом. У создателя те же права.") }
        }.listStyle(.plain).canScreen().navigationTitle("Участники")
            .safeAreaInset(edge: .bottom) {
                if (store.snapshot?.cabinet.members.count ?? 10) < 10 {
                    AsyncButton("Пригласить", busy: $busy) {
                        _ = try await store.onlineCommand("create_invitation", payload: [:])
                        showingInvitation = store.invitation != nil
                    }.buttonStyle(CanPrimaryButtonStyle()).padding(24).background(CanStyle.background)
                }
            }
            .sheet(isPresented: $showingInvitation) { if let invitation = store.invitation { InvitationView(invitation: invitation) } }
    }
}

struct ProfileView: View {
    @EnvironmentObject private var store: AppStore
    @State private var displayName = ""
    @State private var busy = false
    @State private var loggingOut = false
    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text(store.user?.display_name ?? "Профиль").font(.largeTitle.weight(.semibold)).tracking(-0.7)
                    Text("Вход через Apple").font(.subheadline).foregroundStyle(.secondary)
                }.padding(.vertical, 12)
            }.listRowBackground(Color.clear)
            Section("Имя") {
                TextField("Имя", text: $displayName).onChange(of: displayName) { _, value in displayName = String(value.prefix(40)) }
                AsyncButton("Сохранить", busy: $busy) { try await store.renameProfile(displayName) }
                    .disabled(displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || displayName == store.user?.display_name)
            }
            Section("Доступ") {
                Button("Выйти из аккаунта", role: .destructive) { loggingOut = true }
                NavigationLink("Удалить аккаунт", destination: DeletionView())
            }
        }.canScreen().navigationTitle("Профиль").onAppear { displayName = store.user?.display_name ?? "" }
            .confirmationDialog("Выйти из аккаунта?", isPresented: $loggingOut, titleVisibility: .visible) {
                Button("Выйти и сохранить очередь на телефоне", role: .destructive) {
                    Task { do { try await store.logout() } catch { store.message = error.localizedDescription } }
                }
            } message: { Text("Неотправленные записи сохранятся только для этого аккаунта на этом iPhone. Отправка продолжится после входа тем же Apple Account. Для отзыва сессии нужна сеть.") }
    }
}

struct NameEditor: View {
    @Environment(\.dismiss) private var dismiss
    let title: String
    let initial: String
    let save: (String) async throws -> Void
    @State private var value = ""
    @State private var busy = false
    @State private var failure: String?
    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 24) {
                TextField(title, text: $value).font(.title2).textFieldStyle(.plain)
                    .onChange(of: value) { _, text in value = String(text.prefix(40)) }
                if let failure { Text(failure).font(.footnote).foregroundStyle(CanStyle.danger) }
                Button("Сохранить") {
                    busy = true
                    Task {
                        do { try await save(value.trimmingCharacters(in: .whitespacesAndNewlines)); dismiss() }
                        catch { failure = error.localizedDescription }
                        busy = false
                    }
                }.buttonStyle(CanPrimaryButtonStyle()).disabled(busy || value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Spacer(minLength: 0)
            }.padding(24).canScreen().navigationTitle(title)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() } } }
                .onAppear { value = initial }
        }.presentationDetents([.height(280), .medium]).presentationDragIndicator(.visible)
    }
}

struct QueueView: View {
    @EnvironmentObject private var store: AppStore
    @State private var selected: PendingCommand?
    @State private var discarding: PendingCommand?
    @State private var confirmingRestore = false
    @State private var repeatingRestored: PendingCommand?
    var body: some View {
        List {
            Section {
                Text(store.statusText ?? "Последняя синхронизация выполнена")
                if let date = store.state?.lastSyncAt { LabeledContent("Синхронизировано") { Text(date, style: .relative) } }
                Button("Синхронизировать сейчас") { Task { await store.syncNow(force: true) } }.disabled(store.isSyncing)
            }
            Section("Записи на телефоне") {
                ForEach(store.state?.pending ?? []) { item in
                    VStack(alignment: .leading, spacing: 8) {
                        Text("\(item.command.payload["kind"]?.text == "expense" ? "−" : "+")\(Money.format(item.command.payload["amount_minor"]?.number ?? 0))").font(.headline)
                        Text(item.command.payload["occurred_at"]?.text ?? "").font(.caption).foregroundStyle(.secondary)
                        Text(stateName(item.state)).font(.caption)
                        if item.membershipID != store.membershipID {
                            Text("Относится к прежнему членству. В новый кабинет не переносится.").foregroundStyle(.orange)
                            Button("Проверить прежнюю запись") { Task { await store.checkQuarantined(item) } }
                        }
                        if let error = item.error { Text(error.localizedDescription).foregroundStyle(.orange) }
                        if item.state == "restore_review" {
                            Button("Повторить запись после восстановления") { repeatingRestored = item }
                        }
                        if item.state == "needs_attention", item.canRecover { Button("Разобрать запись") { selected = item } }
                        if item.state == "needs_attention" { Button("Убрать отклонённый черновик", role: .destructive) { discarding = item } }
                        if item.state == "queued" {
                            Button("Удалить неотправленный черновик", role: .destructive) {
                                do { try store.discardUnsent(item) } catch { store.message = error.localizedDescription }
                            }
                        }
                    }.padding(.vertical, 6)
                }
                if store.totalPendingCount == 0 { Text("Все записи подтверждены.").foregroundStyle(.secondary) }
            }
            if let intents = store.state?.onlineIntents, !intents.isEmpty {
                Section("Проверить исход действий") {
                    Text("Ответ мог потеряться. Повторяем сохранённую команду с тем же идентификатором.").font(.footnote).foregroundStyle(.secondary)
                    ForEach(intents.keys.sorted(), id: \.self) { key in
                        Button("Проверить: \(intentName(intents[key]!.command.type))") { Task { await store.retryIntent(key) } }
                    }
                }
            }
            Section("Восстановление сервера") {
                Button("Обновить бюджет после восстановления сервера") { confirmingRestore = true }
                Text("Используйте, только если владелец восстановил сервер из резервной копии. Записи на телефоне сохранятся.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }.canScreen().navigationTitle("Синхронизация").toolbar(.visible, for: .navigationBar)
            .sheet(item: $selected) { item in RecoveryView(item: item) }
            .confirmationDialog("Убрать отклонённый черновик?", isPresented: Binding(get: { discarding != nil }, set: { if !$0 { discarding = nil } }), titleVisibility: .visible) {
                Button("Убрать черновик", role: .destructive) {
                    guard let item = discarding else { return }
                    do { try store.dismissRejected(item); discarding = nil } catch { store.message = error.localizedDescription }
                }
            } message: { Text("Сервер подтвердил, что эта запись не применена к бюджету. Удаляется только отклонённый черновик на телефоне.") }
            .confirmationDialog("Сервер восстановлен из резервной копии?", isPresented: $confirmingRestore, titleVisibility: .visible) {
                Button("Заново загрузить подтверждённый бюджет") { Task { await store.refreshAfterServerRestore() } }
            } message: { Text("Подтверждённые суммы будут заново загружены с сервера. Очередь и неизвестные результаты действий останутся на телефоне; идентификаторы запросов сохранятся.") }
            .confirmationDialog("Повторить запись с прежним идентификатором?", isPresented: Binding(get: { repeatingRestored != nil }, set: { if !$0 { repeatingRestored = nil } }), titleVisibility: .visible) {
                Button("Повторить исходную запись") {
                    guard let item = repeatingRestored else { return }
                    do { try store.retryAfterServerRestore(item); repeatingRestored = nil }
                    catch { store.message = error.localizedDescription }
                }
            } message: { Text("Раньше сервер принимал эту запись, но после восстановления её квитанция отсутствует. Проверьте историю. Повтор использует прежний идентификатор и те же данные.") }
    }
    private func stateName(_ state: String) -> String {
        ["queued": "Сохранено на телефоне", "sending": "Проверяем результат отправки", "unknown": "Ожидает повторной отправки",
         "acknowledged": "Принято; ожидаем согласованный снимок", "needs_attention": "Требуется ваше действие",
         "restore_review": "После восстановления сервера квитанция не найдена"][state] ?? state
    }
    private func intentName(_ type: String) -> String {
        ["update_sprint": "остаток и срок", "choose_carry": "перенос", "create_sprint": "новый спринт",
         "leave_cabinet": "выход", "correct_entry": "исправление", "void_entry": "отмена",
         "create_invitation": "приглашение", "revoke_invitation": "отзыв приглашения", "rename_cabinet": "название"][type] ?? "действие"
    }
}

struct RecoveryView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let item: PendingCommand
    @State private var correctClock = false
    @State private var date = Date()
    @State private var confirming = false
    private var clockFailure: Bool {
        ["clock_skew", "entry_outside_sprint"].contains(item.error?.code ?? "")
    }
    var body: some View {
        NavigationStack {
            Form {
                Text(item.error?.localizedDescription ?? "Требуется подтверждение.")
                Text("Запись будет учтена в первоначальном спринте. На новый спринт она не повлияет.").font(.footnote).foregroundStyle(.secondary)
                if clockFailure {
                    Toggle("Исправить ошибку часов", isOn: $correctClock)
                    if correctClock { DatePicker("Время покупки", selection: $date, in: ...Date()) }
                }
                Button("Учесть в исходном спринте") { confirming = true }
                    .disabled(!item.canRecover || item.membershipID != store.membershipID || item.cabinetID != store.user?.active_cabinet_id)
            }.navigationTitle("Разобрать запись")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() } } }
                .onAppear { date = item.command.payload["occurred_at"]?.text.flatMap(BusinessTime.parse) ?? Date() }
                .confirmationDialog("Подтвердить запись в исходный спринт?", isPresented: $confirming, titleVisibility: .visible) {
                    Button("Подтвердить") {
                        do { try store.recover(item, correctedDate: correctClock ? date : nil); dismiss() }
                        catch { store.message = error.localizedDescription }
                    }
                }
        }
    }
}

struct PrivacyView: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List {
                Section("Что хранится") { Text("Имя, суммы и даты хранятся на вашем сервере для общего бюджета. На iPhone хранится защищённая локальная очередь и снимок бюджета. Это не сквозное шифрование.") }
                Section("Доступ") { Text("Бюджет доступен активным участникам кабинета. Вход выполняется только через Apple. Виджет получает отдельный доступ только к краткому остатку.") }
                Section("Камера") { Text("Камера включается только для сканирования приглашения. Кадры не сохраняются и не отправляются на сервер.") }
                Section("Выход и удаление") { Text("Выход из кабинета сохраняет общий журнал; выход из аккаунта отзывает сессию. При удалении аккаунта имя и связь с ним стираются. Операции, суммы и даты остаются в общем журнале без имени; баланс других участников не меняется.") }
            }.canScreen().navigationTitle("Об использовании данных").toolbar { Button("Закрыть") { dismiss() } }
        }
    }
}

struct DeletionView: View {
    @EnvironmentObject private var store: AppStore
    @State private var confirmation = false
    @State private var busy = false
    var body: some View {
        Form {
            Section("Что будет удалено") {
                Text("Ваш аккаунт, имя, связь с Apple Account и доступ к кабинетам. После принятия запроса вы выйдете на всех устройствах.")
                Text("С этого iPhone будут удалены данные этого аккаунта, ключи доступа и локальная очередь. Данные других аккаунтов на телефоне не затрагиваются.")
            }
            Section("Что останется в общем бюджете") {
                Text("Операции, суммы и даты сохранятся без вашего имени и связи с аккаунтом. Общий банк и расчёты других участников не изменятся.")
                Text("Изменения операций тоже сохранятся в журнале без вашего имени. Удаление нельзя отменить.")
            }
            if store.totalPendingCount > 0 || store.state?.onlineIntents.isEmpty == false || store.state?.cabinetIntent != nil {
                Section("На телефоне есть ожидающие действия") {
                    Text("Записей: \(store.totalPendingCount). Других ожидающих действий: \((store.state?.onlineIntents.count ?? 0) + (store.state?.cabinetIntent == nil ? 0 : 1)).")
                    Text("После принятия удаления неотправленные записи и неизвестные результаты будут удалены с этого iPhone. Чтобы сохранить покупки в общем бюджете, сначала проверьте синхронизацию.")
                        .foregroundStyle(.orange)
                    NavigationLink("Проверить синхронизацию", destination: QueueView())
                }
            }
            Section("Подтвердите личность") {
                AppleButton(reauthenticate: true).frame(height: 50)
                if store.deletionAuthorizationReady { Label("Личность подтверждена", systemImage: "checkmark.circle") }
                Button("Обновить подтверждение Apple") { Task { await store.prepareDeletionConfirmation() } }
            }
            Button("Удалить мой аккаунт", role: .destructive) { confirmation = true }
                .disabled(busy || !store.deletionAuthorizationReady || !store.deletionReauthenticationIsFresh)
        }.navigationTitle("Удаление аккаунта")
            .task { await store.prepareDeletionConfirmation() }
            .confirmationDialog("Удалить аккаунт без возможности отмены?", isPresented: $confirmation, titleVisibility: .visible) {
                Button("Удалить аккаунт и данные на этом iPhone", role: .destructive) {
                    busy = true
                    Task { do { try await store.deleteAccount() } catch { store.message = error.localizedDescription }; busy = false }
                }
            } message: {
                Text("Общие операции, суммы и даты останутся без вашего имени. Локальная очередь этого аккаунта будет удалена после принятия запроса. Выход из аккаунта и выход из кабинета — отдельные действия.")
            }
    }
}

struct DeletionProgressView: View {
    @EnvironmentObject private var store: AppStore
    @State private var confirmation = false
    @State private var localConfirmation = false
    var body: some View {
        Form {
            Section {
                if store.deletionIntent?.needsCleanup == true {
                    Text("Удаление принято. Завершаем очистку этого iPhone.").font(.headline)
                    Text("Запрос сохранён. Если приложение закроется, очистка продолжится при следующем запуске. Разблокируйте телефон и повторите проверку, если хранилище недоступно.")
                } else {
                    Text("Результат удаления пока не подтверждён").font(.headline)
                    Text("Ответ мог потеряться. Данные на этом iPhone пока сохранены. Проверка повторяет ваш исходный запрос; новый запрос удаления не создаётся.")
                }
                if store.deletionBusy { ProgressView() }
                Button(store.deletionIntent?.needsCleanup == true ? "Завершить очистку" : "Проверить результат") {
                    Task { await store.resumeDeletion() }
                }.disabled(store.deletionBusy)
            }
            if store.deletionHasReplacementAccount, let oldID = store.deletionIntent?.userID {
                Section("Apple подтвердил новый аккаунт") {
                    Text("Для того же Apple Account сервер вернул новый аккаунт. Это может быть связано с удалением или восстановлением сервера; само по себе это не подтверждает удаление прежнего аккаунта.")
                    Text("Прежняя локальная очередь скрыта и не отправляется в новый бюджет. Вы можете сохранить её или удалить только с этого iPhone. Запрос удаления нового аккаунта не отправляется.")
                    if let count = store.previousLocalAccounts.first(where: { $0.id == oldID })?.pendingCount {
                        Text("Ожидающих записей и действий в прежних данных: \(count).")
                    }
                    Button("Сохранить прежние данные и продолжить") {
                        do { try store.continueWithReplacementAccount() } catch { store.message = error.localizedDescription }
                    }.disabled(store.deletionBusy)
                    if !store.previousCleanupReady {
                        AppleButton(reauthenticate: true).frame(height: 50)
                        Button("Подготовить подтверждение Apple") { Task { await store.prepareApple() } }
                    }
                    Button("Удалить прежние данные с этого iPhone", role: .destructive) { localConfirmation = true }
                        .disabled(store.deletionBusy || !store.previousCleanupReady)
                }
            } else if store.deletionNeedsReauthentication, store.deletionIntent?.needsCleanup == false {
                Section("Подтвердите тот же Apple Account") {
                    Text("Если сервер ещё не принял запрос, для повтора нужно свежее подтверждение Apple. Отсутствие доступа само по себе не означает, что аккаунт удалён.")
                    AppleButton(reauthenticate: true).frame(height: 50).disabled(store.deletionBusy)
                    Button("Подготовить подтверждение Apple") { Task { await store.prepareApple() } }
                    if store.deletionAuthorizationReady {
                        Button("Подтвердить повтор удаления", role: .destructive) { confirmation = true }
                            .disabled(store.deletionBusy || !store.deletionReauthenticationIsFresh)
                    }
                }
            }
        }.navigationTitle("Удаление аккаунта")
            .navigationBarBackButtonHidden()
            .task { if store.deletionNeedsReauthentication { await store.prepareApple() } }
            .confirmationDialog("Повторить подтверждённое удаление?", isPresented: $confirmation, titleVisibility: .visible) {
                Button("Подтвердить удаление", role: .destructive) {
                    Task { do { try await store.retryDeletionWithFreshAuthorization() } catch { store.message = error.localizedDescription } }
                }
            } message: { Text("Сначала проверим исходный запрос. Общий журнал сохранится без имени, а локальные данные этого аккаунта будут удалены после принятия.") }
            .confirmationDialog("Удалить только прежние локальные данные?", isPresented: $localConfirmation, titleVisibility: .visible) {
                Button("Удалить прежнюю очередь и данные", role: .destructive) {
                    guard let id = store.deletionIntent?.userID else { return }
                    do { try store.removePreviousLocalAccount(id) } catch { store.message = error.localizedDescription }
                }
            } message: { Text("Прежние записи и сохранённый запрос будут потеряны на этом iPhone. Новый аккаунт, его ключи, виджет и серверные данные сохранятся. Это не подтверждение удаления прежнего аккаунта на сервере.") }
    }
}

struct PreviousLocalDataView: View {
    @EnvironmentObject private var store: AppStore
    @State private var selected: PreviousLocalAccount?
    var body: some View {
        List {
            Section {
                Text("После входа тем же Apple Account сервер вернул другой аккаунт. Прежние данные сохранены отдельно: они скрыты из бюджета и не отправляются от нового аккаунта.")
                Text("Причина смены может быть связана с восстановлением сервера. Разница аккаунтов сама по себе не подтверждает удаление. Очистка ниже действует только на этот iPhone.")
            }
            ForEach(Array(store.previousLocalAccounts.enumerated()), id: \.element.id) { index, item in
                Section("Прежний набор \(index + 1)") {
                    if let count = item.pendingCount { Text("Ожидающих записей и действий: \(count)") }
                    else { Text("Количество записей прочитать не удалось. Прежние файлы сохранены.") }
                    if item.hasDeletionRequest {
                        Button("Проверить прежний запрос удаления") { Task { await store.checkPreviousDeletion(item.id) } }
                            .disabled(store.deletionBusy)
                    }
                    Button("Удалить эти локальные данные", role: .destructive) { selected = item }
                        .disabled(store.deletionBusy || !store.previousCleanupReady)
                }
            }
            if store.previousLocalAccounts.isEmpty { Text("Прежних локальных данных нет.") }
            if !store.previousCleanupReady, !store.previousLocalAccounts.isEmpty {
                Section("Подтвердите текущий Apple Account") {
                    AppleButton(reauthenticate: true).frame(height: 50)
                    Button("Подготовить подтверждение Apple") { Task { await store.prepareApple() } }
                }
            }
        }.navigationTitle("Прежние данные")
            .task { if !store.previousCleanupReady { await store.prepareApple() } }
            .confirmationDialog("Удалить прежние данные только с этого iPhone?", isPresented: Binding(get: { selected != nil }, set: { if !$0 { selected = nil } }), titleVisibility: .visible) {
                Button("Удалить прежний набор", role: .destructive) {
                    guard let item = selected else { return }
                    do { try store.removePreviousLocalAccount(item.id); selected = nil }
                    catch { store.message = error.localizedDescription }
                }
            } message: { Text("Все неотправленные записи этого прежнего набора и его сохранённые запросы будут удалены без восстановления. Текущий аккаунт, его очередь, ключи и виджет сохранятся. Удаление аккаунта на сервере этим действием не выполняется.") }
    }
}

struct DeletionCompletionView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            Image(systemName: "checkmark.shield").font(.system(size: 48)).foregroundStyle(CanStyle.accent)
            Text(store.user == nil ? "Удаление аккаунта принято" : "Удаление прежнего аккаунта принято").font(.largeTitle.bold())
            Text("Данные этого аккаунта и ключи доступа удалены с этого iPhone. Общие операции, суммы и даты сохраняются без имени и связи с аккаунтом.")
            if let receipt = store.deletionReceipt, let deadline = BusinessTime.parse(receipt.primary_deletion_deadline) {
                Text("Сервер завершит основное удаление до \(deadline.formatted(date: .long, time: .shortened)).")
                    .foregroundStyle(.secondary)
            } else {
                Text("Удаление уже выполняется на сервере. Новый вход станет доступен после завершения и создаст новый пустой аккаунт.").foregroundStyle(.secondary)
            }
            Text("Принятие запроса не означает, что сервер уже завершил очистку.")
                .font(.footnote).foregroundStyle(.secondary)
            Button("Продолжить") { store.dismissDeletionReceipt() }.buttonStyle(.borderedProminent)
        }.padding(28).navigationBarBackButtonHidden()
    }
}
