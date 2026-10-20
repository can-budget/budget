import XCTest
@testable import Can

final class AccountDeletionTests: XCTestCase {
    private let userID = "00000000-0000-0000-0000-000000000001"
    private let otherUserID = "00000000-0000-0000-0000-000000000002"
    private let commandID = "00000000-0000-0000-0000-000000000003"
    private let receipt = DeletionAccepted(job_id: "00000000-0000-0000-0000-000000000004", status: "accepted",
        primary_deletion_deadline: "2026-10-14T12:00:00Z", backup_retention_days: 30)

    func testUnconfirmedDeletionPersistsTheExactImmutableReplayRequest() throws {
        let intent = try AccountDeletionIntent(userID: userID, identityID: "apple-subject", reauthToken: "original-proof", commandID: commandID)
        let restored = try JSONDecoder().decode(AccountDeletionIntent.self, from: JSONEncoder().encode(intent))
        XCTAssertEqual(restored, intent)
        XCTAssertEqual(restored.body, ["command_id": .string(commandID), "confirm_shared_history_changes": .bool(true)])
        XCTAssertEqual(restored.replayProofs, ["original-proof"])
        XCTAssertFalse(restored.needsCleanup)
        XCTAssertNil(restored.accepted)
    }
    func testFreshReauthenticationPreservesOriginalProofForConcurrentLostAcceptedResponse() throws {
        let initial = try AccountDeletionIntent(userID: userID, identityID: "apple-subject", reauthToken: "original-proof", commandID: commandID)
        let retried = try initial.replacingAuthorization("fresh-proof")
        let restored = try JSONDecoder().decode(AccountDeletionIntent.self, from: JSONEncoder().encode(retried))
        XCTAssertEqual(restored.commandID, initial.commandID)
        XCTAssertEqual(restored.body, initial.body)
        XCTAssertEqual(restored.replayProofs, ["original-proof", "fresh-proof"])
        XCTAssertFalse(restored.needsCleanup)
    }
    func testAcceptedMarkerRetainsCleanupOwnerButErasesEveryReplayCredential() throws {
        let pending = try AccountDeletionIntent(userID: userID, identityID: "apple-subject", reauthToken: "original-proof", commandID: commandID)
            .replacingAuthorization("fresh-proof")
        let accepted = try pending.accepting(receipt)
        let bytes = try JSONEncoder().encode(accepted)
        let recovered = try JSONDecoder().decode(AccountDeletionIntent.self, from: bytes)
        XCTAssertTrue(recovered.needsCleanup)
        XCTAssertEqual(recovered.userID, userID)
        XCTAssertEqual(recovered.accepted, receipt)
        XCTAssertTrue(recovered.replayProofs.isEmpty)
        XCTAssertFalse(String(decoding: bytes, as: UTF8.self).contains("original-proof"))
        XCTAssertFalse(String(decoding: bytes, as: UTF8.self).contains("fresh-proof"))
        XCTAssertThrowsError(try recovered.replacingAuthorization("another-proof"))
    }
    func testMalformedAcceptedResponseCannotAuthorizeLocalErasure() throws {
        let intent = try AccountDeletionIntent(userID: userID, identityID: "apple-subject", reauthToken: "proof", commandID: commandID)
        let invalid = [
            DeletionAccepted(job_id: "invalid", status: "accepted", primary_deletion_deadline: "2026-10-14T12:00:00Z", backup_retention_days: 30),
            DeletionAccepted(job_id: receipt.job_id, status: "unknown", primary_deletion_deadline: receipt.primary_deletion_deadline, backup_retention_days: 30),
            DeletionAccepted(job_id: receipt.job_id, status: "accepted", primary_deletion_deadline: "not-a-date", backup_retention_days: 30),
            DeletionAccepted(job_id: receipt.job_id, status: "accepted", primary_deletion_deadline: receipt.primary_deletion_deadline, backup_retention_days: -1),
        ]
        for response in invalid { XCTAssertThrowsError(try intent.accepting(response)) }
        XCTAssertFalse(intent.needsCleanup)
        XCTAssertEqual(intent.replayProofs, ["proof"])
    }
    func testExplicitAuthenticatedDeletionEvidenceHasNoReplayAuthority() throws {
        let evidence = try AccountDeletionIntent.confirmedDeletion(userID: userID, identityID: "apple-subject")
        XCTAssertTrue(evidence.needsCleanup)
        XCTAssertTrue(evidence.replayProofs.isEmpty)
        XCTAssertNil(evidence.accepted)
        XCTAssertEqual(evidence.confirmedByLogin, true)
        XCTAssertThrowsError(try evidence.replacingAuthorization("proof"))
    }
    func testScopedFileCleanupIsIdempotentAndPreservesAnotherAccountsQueue() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let first = try LocalStorage.url(userID, directory: directory)
        let other = try LocalStorage.url(otherUserID, directory: directory)
        let firstState = LocalState(userID: userID, draftAmount: "123,45")
        let otherState = LocalState(userID: otherUserID, draftAmount: "987,65")
        try JSONEncoder().encode(firstState).write(to: first, options: .atomic)
        let orphan = first.deletingLastPathComponent().appendingPathComponent(".interrupted-atomic-write")
        try Data("private temporary".utf8).write(to: orphan)
        let otherBytes = try JSONEncoder().encode(otherState)
        try otherBytes.write(to: other, options: .atomic)
        // No response or a 401 never invokes this cleanup primitive; the caller needs a durable accepted marker.
        XCTAssertTrue(FileManager.default.fileExists(atPath: first.path))
        try LocalStorage.remove(userID, directory: directory)
        try LocalStorage.remove(userID, directory: directory)
        XCTAssertFalse(FileManager.default.fileExists(atPath: first.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: orphan.path))
        XCTAssertEqual(try Data(contentsOf: other), otherBytes)
        XCTAssertThrowsError(try LocalStorage.remove("../another-user", directory: directory))
        XCTAssertEqual(try Data(contentsOf: other), otherBytes)
    }
    func testLegacyFlatStateMovesIntoOwnerDirectoryAndDeletionRemovesBothFormats() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let legacy = directory.appendingPathComponent("production-\(userID).json")
        let bytes = try JSONEncoder().encode(LocalState(userID: userID, draftAmount: "12,34"))
        try bytes.write(to: legacy, options: .atomic)
        let migrated = try LocalStorage.url(userID, directory: directory)
        XCTAssertEqual(try Data(contentsOf: migrated), bytes)
        XCTAssertFalse(FileManager.default.fileExists(atPath: legacy.path))
        try bytes.write(to: legacy)
        try LocalStorage.remove(userID, directory: directory)
        XCTAssertFalse(FileManager.default.fileExists(atPath: legacy.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: migrated.deletingLastPathComponent().path))
    }
    func testNilAuthorDoesNotInventAnIdentityOrClaimDeletion() {
        XCTAssertEqual(HistoryIdentity.displayName(nil), "Без имени")
        XCTAssertEqual(HistoryIdentity.displayName("  "), "Без имени")
        XCTAssertEqual(HistoryIdentity.displayName("Ира"), "Ира")
        let members = [Member(user_id: userID, display_name: "Макс", joined_at: "2026-10-11T12:00:00Z", membership_id: nil)]
        XCTAssertEqual(HistoryIdentity.displayName(nil, id: userID, members: members), "Макс")
        XCTAssertEqual(HistoryIdentity.displayName(nil, id: otherUserID, members: members), "Без имени")
    }
    func testLocalIdentityLookupUsesDistinctHashedAppleSubjects() {
        XCTAssertNotEqual(LocalAccountIdentity.key("subject-a"), LocalAccountIdentity.key("subject-b"))
        XCTAssertFalse(LocalAccountIdentity.key("private-subject").contains("private-subject"))
    }
    func testPreviousLocalCleanupRequiresSameFreshIdentityAndCanNeverTargetCurrentUser() throws {
        XCTAssertNoThrow(try PreviousAccountCleanup.validate(previousUserID: userID, currentUserID: otherUserID,
            expectedIdentityID: "same-apple", verifiedIdentityID: "same-apple", knownPreviousIDs: [userID]))
        XCTAssertThrowsError(try PreviousAccountCleanup.validate(previousUserID: otherUserID, currentUserID: otherUserID,
            expectedIdentityID: "same-apple", verifiedIdentityID: "same-apple", knownPreviousIDs: [otherUserID]))
        XCTAssertThrowsError(try PreviousAccountCleanup.validate(previousUserID: userID, currentUserID: otherUserID,
            expectedIdentityID: "same-apple", verifiedIdentityID: "different-apple", knownPreviousIDs: [userID]))
        XCTAssertThrowsError(try PreviousAccountCleanup.validate(previousUserID: userID, currentUserID: otherUserID,
            expectedIdentityID: "same-apple", verifiedIdentityID: nil, knownPreviousIDs: [userID]))
        XCTAssertThrowsError(try PreviousAccountCleanup.validate(previousUserID: userID, currentUserID: otherUserID,
            expectedIdentityID: "same-apple", verifiedIdentityID: "same-apple", knownPreviousIDs: []))
    }
    func testDeletingPreviousIncarnationCannotClearReplacementSessionAppleIdentityOrWidget() {
        XCTAssertFalse(AccountDeletionCleanupScope.clearsSharedCredentials(deletingUserID: userID, currentUserID: otherUserID))
        XCTAssertTrue(AccountDeletionCleanupScope.clearsSharedCredentials(deletingUserID: userID, currentUserID: userID))
        XCTAssertTrue(AccountDeletionCleanupScope.clearsSharedCredentials(deletingUserID: userID, currentUserID: nil))
    }
}
