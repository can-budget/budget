import XCTest
@testable import Can

final class BudgetAndSyncTests: XCTestCase {
    private struct GoldenCase: Decodable { let name: String; let input: BudgetInput; let expected: Projection }
    private let now = BusinessTime.parse("2026-10-11T04:00:00.500Z")!

    func testAll240AuthoritativeCalculationFixtures() throws {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "golden_cases", withExtension: "json"))
        let fixtures = try JSONDecoder().decode([GoldenCase].self, from: Data(contentsOf: url))
        XCTAssertEqual(fixtures.count, 240)
        for fixture in fixtures { XCTAssertEqual(try BudgetEngine.project(fixture.input), fixture.expected, fixture.name) }
    }
    func testMoneyUsesIntegerCentsAndRejectsInvalidOrOverflowInput() {
        XCTAssertEqual(Money.parse("12,34"), 1234)
        XCTAssertEqual(Money.parse("12.3"), 1230)
        XCTAssertEqual(Money.parse("-0,01", signed: true), -1)
        XCTAssertEqual(Money.parse("9999999,99"), 999_999_999)
        for value in ["", ".", "-1", "1,234", "1e3", "١٢", "NaN", "10000000", "9223372036854775807"] {
            XCTAssertNil(Money.parse(value), value)
        }
        XCTAssertEqual(BudgetEngine.floorDiv(-1, 3), -1)
        XCTAssertEqual(BudgetEngine.floorDiv(-4, 3), -2)
    }
    func testTimestampPreservesSubsecondMembershipOrderingAndShanghaiMidnight() throws {
        let encoded = BusinessTime.timestamp(now)
        XCTAssertEqual(try XCTUnwrap(BusinessTime.parse(encoded)).timeIntervalSince1970, now.timeIntervalSince1970, accuracy: 0.001)
        XCTAssertTrue(encoded.contains(".500"))
        XCTAssertEqual(BusinessTime.day(try XCTUnwrap(BusinessTime.parse("2026-10-11T16:00:00Z"))), "2026-10-12")
    }
    func testLostCommandResponseIsRemovedOnlyWithCoherentReceiptAndSnapshot() throws {
        var state = LocalState(userID: "owner", snapshot: try snapshot())
        state.pending = [pending("first", state: "unknown")]
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now)?.today_remaining_minor, 9_000)
        let response = SyncResponse(snapshot: try snapshot(revision: 2, expense: 1_000), receipts: [applied("first", revision: 2)])
        let settled = try Reconciliation.apply(response, requestedIDs: ["first"], to: state)
        XCTAssertTrue(settled.pending.isEmpty)
        XCTAssertEqual(try Reconciliation.projection(settled, membershipID: "member", now: now)?.today_remaining_minor, 9_000)
    }
    func testResponsePreservesPurchaseEnqueuedWhileRequestWasInFlight() throws {
        var latest = LocalState(userID: "owner", snapshot: try snapshot())
        latest.pending = [pending("first", state: "unknown"), pending("second")]
        let settled = try Reconciliation.apply(SyncResponse(snapshot: try snapshot(revision: 2, expense: 1_000),
                                                            receipts: [applied("first", revision: 2)]), requestedIDs: ["first"], to: latest)
        XCTAssertEqual(settled.pending.map(\.id), ["second"])
        XCTAssertEqual(try Reconciliation.projection(settled, membershipID: "member", now: now)?.today_remaining_minor, 8_000)
    }
    func testAcknowledgedCommandDoesNotDoubleOverlayCommittedSnapshot() throws {
        var state = LocalState(userID: "owner", snapshot: try snapshot(revision: 2, expense: 1_000))
        var item = pending("first", state: "acknowledged"); item.resultRevision = 2; state.pending = [item]
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now)?.today_remaining_minor, 9_000)
        state.snapshot = try snapshot(revision: 1)
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now)?.today_remaining_minor, 9_000)
    }
    func testOldMembershipPendingAndRejectedEntriesNeverOverlayCurrentBudget() throws {
        var state = LocalState(userID: "owner", snapshot: try snapshot())
        state.pending = [pending("old", membership: "previous"), pending("rejected", state: "needs_attention")]
        XCTAssertEqual(try Reconciliation.projection(state, membershipID: "member", now: now)?.today_remaining_minor, 10_000)
    }
    func testRegressingRevisionOrBusinessDateCannotReplaceSnapshot() throws {
        let current = try snapshot(revision: 5, day: "2026-10-12")
        XCTAssertFalse(Reconciliation.accepts(try snapshot(revision: 4, day: "2026-10-12"), over: current))
        XCTAssertFalse(Reconciliation.accepts(try snapshot(revision: 6, day: "2026-10-11"), over: current))
        XCTAssertTrue(Reconciliation.accepts(try snapshot(revision: 5, day: "2026-10-13"), over: current))
    }
    func testIncompleteDuplicateAndContradictoryReceiptsCannotDeleteQueue() throws {
        var state = LocalState(userID: "owner", snapshot: try snapshot())
        state.pending = [pending("first", state: "unknown")]
        let valid = applied("first", revision: 2)
        let inconsistent = Receipt(command_id: "first", status: "applied", result_revision: 2, result: valid.result, error: .local("unexpected"))
        let wrongID = Receipt(command_id: "first", status: "applied", result_revision: 2, result: applied("another", revision: 2).result, error: nil)
        let noResult = Receipt(command_id: "first", status: "applied", result_revision: 2, result: nil, error: nil)
        let unknownWithResult = Receipt(command_id: "first", status: "unknown", result_revision: nil, result: valid.result, error: nil)
        let rejectedWithoutError = Receipt(command_id: "first", status: "rejected", result_revision: nil, result: nil, error: nil)
        for receipts in [[], [valid, valid], [inconsistent], [wrongID], [noResult], [unknownWithResult], [rejectedWithoutError]] {
            XCTAssertThrowsError(try Reconciliation.apply(SyncResponse(snapshot: try snapshot(revision: 2), receipts: receipts), requestedIDs: ["first"], to: state))
            XCTAssertEqual(state.pending.map(\.id), ["first"])
            XCTAssertEqual(state.snapshot?.cabinet_revision, 1)
        }
        XCTAssertThrowsError(try Reconciliation.apply(SyncResponse(snapshot: try snapshot(), receipts: [valid]), requestedIDs: ["first"], to: state))
    }
    func testRecoveryKeepsEntryIdentityAndRequiresTerminalEligibleReceipt() throws {
        var original = pending("rejected", state: "needs_attention")
        original.error = APIError(code: "clock_skew", message: "clock_skew", retryable: false)
        let replacement = try original.recovery(correctedDate: now.addingTimeInterval(-60), commandID: "retry")
        XCTAssertEqual(replacement.id, "retry")
        XCTAssertEqual(replacement.command.payload["entry_id"], original.command.payload["entry_id"])
        XCTAssertEqual(replacement.command.payload["membership_id"], original.command.payload["membership_id"])
        XCTAssertEqual(replacement.command.payload["recovery_of_command_id"], .string("rejected"))
        XCTAssertEqual(replacement.command.payload["original_occurred_at"], original.command.payload["occurred_at"])
        XCTAssertEqual(replacement.command.payload["allow_completed"], .bool(true))
        original.state = "unknown"
        XCTAssertThrowsError(try original.recovery(commandID: "retry"))
        original.state = "needs_attention"
        original.error = APIError(code: "membership_epoch_mismatch", message: "membership_epoch_mismatch", retryable: false)
        XCTAssertThrowsError(try original.recovery(commandID: "retry"))
    }
    func testDurableStateRoundTripKeepsImmutableCommandsAndOnlineIntentScope() throws {
        var state = LocalState(userID: "owner", snapshot: try snapshot())
        state.pending = [pending("first", state: "unknown")]
        state.onlineIntents["carry"] = OnlineIntent(cabinetID: "cabinet", membershipID: "member", command: Command("choose_carry", ["mode": .string("today")], id: "carry-id"))
        state.draftAmount = "12,34"
        let restored = try JSONDecoder().decode(LocalState.self, from: JSONEncoder().encode(state))
        XCTAssertEqual(restored.pending.first?.command, state.pending.first?.command)
        XCTAssertEqual(restored.pending.first?.state, "unknown")
        XCTAssertEqual(restored.onlineIntents["carry"]?.membershipID, "member")
        XCTAssertEqual(restored.onlineIntents["carry"]?.command.command_id, "carry-id")
        XCTAssertEqual(restored.draftAmount, "12,34")
    }
    func testCreationAuditAcceptsNullBeforeState() throws {
        let data = Data(#"{"items":[{"entry_revision":1,"actor_name":"Ира","before_state":null,"after_state":{"amount_minor":123},"created_at":"2026-10-11T04:00:00Z"}]}"#.utf8)
        let audit = try JSONDecoder().decode(EntryAuditPage.self, from: data)
        XCTAssertNil(audit.items.first?.before_state)
        XCTAssertEqual(audit.items.first?.after_state?["amount_minor"], .integer(123))
    }
    func testExplicitRestorePreservesQueueAndRequiresReviewOfLostAcknowledgement() throws {
        var state = LocalState(userID: "owner", snapshot: try snapshot(revision: 10))
        var acknowledged = pending("accepted", state: "acknowledged"); acknowledged.resultRevision = 10
        state.pending = [acknowledged, pending("unknown", state: "unknown")]
        state.onlineIntents["carry"] = OnlineIntent(cabinetID: "cabinet", membershipID: "member", command: Command("choose_carry", ["mode": .string("today")], id: "carry-id"))
        state.lastSyncAt = now
        let prepared = Reconciliation.prepareForServerRestore(state)
        XCTAssertNil(prepared.snapshot)
        XCTAssertNil(prepared.lastSyncAt)
        XCTAssertEqual(prepared.pending.map(\.command), state.pending.map(\.command))
        XCTAssertEqual(prepared.pending.map(\.state), ["acknowledged", "unknown"])
        XCTAssertEqual(prepared.onlineIntents["carry"]?.command, state.onlineIntents["carry"]?.command)
        let receipts = prepared.pending.map { Receipt(command_id: $0.id, status: "unknown", result_revision: nil, result: nil, error: nil) }
        let reconciled = try Reconciliation.apply(SyncResponse(snapshot: try snapshot(revision: 1), receipts: receipts), requestedIDs: ["accepted", "unknown"], to: prepared)
        XCTAssertEqual(reconciled.pending.first?.state, "restore_review")
        XCTAssertEqual(reconciled.pending.first?.command, acknowledged.command)
        XCTAssertEqual(reconciled.pending.last?.state, "unknown")
        XCTAssertEqual(try Reconciliation.projection(reconciled, membershipID: "member", now: now)?.today_remaining_minor, 9_000)
    }
    func testInvitationScannerRejectsForeignEnvironmentAndURLs() throws {
        XCTAssertEqual(try InvitationCode.normalize("abcd-efgh 12"), "ABCDEFGH12")
        XCTAssertEqual(try InvitationCode.fromQR("can-invite:v1:production:ABCDEFGH12"), "ABCDEFGH12")
        for value in ["https://example.com", "can-invite:v1:staging:ABCDEFGH12", "can-invite:v2:production:ABCDEFGH12", "can-invite:v1:production:ABCDEI0123", "can-invite:v1:production:ABCDEFGH12:extra"] {
            XCTAssertThrowsError(try InvitationCode.fromQR(value), value)
        }
    }
    private func pending(_ id: String, state: String = "queued", membership: String = "member") -> PendingCommand {
        PendingCommand(command: Command("add_entry", ["entry_id": .string("entry-" + id), "sprint_id": .string("sprint"),
            "membership_id": .string(membership), "kind": .string("expense"), "amount_minor": .integer(1_000),
            "occurred_at": .string(BusinessTime.timestamp(now)), "allow_completed": .bool(false)], id: id),
            cabinetID: "cabinet", membershipID: membership, createdAt: now, state: state)
    }
    private func applied(_ id: String, revision: Int64) -> Receipt {
        Receipt(command_id: id, status: "applied", result_revision: revision,
                result: CommandResult(command_id: id, status: "applied", result_revision: revision, entity_id: "entry-" + id, invitation: nil), error: nil)
    }
    private func snapshot(revision: Int64 = 1, expense: Int64 = 0, day: String = "2026-10-11") throws -> Snapshot {
        let input = BudgetInput(initial_minor: 100_000, start_date: "2026-10-11", end_date: "2026-10-20", as_of_date: day,
                                days: [DayInput(day: "2026-10-11", topups_minor: 0, expenses_minor: expense)], choices: [:], revisions: [])
        return Snapshot(server_time: BusinessTime.timestamp(now), business_date: day, cabinet_revision: revision,
                        cabinet: Cabinet(id: "cabinet", name: "Test", timezone: "Asia/Shanghai", revision: revision, members: []),
                        sprint: Sprint(id: "sprint", initial_minor: 100_000, start_date: "2026-10-11", end_date: "2026-10-20", initial_end_date: "2026-10-20", calculation_version: 2, status: "active"),
                        daily_totals: input.days!, carry_decisions: [], projection: try BudgetEngine.project(input), sprint_revisions: [])
    }
}
