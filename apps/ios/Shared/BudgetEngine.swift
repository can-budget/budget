// Can calculation_version=2. Pure reference, usable by app/server test fixtures.
// Inputs are normalized business dates, not timestamps. No WidgetKit/iOS dependency.
import Foundation

struct DayInput: Codable {
    let day: String
    let topups_minor: Int64?
    let expenses_minor: Int64?
}
struct RevisionInput: Codable {
    let effective_day: String
    let sequence: Int64
    let bank_adjustment_minor: Int64?
    let end_date: String?
}
struct BudgetInput: Codable {
    let initial_minor: Int64
    let start_date: String
    let end_date: String
    let as_of_date: String
    let days: [DayInput]?
    let choices: [String: String]?
    let revisions: [RevisionInput]?
}
struct Projection: Codable, Equatable {
    let status: String
    let bank_minor: Int64
    let today_remaining_minor: Int64?
    let days_remaining: Int
    let carry_minor: Int64
    let carry_status: String
    let today_mode_remaining_minor: Int64?
    let distribute_remaining_minor: Int64?
}
enum BudgetError: Error { case invalidInput(String) }

enum BudgetEngine {
    static let maxOperation: Int64 = 999_999_999
    static let maxExposure: Int64 = 9_000_000_000_000

    // Swift '/' truncates toward zero. Financial spec requires mathematical floor.
    static func floorDiv(_ value: Int64, _ divisor: Int64) -> Int64 {
        precondition(divisor > 0)
        let quotient = value / divisor
        return quotient - (value < 0 && value % divisor != 0 ? 1 : 0)
    }

    static func project(_ input: BudgetInput) throws -> Projection {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        func parse(_ text: String) throws -> Date {
            guard let value = formatter.date(from: text), formatter.string(from: value) == text else {
                throw BudgetError.invalidInput("Noncanonical business date")
            }
            return value
        }
        let start = try parse(input.start_date)
        let originalEnd = try parse(input.end_date)
        var end = originalEnd
        let now = try parse(input.as_of_date)
        let interval = calendar.dateComponents([.day], from: start, to: end).day!
        guard input.initial_minor > 0, input.initial_minor <= maxOperation,
              interval >= 0, interval <= 365, now >= start else {
            throw BudgetError.invalidInput("Invalid initial amount or interval")
        }
        var totals: [Date: (Int64, Int64)] = [:]
        var exposure = input.initial_minor
        typealias Revision = (day: Date, sequence: Int64, adjustment: Int64, end: Date?)
        var changes: [Revision] = []
        var changesByDay: [Date: [Revision]] = [:]
        var priorDay = start
        var priorSequence: Int64 = 0
        var cursorEnd = originalEnd
        let maxEnd = calendar.date(byAdding: .day, value: 365, to: start)!
        for revision in (input.revisions ?? []).sorted(by: { $0.sequence < $1.sequence }) {
            let effective = try parse(revision.effective_day)
            let delta = revision.bank_adjustment_minor ?? 0
            guard revision.sequence > priorSequence, effective >= priorDay,
                  effective >= start, effective <= cursorEnd,
                  delta >= -maxExposure, delta <= maxExposure else {
                throw BudgetError.invalidInput("Invalid revision order/date/amount")
            }
            let newEnd: Date?
            if let text = revision.end_date {
                newEnd = try parse(text)
                guard newEnd! >= effective, newEnd! <= maxEnd else {
                    throw BudgetError.invalidInput("Invalid revised end date")
                }
                cursorEnd = newEnd!
            } else { newEnd = nil }
            guard abs(delta) <= maxExposure - exposure else {
                throw BudgetError.invalidInput("Revision exposure exceeded")
            }
            exposure += abs(delta)
            let change: Revision = (effective, revision.sequence, delta, newEnd)
            changes.append(change)
            changesByDay[effective, default: []].append(change)
            priorDay = effective
            priorSequence = revision.sequence
        }
        func endForDay(_ day: Date) -> Date {
            var result = originalEnd
            for change in changes {
                if change.day > day { break }
                if let value = change.end { result = value }
            }
            return result
        }
        for row in input.days ?? [] {
            let day = try parse(row.day)
            let p = row.topups_minor ?? 0, e = row.expenses_minor ?? 0
            guard day >= start, day <= endForDay(day), p >= 0, e >= 0,
                  p <= maxExposure - exposure else {
                throw BudgetError.invalidInput("Invalid aggregate or exposure")
            }
            exposure += p
            guard e <= maxExposure - exposure else {
                throw BudgetError.invalidInput("Exposure exceeded")
            }
            exposure += e
            let prior = totals[day] ?? (0, 0)
            totals[day] = (prior.0 + p, prior.1 + e)
        }
        var choices: [Date: String] = [:]
        for (key, mode) in input.choices ?? [:] {
            let day = try parse(key)
            guard day >= start, day <= endForDay(day), ["today", "distribute"].contains(mode) else {
                throw BudgetError.invalidInput("Invalid carry mode")
            }
            choices[day] = mode
        }
        var bank = input.initial_minor
        var previous: Int64 = 0
        var day = start
        var result: Projection?
        while day <= now && day <= end {
            var adjustment: Int64 = 0
            for change in changesByDay[day] ?? [] {
                adjustment += change.adjustment
                if let revisedEnd = change.end { end = revisedEnd }
            }
            let n = calendar.dateComponents([.day], from: day, to: end).day! + 1
            let carry = day > start ? max(previous, 0) : 0
            let (p, e) = totals[day] ?? (0, 0)
            let distributed = floorDiv(bank, Int64(n)) + floorDiv(p + adjustment, Int64(n)) - e
            let todayMode = carry + floorDiv(bank - carry, Int64(n)) + floorDiv(p + adjustment, Int64(n)) - e
            let selectable = day > start && n > 1 && carry > 0
            let stored = choices[day]
            let available = stored == "today" ? todayMode : distributed
            bank += p + adjustment - e
            let state: String
            if !selectable { state = "none" }
            else if let stored = stored { state = "selected_\(stored)" }
            else { state = day == now ? "pending" : "auto_distribute" }
            result = Projection(status: "active", bank_minor: bank,
                today_remaining_minor: available, days_remaining: n, carry_minor: carry,
                carry_status: state, today_mode_remaining_minor: selectable ? todayMode : nil,
                distribute_remaining_minor: selectable ? distributed : nil)
            previous = available
            day = calendar.date(byAdding: .day, value: 1, to: day)!
        }
        if now > end {
            return Projection(status: "completed", bank_minor: bank, today_remaining_minor: nil,
                days_remaining: 0, carry_minor: 0, carry_status: "none",
                today_mode_remaining_minor: nil, distribute_remaining_minor: nil)
        }
        guard let result = result else { throw BudgetError.invalidInput("No projection") }
        return result
    }
}
