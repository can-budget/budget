import XCTest
@testable import Can

final class WidgetPresentationTests: XCTestCase {
    private let now = BusinessTime.parse("2026-10-19T04:00:00Z")!

    private func state() -> SharedWidgetState {
        SharedWidgetState(cabinetID: "cabinet", revision: 3, businessDate: BusinessTime.day(now),
                          calculatedAt: now, lastSyncAt: now, todayMinor: 15_833, bankMinor: 245_000,
                          pendingCount: 0, carryPending: false, status: "active")
    }
    func testActiveAmountPreservesCentsAndNegativeBalance() {
        var snapshot = state()
        XCTAssertEqual(WidgetPresentation(state: snapshot, date: now).amount, "158,33")
        snapshot.todayMinor = -1
        XCTAssertEqual(WidgetPresentation(state: snapshot, date: now).amount, "-0,01")
        snapshot.todayMinor = 999_999_999
        let largeAmount = WidgetPresentation(state: snapshot, date: now).amount.filter { !$0.isWhitespace }
        XCTAssertEqual(largeAmount, "9999999,99")
    }
    func testMissingOrRevokedAccessNeverExposesCachedAmount() {
        XCTAssertFalse(WidgetPresentation(state: nil, date: now).hasAmount)
        let revoked = WidgetPresentation(state: state(), date: now, noAccess: true)
        XCTAssertEqual(revoked.amount, "—")
        XCTAssertEqual(revoked.status, "Откройте приложение")
        XCTAssertEqual(revoked.accessoryStatus, "Откройте приложение")
    }
    func testShanghaiMidnightHidesPreviousDayWithoutDependingOnRefresh() {
        let midnight = BusinessTime.parse("2026-10-19T16:00:00Z")!
        let nextDay = WidgetPresentation(state: state(), date: midnight)
        XCTAssertFalse(nextDay.hasAmount)
        XCTAssertEqual(nextDay.amount, "—")
        XCTAssertTrue(nextDay.status.contains("Новый день"))
        XCTAssertTrue(nextDay.accessoryStatus.contains("Новый день"))
        XCTAssertFalse(WidgetPresentation(state: state(), date: now, expired: true).hasAmount)
    }
    func testCompletedShowsFinalBankWhileInactiveAndMissingAmountsStayNeutral() {
        var snapshot = state()
        snapshot.status = "completed"
        XCTAssertEqual(WidgetPresentation(state: snapshot, date: now).amount.filter { !$0.isWhitespace }, "2450")
        XCTAssertEqual(WidgetPresentation(state: snapshot, date: now).title, "Ура!")
        for status in ["no_sprint"] {
            snapshot.status = status
            XCTAssertEqual(WidgetPresentation(state: snapshot, date: now).amount, "—")
        }
        snapshot.status = "active"
        snapshot.todayMinor = nil
        XCTAssertEqual(WidgetPresentation(state: snapshot, date: now).amount, "—")
    }
    func testPendingExpensesStayVisibleAndMarkedOnBothSurfaces() {
        var snapshot = state()
        snapshot.pendingCount = 2
        snapshot.lastSyncAt = now.addingTimeInterval(-1_800)
        let pending = WidgetPresentation(state: snapshot, date: now)
        XCTAssertTrue(pending.hasAmount)
        XCTAssertTrue(pending.status.contains("Предварительно"))
        XCTAssertEqual(pending.accessoryStatus, "Предварительно · 2")
    }
    func testStaleAndCarryStatesRemainVisibleOnBothSurfaces() {
        var snapshot = state()
        snapshot.carryPending = true
        let carry = WidgetPresentation(state: snapshot, date: now)
        XCTAssertTrue(carry.status.contains("распределено"))
        XCTAssertTrue(carry.accessoryStatus.contains("предварительно"))
        snapshot.lastSyncAt = now.addingTimeInterval(-901)
        let stale = WidgetPresentation(state: snapshot, date: now)
        XCTAssertTrue(stale.hasAmount)
        XCTAssertTrue(stale.status.hasPrefix("Обновлено в "))
        XCTAssertEqual(stale.accessoryStatus, stale.status)
    }
    func testOverdrawnGuideUsesActualBankWithoutSubtractingExpensesAgain() throws {
        let projection = try BudgetEngine.project(BudgetInput(initial_minor: 1_000_000, start_date: "2026-10-19", end_date: "2026-10-28", as_of_date: "2026-10-19",
                                                              days: [DayInput(day: "2026-10-19", topups_minor: 0, expenses_minor: 120_000)], choices: [:], revisions: []))
        XCTAssertEqual(projection.today_remaining_minor, -20_000)
        XCTAssertEqual(BudgetDisplay.nextDay(bank: projection.bank_minor, days: projection.days_remaining), 97_777)
        XCTAssertNil(BudgetDisplay.nextDay(bank: projection.bank_minor, days: 1))
        XCTAssertEqual(BudgetDisplay.nextDay(bank: -1, days: 3), -1)
        var snapshot = state()
        snapshot.todayMinor = projection.today_remaining_minor
        snapshot.bankMinor = projection.bank_minor
        snapshot.daysRemaining = projection.days_remaining
        XCTAssertEqual(WidgetPresentation(state: snapshot, date: now).inlineForecast, " · далее 977,77")
        snapshot.daysRemaining = 1
        XCTAssertTrue(WidgetPresentation(state: snapshot, date: now).inlineForecast.isEmpty)
    }
}
