import Foundation
import Security

enum AppConfiguration {
    static func value(_ key: String) -> String { Bundle.main.object(forInfoDictionaryKey: key) as? String ?? "" }
    static var isSideload: Bool { value("CanDistribution") == "sideload" }
    static var pushEnabled: Bool { value("CanPushEnabled") != "NO" }
    private static let signedRights: [String: Any] = {
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url) else { return [:] }
        return SigningConfiguration.profileEntitlements(data) ?? [:]
    }()
    static var appGroup: String {
        if isSideload, let group = SigningConfiguration.appGroup(signedRights) { return group }
        return value("CanAppGroup")
    }
    static var keychainGroup: String { isSideload ? appGroup : value("CanKeychainGroup") }
    static var privateKeychainGroup: String {
        if isSideload, let group = signedRights["application-identifier"] as? String { return group }
        return value("CanPrivateKeychainGroup")
    }
    static var environment: String { "production" }
    static var baseURL: URL? {
        guard let url = URL(string: value("CanAPIBaseURL")), url.scheme == "https",
              let host = url.host, !host.hasSuffix(".invalid"), !host.contains("YOUR_"),
              url.user == nil, url.password == nil else { return nil }
        return url
    }
    static var issue: String? {
        guard baseURL != nil else { return "Адрес сервера Can ещё не настроен. Нужна подписанная сборка с адресом вашего сервера." }
        #if !targetEnvironment(simulator)
        if isSideload, SigningConfiguration.appGroup(signedRights) == nil || !(signedRights["application-identifier"] is String) {
            return "Подпишите приложение через SideStore, сохранив расширение виджета и общую группу."
        }
        #endif
        guard !appGroup.isEmpty, !appGroup.contains("YOUR_"), !keychainGroup.isEmpty, !keychainGroup.contains("$("),
              !privateKeychainGroup.isEmpty, !privateKeychainGroup.contains("$(") else {
            return "Общая группа приложения и виджета ещё не настроена."
        }
        return nil
    }
}

enum SigningConfiguration {
    // SideStore rewrites bundle/group identifiers. The installed profile is inside
    // the OS-validated app; only use it to select groups enforced by iOS itself.
    static func profileEntitlements(_ data: Data) -> [String: Any]? {
        guard data.count <= 2_000_000,
              let start = data.range(of: Data("<?xml".utf8)),
              let end = data.range(of: Data("</plist>".utf8), in: start.lowerBound..<data.endIndex),
              let profile = try? PropertyListSerialization.propertyList(from: data[start.lowerBound..<end.upperBound], format: nil) as? [String: Any] else { return nil }
        return profile["Entitlements"] as? [String: Any]
    }
    static func appGroup(_ rights: [String: Any]) -> String? {
        guard let groups = rights["com.apple.security.application-groups"] as? [String],
              groups.count == 1, groups[0].hasPrefix("group.") else { return nil }
        return groups[0]
    }
}

enum SecureStorage {
    static func query(_ account: String, shared: Bool) -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                               kSecAttrService as String: "can.production", kSecAttrAccount as String: account]
        q[kSecAttrAccessGroup as String] = shared ? AppConfiguration.keychainGroup : AppConfiguration.privateKeychainGroup
        return q
    }
    static func load<T: Decodable>(_ account: String, shared: Bool = false, as type: T.Type = T.self) throws -> T? {
        var q = query(account, shared: shared)
        q[kSecReturnData as String] = true; q[kSecMatchLimit as String] = kSecMatchLimitOne
        var value: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &value)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = value as? Data else {
            throw APIError.local("Защищённое хранилище недоступно (\(status)). Разблокируйте iPhone.")
        }
        return try JSONDecoder().decode(type, from: data)
    }
    static func save<T: Encodable>(_ value: T, account: String, shared: Bool = false) throws {
        let q = query(account, shared: shared)
        let fields: [String: Any] = [kSecValueData as String: try JSONEncoder().encode(value),
                                     kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(q as CFDictionary, fields as CFDictionary)
        if status == errSecItemNotFound {
            var item = q; fields.forEach { item[$0] = $1 }
            let added = SecItemAdd(item as CFDictionary, nil)
            guard added == errSecSuccess else { throw APIError.local("Не удалось сохранить ключ (\(added)).") }
        } else if status != errSecSuccess { throw APIError.local("Не удалось обновить ключ (\(status)).") }
    }
    static func remove(_ account: String, shared: Bool = false) throws {
        let status = SecItemDelete(query(account, shared: shared) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw APIError.local("Не удалось удалить ключ (\(status)).")
        }
    }
}

struct SharedWidgetState: Codable {
    var formatVersion = 1
    var cabinetID: String
    var revision: Int64
    var businessDate: String
    var calculatedAt: Date
    var lastSyncAt: Date
    var todayMinor: Int64?
    var bankMinor: Int64?
    var pendingCount: Int
    var carryPending: Bool
    var status: String
    var daysRemaining: Int? = nil
    var isOffline: Bool? = nil
}
enum SharedWidgetStorage {
    static func url(_ file: String = "snapshot.json") -> URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: AppConfiguration.appGroup)?.appendingPathComponent(file)
    }
    static func load() -> SharedWidgetState? {
        guard let url = url(), let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(SharedWidgetState.self, from: data)
    }
    static func save(_ state: SharedWidgetState) throws {
        guard let url = url() else { throw APIError.local("Общее хранилище виджета недоступно.") }
        try JSONEncoder().encode(state).write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
    static func clear() throws {
        for name in ["snapshot.json", "widget-network-cache.json"] {
            if let url = url(name), FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
        }
        try SecureStorage.remove("widget-read", shared: true)
    }
}
