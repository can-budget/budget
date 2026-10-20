import SwiftUI

struct SprintEditor: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let restarting: Bool
    @State private var basis: Snapshot?
    @State private var amount = ""
    @State private var end = Date()
    @State private var busy = false
    @State private var failure: String?
    @State private var amountDirty = false
    @State private var endDirty = false
    private var currentDay: String { basis?.business_date ?? BusinessTime.day(Date()) }
    private var dateRange: ClosedRange<Date> {
        let start = BusinessTime.date(currentDay)
        let max = BusinessTime.calendar.date(byAdding: .day, value: 365, to: start)!
        return min(BusinessTime.date(currentDay), max)...max
    }
    private var preview: Projection? {
        guard let bank = Money.parse(amount) else { return nil }
        return try? BudgetCalculation.project(BudgetInput(initial_minor: bank, start_date: currentDay,
            end_date: BusinessTime.day(end), as_of_date: currentDay, days: [], choices: [:], revisions: []),
            topupsTodayFrom: currentDay)
    }
    @State private var choosingDate = false
    @State private var conflict = false
    @State private var ready = false
    @State private var checking = false
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Rectangle().fill(CanStyle.separator).frame(height: 0.5)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Сумма нового бюджета").font(.subheadline).foregroundStyle(.secondary)
                        TextField("0", text: Binding(get: { amount }, set: { amount = $0; amountDirty = true }))
                            .keyboardType(.decimalPad)
                            .font(.system(size: 64, weight: .regular)).monospacedDigit().minimumScaleFactor(0.4)
                            .accessibilityLabel("Сумма нового бюджета")
                            .accessibilityIdentifier("budget-editor.amount")
                        if let p = preview {
                            Text("\(p.today_remaining_minor.map(Money.format) ?? "—") на сегодня")
                                .font(.body).foregroundStyle(.secondary)
                                .accessibilityIdentifier("budget-editor.preview")
                        }
                    }
                    VStack(spacing: 0) {
                        Rectangle().fill(CanStyle.separator).frame(height: 0.5)
                        Button { choosingDate = true } label: {
                            HStack {
                                Text("Срок").foregroundStyle(CanStyle.secondary)
                                Spacer()
                                Text("по \(BudgetDisplay.date(BusinessTime.day(end)))")
                                Image(systemName: "chevron.right").font(.caption2.weight(.semibold))
                            }.font(.body).frame(minHeight: 58)
                        }.accessibilityIdentifier("budget-editor.date")
                        Rectangle().fill(CanStyle.separator).frame(height: 0.5)
                    }
                    let count = (BusinessTime.calendar.dateComponents([.day], from: BusinessTime.date(currentDay), to: end).day ?? 0) + 1
                    Text("\(BudgetDisplay.days(count)), включая сегодня.\(restarting ? "\nТекущий бюджет завершится и останется в истории. Новый расчёт начнётся с сегодняшнего дня." : "")")
                        .font(.footnote).foregroundStyle(.secondary).lineSpacing(5)
                    if let failure { Text(failure).font(.footnote).foregroundStyle(CanStyle.danger).accessibilityIdentifier("budget-editor.failure") }
                    if busy || checking { ProgressView(checking ? "Проверяем расчёт" : "Сохраняем") }
                    if !checking && !ready { Button("Обновить расчёт") { Task { await refreshBasis() } } }
                }.padding(24)
            }.canScreen().navigationTitle("Новый бюджет").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Отменить") { dismiss() }.foregroundStyle(.secondary) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Начать") { submit() }.fontWeight(.semibold)
                            .disabled(busy || !ready || basis == nil || preview == nil).accessibilityIdentifier("budget-editor.save")
                    }
                }
                .safeAreaInset(edge: .bottom) {
                    NavigationLink("Кабинет и участники", destination: SettingsView())
                        .font(.subheadline).frame(maxWidth: .infinity, minHeight: 50)
                        .background(CanStyle.background).accessibilityIdentifier("budget-editor.cabinet")
                }
                .sheet(isPresented: $choosingDate) {
                    BudgetDatePicker(selection: $end, currentDay: currentDay, range: dateRange) { endDirty = true }
                }
                .alert("Бюджет изменился", isPresented: $conflict) {
                    Button("Проверить расчёт", role: .cancel) { }
                } message: { Text("Ваш ввод сохранён. Проверьте обновлённый расчёт перед повторным сохранением.") }
        }
        .onAppear { preparePreview() }
        .task { await refreshBasis() }
    }
    private func preparePreview() {
        guard basis == nil else { return }
        basis = store.snapshot
        if restarting, let p = basis?.projection, let sprint = basis?.sprint {
            amount = Money.editable(p.bank_minor); end = BusinessTime.date(sprint.end_date)
        } else { end = BusinessTime.date(currentDay) }
    }
    private func refreshBasis() async {
        checking = true
        defer { checking = false }
        do {
            preparePreview()
            let cabinet = store.snapshot?.cabinet.id
            try await store.requireSettled()
            guard !Task.isCancelled, cabinet == store.snapshot?.cabinet.id else { return }
            basis = store.snapshot
            if restarting, let p = basis?.projection, let sprint = basis?.sprint {
                if !amountDirty { amount = Money.editable(p.bank_minor) }
                if !endDirty { end = BusinessTime.date(sprint.end_date) }
            }
            ready = true
            failure = nil
        } catch {
            if !RequestFailure.isCancellation(error) { failure = error.localizedDescription }
        }
    }
    private func submit() {
        busy = true
        Task {
            do { try await save(); dismiss() }
            catch {
                if let error = error as? APIError, ["stale_revision", "business_date_changed"].contains(error.code) { conflict = true }
                failure = error.localizedDescription + " Введённые поля сохранены. Проверьте обновлённый расчёт и подтвердите ещё раз."
                basis = store.snapshot
                if restarting, !amountDirty, let bank = basis?.projection?.bank_minor { amount = Money.editable(bank) }
                if restarting, !endDirty, let date = basis?.sprint?.end_date { end = BusinessTime.date(date) }
            }
            busy = false
        }
    }
    private func save() async throws {
        guard let basis, let value = Money.parse(amount), value > 0 else {
            throw APIError.local("Проверьте сумму.")
        }
        // Reuse the pending command's sprint identifier after a lost HTTP response.
        let existing = store.state?.onlineIntents.values.first { $0.command.type == "create_sprint" && $0.cabinetID == basis.cabinet.id }
        let id = existing?.command.payload["sprint_id"]?.text ?? UUID().uuidString.lowercased()
        var payload: [String: JSONValue] = ["sprint_id": .string(id),
            "initial_minor": .integer(value), "end_date": .string(BusinessTime.day(end)),
            "expected_business_date": .string(basis.business_date),
            "expected_cabinet_revision": .integer(basis.cabinet_revision)]
        if restarting, let sprint = basis.sprint { payload["replaces_sprint_id"] = .string(sprint.id) }
        _ = try await store.onlineCommand("create_sprint", payload: payload)
    }
}

struct CarryView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var basis: Snapshot?
    @State private var mode: String? = "distribute"
    @State private var busy = false
    @State private var failure: String?
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Вчера осталось").font(.subheadline).foregroundStyle(.secondary)
                        Text(Money.format(basis?.projection?.carry_minor ?? 0)).font(.system(size: 52, weight: .regular)).monospacedDigit()
                    }
                    if let p = basis?.projection, p.carry_status == "pending" {
                        VStack(spacing: 0) {
                            choice("today", title: "Оставить на сегодня", amount: p.today_mode_remaining_minor)
                            Divider()
                            choice("distribute", title: "Распределить по дням", amount: p.distribute_remaining_minor)
                        }
                        Text("В банке те же \(Money.format(p.bank_minor)). Выбор — общий для всех участников.")
                            .font(.footnote).foregroundStyle(.secondary)
                        Button("Готово") { Task { await choose() } }.buttonStyle(CanPrimaryButtonStyle()).disabled(busy || mode == nil)
                    } else if basis != nil { Text("Выбор уже сделан или перенос сегодня не требуется.").foregroundStyle(.secondary) }
                    if let failure { Text(failure).font(.footnote).foregroundStyle(CanStyle.danger) }
                }.padding(24)
            }.canScreen().navigationTitle("Новый день")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() }.foregroundStyle(.secondary) } }
                .task { await refreshBasis() }

        }.presentationDetents([.height(550), .large]).presentationDragIndicator(.visible)
    }
    private func choice(_ value: String, title: String, amount: Int64?) -> some View {
        Button { mode = value } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.subheadline).foregroundStyle(.primary)
                    if value == "distribute" { Text("Уже учтено предварительно").font(.caption2).foregroundStyle(.secondary) }
                }
                Spacer(minLength: 5)
                Text(amount.map(Money.format) ?? "—").font(.title3).monospacedDigit().foregroundStyle(CanStyle.accent)
                Image(systemName: mode == value ? "checkmark.circle.fill" : "circle").foregroundStyle(mode == value ? CanStyle.accent : CanStyle.secondary)
            }.frame(minHeight: 72)
        }.buttonStyle(.plain).disabled(busy)
    }
    private func refreshBasis() async {
        do { try await store.requireSettled(); basis = store.snapshot }
        catch { if !RequestFailure.isCancellation(error) { failure = error.localizedDescription } }
    }
    private func choose() async {
        guard let basis, let sprint = basis.sprint, let mode else { return }
        busy = true; defer { busy = false }
        do {
            _ = try await store.onlineCommand("choose_carry", payload: ["sprint_id": .string(sprint.id),
                "day": .string(basis.business_date), "mode": .string(mode), "expected_cabinet_revision": .integer(basis.cabinet_revision)])
            dismiss()
        } catch {
            failure = error.localizedDescription + " Проверьте новые варианты. Повторное подтверждение остаётся за вами."
            self.basis = store.snapshot
        }
    }
}

struct HistoryView: View {
    @EnvironmentObject private var store: AppStore
    var archivedSprint: Sprint?
    @State private var entries: [Entry] = []
    @State private var nextCursor: String?
    @State private var observedRevision: Int64?
    @State private var selected: Entry?
    @State private var archivedProjection: ArchivedProjection?
    @State private var busy = false
    @State private var loadGeneration = UUID()
    private var sprint: Sprint? { archivedSprint ?? store.snapshot?.sprint }
    private var revisions: [SprintRevision] { archivedProjection?.sprint_revisions ?? store.snapshot?.sprint_revisions ?? [] }
    var body: some View {
        List {
            if let p = archivedProjection?.projection { LabeledContent("Итоговый остаток", value: Money.format(p.bank_minor)) }
            if archivedSprint == nil, store.pendingCount > 0 {
                NavigationLink("На телефоне: \(store.pendingCount)", destination: QueueView())
            }
            ForEach(Dictionary(grouping: entries, by: \.effective_day).keys.sorted(by: >), id: \.self) { day in
                Section(day == BusinessTime.day(Date()) ? "Сегодня, \(BudgetDisplay.date(day))" : BudgetDisplay.date(day)) {
                    ForEach(entries.filter { $0.effective_day == day }) { entry in
                        Button { selected = entry } label: {
                            HStack(spacing: 14) {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text("\(entry.kind == "expense" ? "−" : "+")\(Money.format(entry.amount_minor))")
                                        .font(.system(size: 23, weight: .medium)).monospacedDigit().strikethrough(entry.deleted_at != nil)
                                        .foregroundStyle(entry.kind == "expense" ? Color.primary : CanStyle.accent)
                                    HStack(spacing: 4) {
                                        Text(HistoryIdentity.displayName(entry.author_name, id: entry.author_id, members: store.snapshot?.cabinet.members ?? []))
                                        Text("·")
                                        Text(BusinessTime.parse(entry.occurred_at) ?? .distantPast, style: .time)
                                        if entry.deleted_at != nil { Text("· отменено") }
                                        else if entry.revision > 1 { Text("· изменено") }
                                    }.font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "ellipsis").foregroundStyle(.secondary)
                            }.padding(.vertical, 9).foregroundStyle(.primary)
                        }.listRowBackground(CanStyle.background)
                    }
                }
            }
            if !revisions.isEmpty {
                Section("Изменения спринта") {
                    ForEach(revisions.sorted { $0.sequence > $1.sequence }) { change in
                        VStack(alignment: .leading, spacing: 5) {
                            Text("\(HistoryIdentity.displayName(change.actor_name, id: change.actor_id, members: store.snapshot?.cabinet.members ?? [])) · \(change.effective_day)").font(.caption).foregroundStyle(.secondary)
                            if change.bank_adjustment_minor != 0 { Text("Остаток: \(Money.format(change.bank_before_minor)) → \(Money.format(change.bank_after_minor))") }
                            if change.end_date_before != change.end_date_after { Text("Срок: \(change.end_date_before) → \(change.end_date_after)") }
                        }
                    }
                }
            }
            if nextCursor != nil { Button("Загрузить ещё") { Task { await load(reset: false) } }.disabled(busy) }
            if entries.isEmpty && !busy { Text("Пока нет операций").foregroundStyle(.secondary) }
            if busy { ProgressView() }
        }.listStyle(.plain).canScreen().navigationTitle(archivedSprint == nil ? "Операции" : "Прошлый бюджет")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { NavigationLink("Кабинет", destination: SettingsView()).font(.subheadline) } }
            .task { await load() }.refreshable { await load() }
            .onChange(of: store.snapshot?.cabinet_revision) { _, _ in Task { await load() } }
            .sheet(item: $selected, onDismiss: { Task { await load() } }) { entry in EntryEditor(entry: entry, archived: archivedSprint != nil) }
    }
    private func load(reset: Bool = true) async {
        guard let cabinet = store.user?.active_cabinet_id, let sprint else { return }
        let generation = UUID(); loadGeneration = generation
        busy = true; defer { if generation == loadGeneration { busy = false } }
        do {
            var path = "/v1/cabinets/\(cabinet)/sprints/\(sprint.id)/entries?limit=100"
            if !reset, let nextCursor { path += "&cursor=\(nextCursor.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")" }
            let page: EntryPage = try await store.api.request(path)
            guard generation == loadGeneration, cabinet == store.user?.active_cabinet_id else { return }
            if !reset, let observedRevision, observedRevision != page.observed_cabinet_revision {
                let first: EntryPage = try await store.api.request("/v1/cabinets/\(cabinet)/sprints/\(sprint.id)/entries?limit=100")
                guard generation == loadGeneration, cabinet == store.user?.active_cabinet_id else { return }
                entries = first.items; nextCursor = first.next_cursor; self.observedRevision = first.observed_cabinet_revision
                return
            }
            entries = reset ? page.items : entries + page.items.filter { item in !entries.contains(where: { $0.id == item.id }) }
            nextCursor = page.next_cursor
            observedRevision = page.observed_cabinet_revision
            if archivedSprint != nil {
                let projection: ArchivedProjection = try await store.api.request("/v1/cabinets/\(cabinet)/sprints/\(sprint.id)/projection")
                guard generation == loadGeneration, cabinet == store.user?.active_cabinet_id else { return }
                archivedProjection = projection
            }
        } catch { if !RequestFailure.isCancellation(error) { store.message = error.localizedDescription } }
    }
}

struct ArchiveView: View {
    @EnvironmentObject private var store: AppStore
    @State private var sprints: [Sprint] = []
    @State private var cursor: String?
    @State private var finalBalances: [String: Int64] = [:]
    @State private var loaded = false
    var body: some View {
        List {
            if loaded, sprints.allSatisfy({ $0.status != "completed" }), cursor == nil {
                Text("Завершённые бюджеты появятся здесь.").font(.subheadline).foregroundStyle(.secondary)
                    .listRowBackground(Color.clear).listRowSeparator(.hidden).padding(.vertical, 12)
            }
            ForEach(sprints.filter { $0.status == "completed" }) { sprint in
                NavigationLink(destination: HistoryView(archivedSprint: sprint)) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("\(BudgetDisplay.date(sprint.start_date)) — \(BudgetDisplay.date(sprint.historyEndDate))")
                        if let bank = finalBalances[sprint.id] { Text("Осталось \(Money.format(bank))").font(.subheadline).foregroundStyle(.secondary) }
                        else { Text("На старте \(Money.format(sprint.initial_minor))").font(.subheadline).foregroundStyle(.secondary) }
                    }.padding(.vertical, 6)
                }.task {
                    guard finalBalances[sprint.id] == nil, let cabinet = store.user?.active_cabinet_id else { return }
                    if let result: ArchivedProjection = try? await store.api.request("/v1/cabinets/\(cabinet)/sprints/\(sprint.id)/projection"),
                       !Task.isCancelled, cabinet == store.user?.active_cabinet_id { finalBalances[sprint.id] = result.projection.bank_minor }
                }
            }
            if cursor != nil { Button("Загрузить ещё") { Task { await load() } } }
        }.listStyle(.plain).canScreen().navigationTitle("Прошлые бюджеты").task { if sprints.isEmpty { await load() } }
    }
    private func load() async {
        guard let cabinet = store.user?.active_cabinet_id else { return }
        do {
            let path = "/v1/cabinets/\(cabinet)/sprints?limit=100" + (cursor.map { "&cursor=\($0.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")" } ?? "")
            let page: SprintPage = try await store.api.request(path)
            sprints += page.items.filter { item in !sprints.contains(where: { $0.id == item.id }) }; cursor = page.next_cursor; loaded = true
        } catch { if !RequestFailure.isCancellation(error) { store.message = error.localizedDescription } }
    }
}

struct EntryEditor: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let entry: Entry
    let archived: Bool
    @State private var basis: Entry?
    @State private var amount = ""
    @State private var kind = "expense"
    @State private var failure: String?
    @State private var deleting = false
    @State private var busy = false
    @State private var audit: [EntryAudit] = []
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("\(HistoryIdentity.displayName(entry.author_name, id: entry.author_id, members: store.snapshot?.cabinet.members ?? [])) · \(BudgetDisplay.time(BusinessTime.parse(entry.occurred_at) ?? .distantPast))")
                        .font(.subheadline).foregroundStyle(.secondary)
                    Picker("Тип", selection: $kind) { Text("Расход").tag("expense"); Text("Пополнение").tag("topup") }.pickerStyle(.segmented)
                    TextField("Сумма", text: $amount).keyboardType(.decimalPad)
                        .font(.system(size: 52, weight: .regular)).monospacedDigit().accessibilityIdentifier("entry-editor.amount")
                    if archived { Text("Изменение относится только к этому прошлому бюджету.").font(.footnote).foregroundStyle(.secondary) }
                    if let failure { Text(failure).font(.footnote).foregroundStyle(CanStyle.danger) }
                    if let current = basis, current.revision != entry.revision {
                        Text("Сейчас на сервере: \(current.kind == "expense" ? "расход" : "пополнение") \(Money.format(current.amount_minor)). Проверьте изменение ещё раз.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Button("Сохранить") { Task { await save(void: false) } }.buttonStyle(CanPrimaryButtonStyle())
                        .disabled(busy || basis?.deleted_at != nil || (Money.parse(amount) ?? 0) <= 0)
                    Button("Удалить операцию", role: .destructive) { deleting = true }
                        .frame(maxWidth: .infinity, minHeight: 44).disabled(busy || basis?.deleted_at != nil)
                    if !audit.isEmpty {
                        DisclosureGroup("Журнал изменений") {
                            ForEach(audit) { revision in
                                VStack(alignment: .leading, spacing: 6) {
                                    Text("\(HistoryIdentity.displayName(revision.actor_name)) · \(revision.created_at)").font(.caption).foregroundStyle(.secondary)
                                    if let before = revision.before_state?["amount_minor"]?.number, let after = revision.after_state?["amount_minor"]?.number {
                                        Text("\(Money.format(before)) → \(Money.format(after))").font(.subheadline)
                                    }
                                    if revision.after_state?["deleted_at"] != nil && revision.after_state?["deleted_at"] != .null { Text("Операция отменена").font(.caption) }
                                }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 6)
                            }
                        }.font(.footnote).foregroundStyle(.secondary)
                    }
                }.padding(24)
            }.canScreen().navigationTitle("Изменить сумму")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() }.foregroundStyle(.secondary) } }
                .confirmationDialog("Удалить \(Money.format(entry.amount_minor))?", isPresented: $deleting, titleVisibility: .visible) {
                    Button("Удалить операцию", role: .destructive) { Task { await save(void: true) } }
                } message: { Text("Общий остаток пересчитается. Запись останется в журнале изменений.") }
                .onAppear { basis = entry; amount = Money.editable(entry.amount_minor); kind = entry.kind }
                .task(id: store.snapshot?.cabinet_revision) {
                    guard let cabinet = store.user?.active_cabinet_id else { return }
                    do {
                        let page: EntryAuditPage = try await store.api.request("/v1/cabinets/\(cabinet)/sprints/\(entry.sprint_id)/entries/\(entry.id)/revisions")
                        guard !Task.isCancelled, cabinet == store.user?.active_cabinet_id else { return }
                        audit = page.items
                    } catch { failure = error.localizedDescription }
                }
        }.presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
    }
    private func save(void: Bool) async {
        guard let basis else { return }
        busy = true; defer { busy = false }
        do {
            var payload: [String: JSONValue] = ["entry_id": .string(entry.id), "sprint_id": .string(entry.sprint_id),
                "expected_entry_revision": .integer(basis.revision), "allow_completed": .bool(archived)]
            if !void {
                guard let value = Money.parse(amount), value > 0 else { throw APIError.local("Укажите положительную сумму.") }
                payload["amount_minor"] = .integer(value); payload["kind"] = .string(kind)
            }
            _ = try await store.onlineCommand(void ? "void_entry" : "correct_entry", payload: payload, key: "entry:\(entry.id)")
            dismiss()
        } catch {
            failure = error.localizedDescription + " Ваш ввод сохранён. Проверьте актуальную запись перед новым подтверждением."
            if let cabinet = store.user?.active_cabinet_id {
                self.basis = try? await store.api.request("/v1/cabinets/\(cabinet)/sprints/\(entry.sprint_id)/entries/\(entry.id)")
            }
        }
    }
}
