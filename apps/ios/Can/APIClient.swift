import Foundation

struct EmptyResponse: Decodable {}
struct Credentials: Codable {
    var user: User
    var tokens: SessionTokens
    var refreshRequestID: String?
}

actor APIClient {
    private var credentials: Credentials?
    private var refreshTask: Task<SessionTokens, Error>?
    private var generation = UUID()
    private let session: URLSession

    init() {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 20
        config.timeoutIntervalForResource = 30
        config.waitsForConnectivity = false
        session = URLSession(configuration: config)
    }
    func restore() throws -> User? {
        credentials = try SecureStorage.load("session")
        return credentials?.user
    }
    func install(_ result: LoginResult) throws {
        let next = Credentials(user: result.user, tokens: result.session)
        try SecureStorage.save(next, account: "session")
        generation = UUID(); refreshTask?.cancel(); refreshTask = nil
        credentials = next
    }
    func updateUser(_ user: User) throws {
        guard var next = credentials else { return }
        guard next.user.id == user.id else { throw CancellationError() }
        next.user = user
        try SecureStorage.save(next, account: "session")
        credentials = next
    }
    func forget() throws {
        try SecureStorage.remove("session")
        generation = UUID(); refreshTask?.cancel(); refreshTask = nil
        credentials = nil
    }
    func invalidateRequests() {
        generation = UUID(); refreshTask?.cancel(); refreshTask = nil
    }
    func forget(userID: String) throws {
        let stored: Credentials? = try SecureStorage.load("session")
        if stored?.user.id == userID { try SecureStorage.remove("session") }
        if credentials?.user.id == userID {
            invalidateRequests()
            credentials = nil
        }
    }
    func request<T: Decodable>(_ path: String, method: String = "GET", body: Data? = nil,
                               authenticated: Bool = true, headers: [String: String] = [:], expectedStatus: Int? = nil) async throws -> T {
        let generation = self.generation
        if authenticated {
            guard let current = credentials else { throw APIError(code: "unauthorized", message: "Войдите через Apple. Очередь сохранена.", retryable: false) }
            if (BusinessTime.parse(current.tokens.access_expires_at) ?? .distantPast) < Date().addingTimeInterval(30) {
                try await refresh()
            }
        }
        if authenticated && generation != self.generation { throw CancellationError() }
        do {
            let result: T = try await perform(path, method: method, body: body, authenticated: authenticated, headers: headers, expectedStatus: expectedStatus)
            if authenticated && generation != self.generation { throw CancellationError() }
            return result
        }
        catch let error as APIError where error.code == "session_expired" || error.code == "unauthorized" || error.code == "invalid_access_token" {
            guard authenticated else { throw error }
            try await refresh()
            guard generation == self.generation else { throw CancellationError() }
            let result: T = try await perform(path, method: method, body: body, authenticated: authenticated, headers: headers, expectedStatus: expectedStatus)
            guard generation == self.generation else { throw CancellationError() }
            return result
        }
    }
    private func perform<T: Decodable>(_ path: String, method: String, body: Data?, authenticated: Bool,
                                       headers: [String: String], expectedStatus: Int? = nil) async throws -> T {
        guard let base = AppConfiguration.baseURL,
              let url = URL(string: path, relativeTo: base)?.absoluteURL, url.host == base.host else {
            throw APIError.local("Сервер приложения ещё не настроен.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method; request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if authenticated, let c = credentials { request.setValue("Bearer \(c.tokens.access_token)", forHTTPHeaderField: "Authorization") }
        headers.forEach { request.setValue($1, forHTTPHeaderField: $0) }
        let requestGeneration = generation
        let (data, response) = try await session.data(for: request)
        if authenticated && requestGeneration != generation { throw CancellationError() }
        guard let http = response as? HTTPURLResponse else { throw APIError.local("Не получен ответ сервера.") }
        guard (200..<300).contains(http.statusCode) else {
            if var error = try? JSONDecoder().decode(APIError.self, from: data) {
                if http.statusCode == 401 { error.code = "unauthorized" }
                throw error
            }
            throw APIError(code: http.statusCode == 401 ? "unauthorized" : "http_\(http.statusCode)",
                           message: "Сервер вернул ошибку \(http.statusCode). Данные на телефоне сохранены.",
                           retryable: http.statusCode >= 500 || http.statusCode == 429)
        }
        if let expectedStatus, http.statusCode != expectedStatus {
            throw APIError.local("Сервер не подтвердил ожидаемый результат. Сохранённый запрос можно проверить повторно.")
        }
        return try JSONDecoder().decode(T.self, from: data.isEmpty ? Data("{}".utf8) : data)
    }
    private func refresh() async throws {
        if let refreshTask { _ = try await refreshTask.value; return }
        guard var current = credentials else { throw APIError.local("Нужно снова войти через Apple.") }
        if current.refreshRequestID == nil {
            current.refreshRequestID = UUID().uuidString.lowercased()
            // Save the request identifier before consuming the refresh credential.
            try SecureStorage.save(current, account: "session")
            credentials = current
        }
        let payload: [String: JSONValue] = ["refresh_token": .string(current.tokens.refresh_token),
                                           "refresh_request_id": .string(current.refreshRequestID!)]
        let data = try JSONEncoder().encode(payload)
        let startedGeneration = generation
        let original = current
        let task = Task<SessionTokens, Error> { try await self.rotate(data, original: original, generation: startedGeneration) }
        refreshTask = task
        defer { if generation == startedGeneration { refreshTask = nil } }
        _ = try await task.value
    }
    private func rotate(_ data: Data, original: Credentials, generation started: UUID) async throws -> SessionTokens {
        let tokens: SessionTokens = try await perform("/v1/auth/refresh", method: "POST", body: data, authenticated: false, headers: [:])
        guard generation == started, credentials?.tokens.refresh_token == original.tokens.refresh_token else {
            throw CancellationError()
        }
        var next = original
        next.tokens = tokens; next.refreshRequestID = nil
        try SecureStorage.save(next, account: "session")
        credentials = next
        return tokens
    }
}
