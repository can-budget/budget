import XCTest
@testable import Can

final class DraftPreviewTests: XCTestCase {
    private let now = BusinessTime.parse("2026-10-20T04:00:00Z")!
    private struct Example: Decodable {
        let name: String
        let topups_today_from: String
        let input: BudgetInput
        let expected: Projection
    }
    func testVersion3MatchesSharedServerExamples() throws {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "today_topup_cases", withExtension: "json"))
        let cases = try JSONDecoder().decode([Example].self, from: Data(contentsOf: url))
        XCTAssertEqual(cases.count, 12)
        for example in cases {
            XCTAssertEqual(try BudgetCalculation.project(example.input, topupsTodayFrom: example.topups_today_from), example.expected, example.name)
        }
    }
    func testExpensePreviewUpdatesPerKeystrokeAndDeleteWithoutChangingSavedBudget() throws {
        var state = try fixture()
        for (input, expected) in [("5", Int64(19500)), ("50", 15000), ("5", 19500), ("", 20000)] {
            state.draftAmount = input
            XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true)?.today_remaining_minor, expected)
            XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now)?.today_remaining_minor, 20000)
            XCTAssertTrue(state.pending.isEmpty)
            XCTAssertEqual(state.snapshot?.cabinet_revision, 1)
        }
    }
    func testTopupPreviewUpdatesPerKeystrokeAndKindSwitch() throws {
        var state = try fixture()
        state.draftKind = "topup"
        for (input, expected) in [("2", Int64(20200)), ("20", 22000)] {
            state.draftAmount = input
            XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true)?.today_remaining_minor, expected)
        }
        state.draftKind = "expense"
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true)?.today_remaining_minor, 18000)
    }
    func testDraftIsNotAppliedTwiceWhenQueuedAndAcknowledged() throws {
        for kind in ["expense", "topup"] {
            var state = try fixture()
            state.draftKind = kind; state.draftAmount = "20"
            let before = try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true)
            state.pending.append(pending(kind: kind, amount: 2000))
            state.draftAmount = ""; state.draftKind = "expense"
            XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true), before)
            let confirmed = try fixture(topup: kind == "topup" ? 2000 : 0, expense: kind == "expense" ? 2000 : 0, revision: 2)
            let receipt = Receipt(command_id: "command", status: "applied", result_revision: 2,
                result: CommandResult(command_id: "command", status: "applied", result_revision: 2, entity_id: "entry", invitation: nil), error: nil)
            let next = try Reconciliation.apply(SyncResponse(snapshot: confirmed.snapshot!, receipts: [receipt]), requestedIDs: ["command"], to: state)
            XCTAssertTrue(next.pending.isEmpty)
            XCTAssertEqual(try Reconciliation.projection(next, membershipID: "member", now: now), before)
        }
    }
    func testExistingPendingExpenseAndDraftAreEachCountedOnce() throws {
        var state = try fixture()
        state.pending = [pending(kind: "expense", amount: 1000)]
        state.draftAmount = "5"
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true)?.today_remaining_minor, 18500)
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now)?.today_remaining_minor, 19000)
        state.pending[0].state = "needs_attention"
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true)?.today_remaining_minor, 19500)
    }
    func testInvalidDraftAndCompletedSprintNeverChangeTheProjection() throws {
        var state = try fixture()
        for input in ["", "0", "-5", "1,234", "999999999999999"] {
            state.draftAmount = input
            XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true),
                           try Reconciliation.projection(state, membershipID: "member", now: now))
        }
        state.draftAmount = "5"
        let afterEnd = BusinessTime.parse("2026-10-30T04:00:00Z")!
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: afterEnd, includingDraft: true),
                       try Reconciliation.projection(state, membershipID: "member", now: afterEnd))
    }
    func testExpenseCanExceedDailyAllowanceButNotTheBank() throws {
        var state = try fixture()
        let base = try XCTUnwrap(Reconciliation.projection(state, membershipID: "member", now: now))
        state.draftAmount = "201"
        XCTAssertFalse(Reconciliation.draftExceedsBank(state, projection: base))
        let dailyOverspend = try XCTUnwrap(Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true))
        XCTAssertEqual(dailyOverspend.today_remaining_minor, -100)
        XCTAssertEqual(dailyOverspend.bank_minor, 179900)
        XCTAssertEqual(BudgetDisplay.nextDay(bank: dailyOverspend.bank_minor, days: dailyOverspend.days_remaining), 19988)
        state.draftAmount = "2000"
        XCTAssertFalse(Reconciliation.draftExceedsBank(state, projection: base))
        let exhausted = try XCTUnwrap(Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true))
        XCTAssertEqual(exhausted.bank_minor, 0)
        XCTAssertEqual(BudgetDisplay.nextDay(bank: exhausted.bank_minor, days: exhausted.days_remaining), 0)
        state.draftAmount = "2000,01"
        XCTAssertTrue(Reconciliation.draftExceedsBank(state, projection: base))
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true), base)
        XCTAssertEqual(state.draftAmount, "2000,01")
        XCTAssertTrue(state.pending.isEmpty)
    }
    func testBankLimitIncludesPendingEntriesAndDoesNotLimitTopups() throws {
        var state = try fixture()
        state.pending = [pending(kind: "expense", amount: 50000)]
        state.draftAmount = "1500"
        var base = try XCTUnwrap(Reconciliation.projection(state, membershipID: "member", now: now))
        XCTAssertFalse(Reconciliation.draftExceedsBank(state, projection: base))
        state.draftAmount = "1500,01"
        XCTAssertTrue(Reconciliation.draftExceedsBank(state, projection: base))
        state.draftKind = "topup"
        XCTAssertFalse(Reconciliation.draftExceedsBank(state, projection: base))
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true)?.bank_minor, 300001)
        state.draftKind = "expense"
        state.pending = [pending(kind: "topup", amount: 10000)]
        base = try XCTUnwrap(Reconciliation.projection(state, membershipID: "member", now: now))
        state.draftAmount = "2100"
        XCTAssertFalse(Reconciliation.draftExceedsBank(state, projection: base))
        state.draftAmount = "2100,01"
        XCTAssertTrue(Reconciliation.draftExceedsBank(state, projection: base))
    }
    func testEmptyBankRejectsPositiveExpenseAndAllowsTopup() throws {
        var state = try fixture(expense: 200000)
        let base = try XCTUnwrap(Reconciliation.projection(state, membershipID: "member", now: now))
        XCTAssertEqual(base.bank_minor, 0)
        state.draftAmount = "0,01"
        XCTAssertTrue(Reconciliation.draftExceedsBank(state, projection: base))
        state.draftKind = "topup"
        XCTAssertFalse(Reconciliation.draftExceedsBank(state, projection: base))
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now, includingDraft: true)?.bank_minor, 1)
    }
    func testTodaysCarryDoesNotBecomeTheBaseAllowanceForEveryFutureDay() throws {
        let input = BudgetInput(initial_minor: 250000, start_date: "2026-10-19", end_date: "2026-10-30", as_of_date: "2026-10-20",
            days: [DayInput(day: "2026-10-19", topups_minor: 0, expenses_minor: 5000),
                   DayInput(day: "2026-10-20", topups_minor: 2000, expenses_minor: 15400)],
            choices: ["2026-10-20": "today"],
            revisions: [RevisionInput(effective_day: "2026-10-20", sequence: 1, bank_adjustment_minor: 0, end_date: "2026-11-14"),
                        RevisionInput(effective_day: "2026-10-20", sequence: 2, bank_adjustment_minor: 868400, end_date: "2026-11-18")])
        let base = try BudgetCalculation.project(input, topupsTodayFrom: "2026-10-20")
        XCTAssertEqual(base.bank_minor, 1100000)
        XCTAssertEqual(base.today_remaining_minor, 39017)
        XCTAssertEqual(base.days_remaining, 30)
        let afterExpense = BudgetInput(initial_minor: input.initial_minor, start_date: input.start_date,
            end_date: input.end_date, as_of_date: input.as_of_date,
            days: [input.days![0], DayInput(day: "2026-10-20", topups_minor: 2000, expenses_minor: 54900)],
            choices: input.choices, revisions: input.revisions)
        let result = try BudgetCalculation.project(afterExpense, topupsTodayFrom: "2026-10-20")
        XCTAssertEqual(result.today_remaining_minor, -483)
        XCTAssertEqual(result.bank_minor, 1060500)
        XCTAssertEqual(BudgetDisplay.nextDay(bank: result.bank_minor, days: result.days_remaining), 36568)
    }
    private func fixture(topup: Int64 = 0, expense: Int64 = 0, revision: Int64 = 1) throws -> LocalState {
        let input = BudgetInput(initial_minor: 200000, start_date: "2026-10-20", end_date: "2026-10-29", as_of_date: "2026-10-20",
                                days: [DayInput(day: "2026-10-20", topups_minor: topup, expenses_minor: expense)], choices: [:], revisions: [])
        let sprint = Sprint(id: "sprint", initial_minor: input.initial_minor, start_date: input.start_date,
                            end_date: input.end_date, initial_end_date: input.end_date, calculation_version: 3,
                            status: "active", topups_today_from: input.start_date)
        let snapshot = Snapshot(server_time: BusinessTime.timestamp(now), business_date: input.start_date, cabinet_revision: revision,
            cabinet: Cabinet(id: "cabinet", name: "Test", timezone: "Asia/Shanghai", revision: revision, members: []),
            sprint: sprint, daily_totals: input.days!, carry_decisions: [],
            projection: try BudgetCalculation.project(input, topupsTodayFrom: input.start_date), sprint_revisions: [])
        return LocalState(userID: "fixture", snapshot: snapshot)
    }
    private func pending(kind: String, amount: Int64) -> PendingCommand {
        PendingCommand(command: Command("add_entry", ["entry_id": .string("entry"), "sprint_id": .string("sprint"),
            "kind": .string(kind), "amount_minor": .integer(amount), "occurred_at": .string(BusinessTime.timestamp(now))], id: "command"),
            cabinetID: "cabinet", membershipID: "member", createdAt: now)
    }
}
