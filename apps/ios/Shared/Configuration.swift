import Foundation
import Security

enum AppConfiguration {
    static func value(_ key: String) -> String { Bundle.main.object(forInfoDictionaryKey: key) as? String ?? "" }
    static var appGroup: String { value("CanAppGroup") }
    static var keychainGroup: String { value("CanKeychainGroup") }
    static var privateKeychainGroup: String { value("CanPrivateKeychainGroup") }
    static var environment: String { "production" }
    static var baseURL: URL? {
        guard let url = URL(string: value("CanAPIBaseURL")), url.scheme == "https",
              let host = url.host, !host.hasSuffix(".invalid"), !host.contains("YOUR_"),
              url.user == nil, url.password == nil else { return nil }
        return url
    }
    static var issue: String? {
        guard baseURL != nil else { return "Адрес сервера Can ещё не настроен. Нужна подписанная сборка с адресом вашего сервера." }
        guard !appGroup.isEmpty, !appGroup.contains("YOUR_"), !keychainGroup.isEmpty, !keychainGroup.contains("$("),
              !privateKeychainGroup.isEmpty, !privateKeychainGroup.contains("$(") else {
            return "Общая группа приложения и виджета ещё не настроена."
        }
        return nil
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
