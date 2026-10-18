import Foundation
import CryptoKit

struct DeletionAccepted: Codable, Equatable {
    let job_id: String
    let status: String
    let primary_deletion_deadline: String
    let backup_retention_days: Int

    func validate() throws {
        guard UUID(uuidString: job_id) != nil, status == "accepted",
              BusinessTime.parse(primary_deletion_deadline) != nil, backup_retention_days == 30 else {
            throw APIError.local("Не удалось подтвердить принятие удаления. Запрос сохранён; проверьте его результат ещё раз.")
        }
    }
}

struct AccountDeletionIntent: Codable, Equatable {
    let userID: String
    let appleUserID: String
    let commandID: String
    var reauthToken: String?
    var priorReauthTokens: [String]?
    var accepted: DeletionAccepted?
    var confirmedByAppleLogin: Bool?

    init(userID: String, appleUserID: String, reauthToken: String,
         commandID: String = UUID().uuidString.lowercased()) throws {
        guard UUID(uuidString: userID) != nil, UUID(uuidString: commandID) != nil,
              !appleUserID.isEmpty, !reauthToken.isEmpty else {
            throw APIError.local("Не удалось подготовить подтверждение удаления.")
        }
        self.userID = userID; self.appleUserID = appleUserID
        self.commandID = commandID; self.reauthToken = reauthToken
    }
    var body: [String: JSONValue] {
        ["command_id": .string(commandID), "confirm_shared_history_changes": .bool(true)]
    }
    var needsCleanup: Bool { accepted != nil || confirmedByAppleLogin == true }
    var replayProofs: [String] { (priorReauthTokens ?? []) + (reauthToken.map { [$0] } ?? []) }
    static func confirmedDeletion(userID: String, appleUserID: String) throws -> Self {
        try Self(confirmedUserID: userID, appleUserID: appleUserID)
    }
    private init(confirmedUserID: String, appleUserID: String) throws {
        guard UUID(uuidString: confirmedUserID) != nil, !appleUserID.isEmpty else {
            throw APIError.local("Не удалось определить локальные данные удаляемого аккаунта.")
        }
        userID = confirmedUserID; self.appleUserID = appleUserID
        commandID = UUID().uuidString.lowercased()
        reauthToken = nil; confirmedByAppleLogin = true
    }
    func accepting(_ response: DeletionAccepted) throws -> Self {
        try response.validate()
        var next = self
        next.accepted = response
        next.reauthToken = nil
        next.priorReauthTokens = nil
        return next
    }
    func replacingAuthorization(_ token: String) throws -> Self {
        guard !needsCleanup, !token.isEmpty else { throw APIError.local("Удаление уже принято сервером.") }
        var next = self
        if let current = reauthToken, current != token, !(priorReauthTokens ?? []).contains(current) {
            next.priorReauthTokens = (priorReauthTokens ?? []) + [current]
        }
        next.reauthToken = token
        return next
    }
}

enum LocalAccountIdentity {
    static func key(_ appleUserID: String) -> String {
        "local-user-" + SHA256.hash(data: Data(appleUserID.utf8)).map { String(format: "%02x", $0) }.joined()
    }
    static func save(userID: String, appleUserID: String) throws { try SecureStorage.save(userID, account: key(appleUserID)) }
    static func load(_ appleUserID: String) throws -> String? { try SecureStorage.load(key(appleUserID)) }
    static func previous(_ appleUserID: String) throws -> [String] {
        try SecureStorage.load("previous-" + key(appleUserID)) ?? []
    }
    static func rememberPrevious(userID: String, appleUserID: String) throws {
        guard UUID(uuidString: userID) != nil else { throw APIError.local("Некорректный прежний аккаунт.") }
        var ids = try previous(appleUserID)
        if !ids.contains(userID) { ids.append(userID) }
        try SecureStorage.save(ids, account: "previous-" + key(appleUserID))
    }
    static func removePrevious(userID: String, appleUserID: String) throws {
        let ids = try previous(appleUserID).filter { $0 != userID }
        if ids.isEmpty { try SecureStorage.remove("previous-" + key(appleUserID)) }
        else { try SecureStorage.save(ids, account: "previous-" + key(appleUserID)) }
    }
    static func remove(userID: String, appleUserID: String) throws {
        if try load(appleUserID) == userID { try SecureStorage.remove(key(appleUserID)) }
        try removePrevious(userID: userID, appleUserID: appleUserID)
    }
}

enum DeletionStorage {
    static let account = "account-deletion-intent"
    static func load() throws -> AccountDeletionIntent? { try SecureStorage.load(account) }
    static func save(_ intent: AccountDeletionIntent) throws { try SecureStorage.save(intent, account: account) }
    static func clear() throws { try SecureStorage.remove(account) }
    static func archive(_ intent: AccountDeletionIntent) throws { try SecureStorage.save(intent, account: "previous-deletion-" + intent.userID) }
    static func archived(_ userID: String) throws -> AccountDeletionIntent? { try SecureStorage.load("previous-deletion-" + userID) }
    static func clearArchive(_ userID: String) throws { try SecureStorage.remove("previous-deletion-" + userID) }
}

struct PreviousLocalAccount: Identifiable {
    let id: String
    let pendingCount: Int?
    let hasDeletionRequest: Bool
}

enum PreviousAccountCleanup {
    static func validate(previousUserID: String, currentUserID: String, expectedAppleUserID: String,
                         verifiedAppleUserID: String?, knownPreviousIDs: [String]) throws {
        guard UUID(uuidString: previousUserID) != nil, previousUserID != currentUserID,
              verifiedAppleUserID == expectedAppleUserID, knownPreviousIDs.contains(previousUserID) else {
            throw APIError.local("Подтвердите текущий Apple Account. Данные действующего аккаунта удаляться не будут.")
        }
    }
}

enum AccountDeletionCleanupScope {
    static func clearsSharedCredentials(deletingUserID: String, currentUserID: String?) -> Bool {
        currentUserID == nil || currentUserID == deletingUserID
    }
}

enum HistoryIdentity {
    static func displayName(_ name: String?, id: String? = nil, members: [Member] = []) -> String {
        if let name, !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return name }
        if let id, let member = members.first(where: { $0.user_id == id }) { return member.display_name }
        return "Без имени"
    }
}
