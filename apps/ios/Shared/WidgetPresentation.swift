import Foundation

struct WidgetPresentation {
    let state: SharedWidgetState?
    let date: Date
    var expired = false
    var noAccess = false

    private var isPreviousDay: Bool { expired || state?.businessDate != BusinessTime.day(date) }
    var hasAmount: Bool {
        guard !noAccess, !isPreviousDay, let state else { return false }
        return state.status == "completed" ? state.bankMinor != nil : state.status == "active" && state.todayMinor != nil
    }
    var title: String { hasAmount && state?.status == "completed" ? ((state?.bankMinor ?? 0) >= 0 ? "Ура!" : "Готово") : "Сегодня" }
    var amount: String {
        guard hasAmount, let minor = state?.status == "completed" ? state?.bankMinor : state?.todayMinor else { return "—" }
        return Money.format(minor)
    }
    var status: String {
        guard !noAccess, let state else { return "Откройте приложение" }
        if isPreviousDay { return "Новый день · откройте приложение" }
        if state.status == "completed" { return (state.bankMinor ?? 0) >= 0 ? "Получилось" : "Бюджет завершён" }
        if state.status != "active" { return "Задайте бюджет" }
        if state.pendingCount > 0 { return "Предварительно · \(state.pendingCount) в очереди" }
        if state.isOffline == true { return "Нет связи · \(BudgetDisplay.time(state.lastSyncAt))" }
        if date.timeIntervalSince(state.lastSyncAt) > 900 { return "Обновлено в \(BudgetDisplay.time(state.lastSyncAt))" }
        if let remaining = state.todayMinor, remaining < 0, let days = state.daysRemaining,
           let next = BudgetDisplay.nextDay(bank: state.bankMinor ?? 0, days: days), state.bankMinor != nil {
            return "Дальше \(Money.format(next))/день"
        }
        if state.carryPending { return "Предварительно · распределено по дням" }
        return ""
    }
    var inlineForecast: String {
        guard status.hasPrefix("Дальше"), let state, let bank = state.bankMinor,
              let days = state.daysRemaining, let next = BudgetDisplay.nextDay(bank: bank, days: days) else { return "" }
        return " · далее \(Money.format(next))"
    }
    var accessoryStatus: String {
        guard !noAccess, let state else { return "Откройте приложение" }
        if isPreviousDay { return "Новый день · откройте Можно" }
        if state.status == "active", state.pendingCount > 0 { return "Предварительно · \(state.pendingCount)" }
        if state.status == "active", state.carryPending, status.contains("распределено") { return "По дням · предварительно" }
        return status.isEmpty ? "Можно" : status
    }
}
