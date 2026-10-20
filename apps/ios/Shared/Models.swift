import Foundation

enum RequestFailure {
    static func isCancellation(_ error: Error) -> Bool {
        error is CancellationError || (error as? URLError)?.code == .cancelled
    }
}

enum JSONValue: Codable, Equatable, Sendable {
    case string(String), integer(Int64), decimal(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Int64.self) { self = .integer(v) }
        else if let v = try? c.decode(Double.self) { self = .decimal(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([String: JSONValue].self) { self = .object(v) }
        else { self = .array(try c.decode([JSONValue].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .integer(let v): try c.encode(v)
        case .decimal(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }
    var text: String? { if case .string(let v) = self { return v }; return nil }
    var number: Int64? { if case .integer(let v) = self { return v }; return nil }
}

struct APIError: Codable, Error, LocalizedError, Sendable {
    var code: String
    var message: String
    var request_id: String?
    var retryable: Bool
    var details: [String: JSONValue]?
    var errorDescription: String? {
        let messages = [
            "stale_revision": "Бюджет уже изменился. Проверьте новые значения и подтвердите ещё раз.",
            "stale_entry_revision": "Эту запись уже исправили. Ваш ввод сохранён; проверьте свежую версию.",
            "business_date_changed": "Начался новый день. Проверьте расчёт и подтвердите ещё раз.",
            "completed_confirmation_required": "Спринт завершён. Подтвердите, что покупка относится к нему.",
            "membership_epoch_mismatch": "Эта запись относится к прежнему членству в кабинете.",
            "membership_required": "Доступ к этому кабинету завершён. Записи на телефоне сохранены.",
            "entry_outside_sprint": "Дата покупки выходит за границы спринта. Проверьте часы телефона.",
            "clock_skew": "Часы телефона опережают сервер. Подтвердите верное время покупки.",
            "invalid_invitation": "Приглашение недействительно, использовано или истекло.",
            "invitation_invalid": "Приглашение недействительно, использовано или истекло.",
            "invitation_unavailable": "Приглашение больше не действует.",
            "cabinet_full": "В кабинете уже десять участников.",
            "already_member": "У вас уже есть активный кабинет.",
            "active_membership_exists": "У вас уже есть активный кабинет.",
            "sprint_already_active": "В кабинете уже начат спринт.",
            "sprint_completed": "Этот спринт завершён. Его нельзя переоткрыть.",
            "budget_sync_pending": "Новый бюджет создан. Дожидаемся синхронизации; повторная проверка не создаст ещё один бюджет.",
            "carry_unavailable": "Выбор переноса уже сделан или сегодня не требуется.",
            "invalid_end_date": "Выберите дату от сегодняшнего дня до 366-го дня спринта.",
            "exposure_exceeded": "Достигнут допустимый объём операций спринта.",
            "insufficient_funds": "В банке недостаточно денег. Обновите остаток и уменьшите сумму расхода.",
            "rate_limited": "Слишком много запросов. Подождите немного и повторите.",
            "unauthorized": "Нужно снова войти через Apple. Очередь на телефоне сохранена.",
            "account_deletion_pending": "Удаление этого аккаунта уже принято и выполняется. Повторный вход пока недоступен.",
            "reauthentication_required": "Подтвердите личность через Apple ещё раз.",
            "invalid_reauth_token": "Подтверждение Apple истекло. Повторите подтверждение личности.",
            "entry_voided": "Операция уже отменена.",
            "invalid_request": "Проверьте введённые данные.",
        ]
        if let known = messages[code] { return known }
        if code == "local" || message.unicodeScalars.contains(where: { (0x0400...0x04FF).contains(Int($0.value)) }) { return message }
        return retryable ? "Сервис временно недоступен. Сохранённые записи остаются на телефоне." : "Действие не выполнено. Обновите данные и повторите."
    }
    static func local(_ message: String) -> APIError {
        APIError(code: "local", message: message, retryable: false)
    }
}

struct User: Codable, Identifiable {
    let id: String
    var display_name: String
    var active_cabinet_id: String?
    var active_membership_id: String?
}
struct Member: Codable, Identifiable {
    let user_id: String
    let display_name: String
    let joined_at: String
    let membership_id: String?
    var id: String { user_id }
}
struct Cabinet: Codable {
    let id: String
    var name: String
    let timezone: String
    let revision: Int64
    let members: [Member]
}
struct Sprint: Codable, Identifiable {
    let id: String
    let initial_minor: Int64
    let start_date: String
    let end_date: String
    let initial_end_date: String
    let calculation_version: Int
    let status: String
    var topups_today_from: String? = nil
    var closed_at: String? = nil
    var historyEndDate: String {
        guard let closed_at, let closed = BusinessTime.parse(closed_at) else { return end_date }
        return min(end_date, BusinessTime.day(closed))
    }
}
struct CarryDecision: Codable {
    let day: String
    let mode: String
}
struct SprintRevision: Codable, Identifiable {
    let id: String
    let sprint_id: String
    let sequence: Int64
    let effective_day: String
    let bank_adjustment_minor: Int64
    let bank_before_minor: Int64
    let bank_after_minor: Int64
    let end_date_before: String
    let end_date_after: String
    let actor_id: String?
    let actor_name: String?
    let created_at: String
    var input: RevisionInput {
        RevisionInput(effective_day: effective_day, sequence: sequence,
                      bank_adjustment_minor: bank_adjustment_minor, end_date: end_date_after)
    }
}
struct Snapshot: Codable {
    let server_time: String
    let business_date: String
    let cabinet_revision: Int64
    let cabinet: Cabinet
    let sprint: Sprint?
    let daily_totals: [DayInput]
    let carry_decisions: [CarryDecision]
    let projection: Projection?
    let sprint_revisions: [SprintRevision]

    func project(asOf: String? = nil, additional: [DayInput] = [], revision: RevisionInput? = nil) throws -> Projection {
        try BudgetCalculation.project(input(asOf: asOf, additional: additional, revision: revision),
                                      topupsTodayFrom: sprint?.calculation_version == 3 ? sprint?.topups_today_from : nil)
    }

    func input(asOf: String? = nil, additional: [DayInput] = [], revision: RevisionInput? = nil) throws -> BudgetInput {
        guard let s = sprint, [2, 3].contains(s.calculation_version),
              s.calculation_version != 3 || s.topups_today_from != nil else {
            throw APIError.local("Для этого расчёта требуется обновить приложение.")
        }
        return BudgetInput(initial_minor: s.initial_minor, start_date: s.start_date,
                           end_date: s.initial_end_date, as_of_date: asOf ?? business_date,
                           days: daily_totals + additional,
                           choices: Dictionary(carry_decisions.map { ($0.day, $0.mode) }, uniquingKeysWith: { _, b in b }),
                           revisions: sprint_revisions.map(\.input) + (revision.map { [$0] } ?? []))
    }
}
struct Entry: Codable, Identifiable {
    let id: String
    let sprint_id: String
    let kind: String
    let amount_minor: Int64
    let occurred_at: String
    let effective_day: String
    let author_id: String?
    let last_editor_id: String?
    let author_name: String?
    let last_editor_name: String?
    let revision: Int64
    let deleted_at: String?
}
struct EntryPage: Codable { let items: [Entry]; let next_cursor: String?; let observed_cabinet_revision: Int64 }
struct EntryAudit: Codable, Identifiable {
    let entry_revision: Int64
    let actor_name: String?
    let before_state: [String: JSONValue]?
    let after_state: [String: JSONValue]?
    let created_at: String
    var id: Int64 { entry_revision }
}
struct EntryAuditPage: Codable { let items: [EntryAudit] }
struct SprintPage: Codable { let items: [Sprint]; let next_cursor: String? }
struct ArchivedProjection: Codable {
    let sprint: Sprint
    let projection: Projection
    let sprint_revisions: [SprintRevision]
}
struct Command: Codable, Equatable, Identifiable {
    var command_id: String
    var type: String
    var payload: [String: JSONValue]
    var id: String { command_id }
    init(_ type: String, _ payload: [String: JSONValue], id: String = UUID().uuidString.lowercased()) {
        command_id = id; self.type = type; self.payload = payload
    }
}
struct Invitation: Codable, Identifiable {
    let id: String
    let code: String
    let expires_at: String
    let environment: String
    var qr: String { "can-invite:v1:\(environment):\(code)" }
}
struct InvitationPreview: Codable {
    let cabinet_name: String
    let inviter_name: String
    let available_slots: Int
    let member_count: Int
    let expires_at: String
}
struct CommandResult: Codable {
    let command_id: String
    let status: String
    let result_revision: Int64?
    let entity_id: String?
    let invitation: Invitation?
}
struct Receipt: Codable {
    let command_id: String
    let status: String
    let result_revision: Int64?
    let result: CommandResult?
    let error: APIError?
}
struct SyncResponse: Codable { let snapshot: Snapshot; let receipts: [Receipt] }
struct Challenge: Codable { let challenge_id: String; let raw_nonce: String; let expires_at: String }
struct SessionTokens: Codable {
    let access_token: String
    let access_expires_at: String
    let refresh_token: String
    let refresh_expires_at: String
}
struct LoginResult: Codable {
    let user: User
    let session: SessionTokens
    let reauth_token: String
    let reauth_expires_at: String
}
struct CabinetResult: Codable { let command_id: String; let cabinet_id: String }
struct WidgetToken: Codable { let token: String; let expires_at: String }
struct WidgetSnapshot: Codable {
    let server_time: String
    let business_date: String
    let cabinet_revision: Int64
    let calculation_version: Int
    let status: String
    let today_remaining_minor: Int64?
    let bank_minor: Int64?
    let carry_pending: Bool
}

enum BusinessTime {
    static var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        return c
    }
    static func day(_ date: Date) -> String {
        let f = DateFormatter()
        f.calendar = calendar; f.timeZone = calendar.timeZone
        f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
    static func date(_ day: String) -> Date {
        let f = DateFormatter()
        f.calendar = calendar; f.timeZone = calendar.timeZone
        f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd"
        return f.date(from: day) ?? Date()
    }
    static func parse(_ value: String) -> Date? {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
    static func timestamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }
}

enum Money {
    static func format(_ minor: Int64) -> String {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "ru_RU"); f.numberStyle = .decimal
        f.maximumFractionDigits = 2; f.minimumFractionDigits = minor % 100 == 0 ? 0 : 2
        return f.string(from: NSDecimalNumber(value: minor).dividing(by: NSDecimalNumber(value: 100))) ?? "—"
    }
    static func parse(_ text: String, signed: Bool = false, max: Int64 = BudgetEngine.maxOperation) -> Int64? {
        let clean = text.replacingOccurrences(of: ",", with: ".").replacingOccurrences(of: " ", with: "")
        let negative = clean.hasPrefix("-")
        guard signed || !negative else { return nil }
        let digits = negative ? String(clean.dropFirst()) : clean
        let parts = digits.split(separator: ".", omittingEmptySubsequences: false)
        guard !parts.isEmpty, parts.count <= 2, !parts[0].isEmpty, parts[0].allSatisfy({ $0.isASCII && $0.isNumber }),
              let whole = Int64(parts[0]), whole <= max / 100 + 1 else { return nil }
        let cents = parts.count == 2 ? String(parts[1]) : ""
        guard cents.count <= 2, cents.allSatisfy({ $0.isASCII && $0.isNumber }) else { return nil }
        let value = whole * 100 + (Int64(cents.padding(toLength: 2, withPad: "0", startingAt: 0)) ?? 0)
        guard value <= max else { return nil }
        return negative ? -value : value
    }
    static func editable(_ minor: Int64) -> String {
        let a = abs(minor)
        return "\(minor < 0 ? "-" : "")\(a / 100),\(String(format: "%02lld", a % 100))"
    }
}
