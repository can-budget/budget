import SwiftUI

enum CanStyle {
    static let background = Color(red: 0.043, green: 0.043, blue: 0.051)
    static let panel = Color(red: 0.11, green: 0.11, blue: 0.12)
    static let separator = Color.white.opacity(0.11)
    static let accent = Color(red: 1, green: 0.76, blue: 0.28)
    static let danger = Color(red: 1, green: 0.54, blue: 0.50)
    static let secondary = Color(red: 0.60, green: 0.60, blue: 0.63)
}

enum BudgetDisplay {
    static func days(_ count: Int) -> String {
        let remainder = count % 100
        let word = (11...14).contains(remainder) ? "дней" : count % 10 == 1 ? "день" : (2...4).contains(count % 10) ? "дня" : "дней"
        return "\(count) \(word)"
    }
    static func date(_ day: String) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "ru_RU")
        formatter.calendar = BusinessTime.calendar
        formatter.timeZone = BusinessTime.calendar.timeZone
        formatter.dateFormat = "d MMMM"
        return formatter.string(from: BusinessTime.date(day))
    }
    static func time(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "ru_RU")
        formatter.timeZone = BusinessTime.calendar.timeZone
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }
    static func nextDay(bank: Int64, days: Int) -> Int64? {
        guard days > 1 else { return nil }
        return BudgetEngine.floorDiv(bank, Int64(days - 1))
    }
}
