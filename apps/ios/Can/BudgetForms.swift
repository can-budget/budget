import SwiftUI

struct SprintEditor: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let editing: Bool
    @State private var basis: Snapshot?
    @State private var amount = ""
    @State private var end = Date()
    @State private var busy = false
    @State private var failure: String?
    @State private var bankDirty = false
    @State private var endDirty = false
    private var currentDay: String { basis?.business_date ?? BusinessTime.day(Date()) }
    private var dateRange: ClosedRange<Date> {
        let start = BusinessTime.date(editing ? basis?.sprint?.start_date ?? currentDay : currentDay)
        let max = BusinessTime.calendar.date(byAdding: .day, value: 365, to: start)!
        return min(BusinessTime.date(currentDay), max)...max
    }
    private var preview: Projection? {
        guard let bank = Money.parse(amount, signed: editing, max: editing ? BudgetEngine.maxExposure : BudgetEngine.maxOperation) else { return nil }
        if editing {
            guard let basis, let confirmed = basis.projection else { return nil }
            let change = RevisionInput(effective_day: basis.business_date,
                                       sequence: (basis.sprint_revisions.map(\.sequence).max() ?? 0) + 1,
                                       bank_adjustment_minor: bankDirty ? bank - confirmed.bank_minor : 0,
                                       end_date: endDirty ? BusinessTime.day(end) : basis.sprint?.end_date)
            return try? BudgetEngine.project(basis.input(revision: change))
        }
        return try? BudgetEngine.project(BudgetInput(initial_minor: bank, start_date: currentDay,
                                                     end_date: BusinessTime.day(end), as_of_date: currentDay,
                                                     days: [], choices: [:], revisions: []))
    }
    var body: some View {
        NavigationStack {
            Form {
                Section(editing ? "Текущий остаток" : "Новый спринт") {
                    TextField(editing ? "Остаток бюджета" : "Сумма", text: Binding(get: { amount }, set: { amount = $0; bankDirty = true }))
                        .keyboardType(editing ? .numbersAndPunctuation : .decimalPad)
                    DatePicker("По какую дату", selection: Binding(get: { end }, set: { end = $0; endDirty = true }), in: dateRange, displayedComponents: .date)
                        .environment(\.timeZone, BusinessTime.calendar.timeZone)
                    if editing { Text("Изменяется текущий банк. Первоначальная сумма и история сохраняются.").font(.footnote).foregroundStyle(.secondary) }
                }
                if let p = preview {
                    Section("Предварительный расчёт") {
                        LabeledContent("Остаток", value: Money.format(p.bank_minor))
                        LabeledContent("Дней, включая сегодня", value: String(p.days_remaining))
                        LabeledContent("На сегодня", value: p.today_remaining_minor.map(Money.format) ?? "—")
                    }
                }
                if let failure { Section { Text(failure).foregroundStyle(.orange) } }
                Section {
                    Button(editing ? "Подтвердить изменения" : "Начать спринт") {
                        busy = true
                        Task {
                            do { try await save(); dismiss() }
                            catch {
                                failure = error.localizedDescription + " Введённые поля сохранены. Проверьте обновлённый расчёт и подтвердите ещё раз."
                                basis = store.snapshot
                                if editing, !bankDirty, let bank = basis?.projection?.bank_minor { amount = Money.editable(bank) }
                                if editing, !endDirty, let date = basis?.sprint?.end_date { end = BusinessTime.date(date) }
                            }
                            busy = false
                        }
                    }.disabled(busy || basis == nil || preview == nil)
                    if busy { ProgressView() }
                }
            }.navigationTitle(editing ? "Изменить спринт" : "Начать спринт")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() } } }
                .task {
                    do {
                        try await store.requireSettled()
                        basis = store.snapshot
                        if editing, let p = basis?.projection, let sprint = basis?.sprint {
                            amount = Money.editable(p.bank_minor); end = BusinessTime.date(sprint.end_date)
                        } else { end = BusinessTime.date(currentDay) }
                    } catch { failure = error.localizedDescription }
                }
        }
    }
    private func save() async throws {
        guard let basis, let value = Money.parse(amount, signed: editing, max: editing ? BudgetEngine.maxExposure : BudgetEngine.maxOperation) else {
            throw APIError.local("Проверьте сумму.")
        }
        if editing, let sprint = basis.sprint {
            var payload: [String: JSONValue] = ["sprint_id": .string(sprint.id),
                "expected_cabinet_revision": .integer(basis.cabinet_revision), "expected_business_date": .string(basis.business_date)]
            if bankDirty, value != basis.projection?.bank_minor { payload["bank_minor"] = .integer(value) }
            if endDirty, BusinessTime.day(end) != sprint.end_date { payload["end_date"] = .string(BusinessTime.day(end)) }
            guard payload.count > 3 else { return }
            _ = try await store.onlineCommand("update_sprint", payload: payload)
        } else {
            guard value > 0 else { throw APIError.local("Сумма должна быть положительной.") }
            // Reuse the pending command's sprint identifier after a lost HTTP response.
            let existing = store.state?.onlineIntents.values.first { $0.command.type == "create_sprint" && $0.cabinetID == basis.cabinet.id }
            let id = existing?.command.payload["sprint_id"]?.text ?? UUID().uuidString.lowercased()
            _ = try await store.onlineCommand("create_sprint", payload: ["sprint_id": .string(id),
                "initial_minor": .integer(value), "end_date": .string(BusinessTime.day(end)),
                "expected_cabinet_revision": .integer(basis.cabinet_revision)])
        }
    }
}

struct CarryView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var basis: Snapshot?
    @State private var mode: String?
    @State private var confirming = false
    @State private var busy = false
    @State private var failure: String?
    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 24) {
                Text("Вчера осталось").font(.title2).foregroundStyle(.secondary)
                Text(Money.format(basis?.projection?.carry_minor ?? 0)).font(.system(size: 54, weight: .medium, design: .rounded))
                Text("Общий выбор для всех участников. После подтверждения его нельзя изменить.").foregroundStyle(.secondary)
                if let p = basis?.projection, p.carry_status == "pending" {
                    choice("today", title: "Добавить на сегодня", amount: p.today_mode_remaining_minor)
                    choice("distribute", title: "Распределить", amount: p.distribute_remaining_minor)
                } else if basis != nil { Text("Выбор уже сделан или перенос сегодня не требуется.") }
                if let failure { Text(failure).foregroundStyle(.orange) }
                Spacer()
            }.padding(26).navigationTitle("Перенос")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() } } }
                .task { await refreshBasis() }
                .confirmationDialog("Подтвердить общий выбор?", isPresented: $confirming, titleVisibility: .visible) {
                    Button(mode == "today" ? "Добавить на сегодня" : "Распределить") { Task { await choose() } }
                }
        }
    }
    private func choice(_ value: String, title: String, amount: Int64?) -> some View {
        Button { mode = value; confirming = true } label: {
            HStack { Text(title); Spacer(); Text(amount.map(Money.format) ?? "—") }.padding(18)
                .background(CanStyle.panel, in: RoundedRectangle(cornerRadius: 16))
        }.disabled(busy)
    }
    private func refreshBasis() async {
        do { try await store.requireSettled(); basis = store.snapshot }
        catch { failure = error.localizedDescription }
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
            if archivedSprint == nil { NavigationLink("Прошлые спринты", destination: ArchiveView()) }
            if let p = archivedProjection?.projection { LabeledContent("Итоговый остаток", value: Money.format(p.bank_minor)) }
            if archivedSprint == nil, store.pendingCount > 0 {
                NavigationLink("На телефоне: \(store.pendingCount)", destination: QueueView())
            }
            ForEach(Dictionary(grouping: entries, by: \.effective_day).keys.sorted(by: >), id: \.self) { day in
                Section(day) {
                    ForEach(entries.filter { $0.effective_day == day }) { entry in
                        Button { selected = entry } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(HistoryIdentity.displayName(entry.author_name, id: entry.author_id, members: store.snapshot?.cabinet.members ?? []))
                                    Text(BusinessTime.parse(entry.occurred_at) ?? .distantPast, style: .time).font(.caption).foregroundStyle(.secondary)
                                    if entry.revision > 1 { Text("Изменено: \(HistoryIdentity.displayName(entry.last_editor_name, id: entry.last_editor_id, members: store.snapshot?.cabinet.members ?? []))").font(.caption).foregroundStyle(.secondary) }
                                    if entry.deleted_at != nil { Text("Отменено").font(.caption).foregroundStyle(.secondary) }
                                }
                                Spacer()
                                Text("\(entry.kind == "expense" ? "−" : "+")\(Money.format(entry.amount_minor))")
                                    .monospacedDigit().strikethrough(entry.deleted_at != nil)
                            }.foregroundStyle(.primary)
                        }
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
            if entries.isEmpty && !busy { Text("В этом спринте пока нет операций.").foregroundStyle(.secondary) }
            if busy { ProgressView() }
        }.navigationTitle(archivedSprint == nil ? "История" : "Архив")
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
        } catch { store.message = error.localizedDescription }
    }
}

struct ArchiveView: View {
    @EnvironmentObject private var store: AppStore
    @State private var sprints: [Sprint] = []
    @State private var cursor: String?
    var body: some View {
        List {
            ForEach(sprints.filter { $0.status == "completed" }) { sprint in
                NavigationLink(destination: HistoryView(archivedSprint: sprint)) {
                    VStack(alignment: .leading) { Text("\(sprint.start_date) — \(sprint.end_date)"); Text("На старте \(Money.format(sprint.initial_minor))").foregroundStyle(.secondary) }
                }
            }
            if cursor != nil { Button("Загрузить ещё") { Task { await load() } } }
        }.navigationTitle("Прошлые спринты").task { if sprints.isEmpty { await load() } }
    }
    private func load() async {
        guard let cabinet = store.user?.active_cabinet_id else { return }
        do {
            let path = "/v1/cabinets/\(cabinet)/sprints?limit=100" + (cursor.map { "&cursor=\($0.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")" } ?? "")
            let page: SprintPage = try await store.api.request(path)
            sprints += page.items.filter { item in !sprints.contains(where: { $0.id == item.id }) }; cursor = page.next_cursor
        } catch { store.message = error.localizedDescription }
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
            Form {
                Section {
                    Picker("Тип", selection: $kind) { Text("Расход").tag("expense"); Text("Пополнение").tag("topup") }.pickerStyle(.segmented)
                    TextField("Сумма", text: $amount).keyboardType(.decimalPad)
                    Text("Дата: \(entry.effective_day)").foregroundStyle(.secondary)
                    if archived { Text("Изменение относится только к этому прошлому спринту.").foregroundStyle(.secondary) }
                }
                if let failure { Text(failure).foregroundStyle(.orange) }
                if let current = basis, current.revision != entry.revision {
                    Text("Сейчас на сервере: \(current.kind == "expense" ? "расход" : "пополнение") \(Money.format(current.amount_minor)), версия \(current.revision).")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Button("Сохранить исправление") { Task { await save(void: false) } }.disabled(busy || basis?.deleted_at != nil)
                Button("Отменить операцию", role: .destructive) { deleting = true }.disabled(busy || basis?.deleted_at != nil)
                Text("После отмены финансовый эффект убирается, след в аудите сохраняется.").font(.footnote).foregroundStyle(.secondary)
                if !audit.isEmpty {
                    Section("История исправлений") {
                        ForEach(audit) { revision in
                            VStack(alignment: .leading, spacing: 6) {
                                Text("\(HistoryIdentity.displayName(revision.actor_name)) · \(revision.created_at)").font(.caption).foregroundStyle(.secondary)
                                if let before = revision.before_state?["amount_minor"]?.number,
                                   let after = revision.after_state?["amount_minor"]?.number {
                                    Text("\(Money.format(before)) → \(Money.format(after))")
                                }
                                if revision.after_state?["deleted_at"] != nil && revision.after_state?["deleted_at"] != .null { Text("Операция отменена") }
                            }
                        }
                    }
                }
            }.navigationTitle("Операция")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() } } }
                .confirmationDialog("Отменить эту операцию?", isPresented: $deleting, titleVisibility: .visible) {
                    Button("Отменить операцию", role: .destructive) { Task { await save(void: true) } }
                }
                .onAppear { basis = entry; amount = Money.editable(entry.amount_minor); kind = entry.kind }
                .task(id: store.snapshot?.cabinet_revision) {
                    guard let cabinet = store.user?.active_cabinet_id else { return }
                    do {
                        let page: EntryAuditPage = try await store.api.request("/v1/cabinets/\(cabinet)/sprints/\(entry.sprint_id)/entries/\(entry.id)/revisions")
                        guard !Task.isCancelled, cabinet == store.user?.active_cabinet_id else { return }
                        audit = page.items
                    } catch { failure = error.localizedDescription }
                }
        }
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
