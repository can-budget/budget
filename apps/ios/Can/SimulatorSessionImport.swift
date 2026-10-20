#if DEBUG && targetEnvironment(simulator)
import Foundation

// The SSH operator supplies this file to the simulator sandbox, never to a device build.
struct SimulatorSessionImport: Decodable {
    let api_origin: String
    let expires_at: String
    let user: User
    let session: SessionTokens

    func validatedLogin(for origin: URL, at date: Date = Date()) throws -> LoginResult {
        guard api_origin == origin.absoluteString,
              UUID(uuidString: user.id) != nil,
              let expiry = BusinessTime.parse(expires_at), expiry > date,
              expiry <= date.addingTimeInterval(8 * 3600 + 60),
              let accessExpiry = BusinessTime.parse(session.access_expires_at), accessExpiry > date,
              let refreshExpiry = BusinessTime.parse(session.refresh_expires_at), refreshExpiry > date,
              accessExpiry <= expiry, refreshExpiry <= expiry,
              !session.access_token.isEmpty, !session.refresh_token.isEmpty else {
            throw APIError.local("Тестовая сессия истекла или предназначена для другого сервера. Запустите make ios-simulator.")
        }
        // This session deliberately supplies no Apple identity or reauthentication proof.
        return LoginResult(user: user, session: session, reauth_token: "", reauth_expires_at: "1970-01-01T00:00:00Z")
    }

    static func install(using api: APIClient) async throws {
        let file = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Can/simulator-session.json")
        guard let origin = AppConfiguration.baseURL else { throw APIError.local("Не настроен сервер симулятора.") }
        let envelope = try JSONDecoder().decode(Self.self, from: Data(contentsOf: file))
        var login = try envelope.validatedLogin(for: origin)
        var request = URLRequest(url: origin.appendingPathComponent("v1/me"))
        request.setValue("Bearer \(login.session.access_token)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 20
        let config = URLSessionConfiguration.ephemeral
        let connection = URLSession(configuration: config)
        defer { connection.finishTasksAndInvalidate() }
        let (data, response) = try await connection.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let current = try? JSONDecoder().decode(User.self, from: data), current.id == login.user.id else {
            throw APIError.local("Сервер не подтвердил тестовую сессию. Запустите make ios-simulator ещё раз.")
        }
        login = LoginResult(user: current, session: login.session, reauth_token: "", reauth_expires_at: login.reauth_expires_at)
        try await api.install(login)
        // The operator revokes the prior session and its widget credential on renewal.
        try SharedWidgetStorage.clear()
        try FileManager.default.removeItem(at: file)
    }
}
#endif
