#if DEBUG && targetEnvironment(simulator)
import XCTest
@testable import Can

final class SimulatorSessionTests: XCTestCase {
    private let now = BusinessTime.parse("2026-10-19T04:00:00Z")!
    private let origin = URL(string: "https://151.243.217.6")!

    private func envelope(origin: String = "https://151.243.217.6", expiry: String = "2026-10-19T12:00:00Z",
                          access: String = "access-fixture") -> SimulatorSessionImport {
        SimulatorSessionImport(api_origin: origin, expires_at: expiry,
                               user: User(id: "8082943f-55d3-42c7-bca0-c31db7c38b28", display_name: "Simulator"),
                               session: SessionTokens(access_token: access, access_expires_at: "2026-10-19T04:15:00Z",
                                                      refresh_token: "refresh-fixture", refresh_expires_at: expiry))
    }

    func testSimulatorSessionDoesNotSupplyAppleReauthentication() throws {
        let login = try envelope().validatedLogin(for: origin, at: now)
        XCTAssertEqual(login.user.display_name, "Simulator")
        XCTAssertTrue(login.reauth_token.isEmpty)
        XCTAssertLessThan(try XCTUnwrap(BusinessTime.parse(login.reauth_expires_at)), now)
    }

    func testSimulatorSessionCannotBeImportedForAnotherOrigin() {
        for other in ["https://other.example", "http://151.243.217.6", "https://151.243.217.6/extra"] {
            XCTAssertThrowsError(try envelope(origin: other).validatedLogin(for: origin, at: now))
        }
    }

    func testExpiredOrLongLivedSimulatorSessionsAreRejected() {
        for expiry in ["2026-10-19T03:59:00Z", "2026-10-20T04:00:00Z", "invalid"] {
            XCTAssertThrowsError(try envelope(expiry: expiry).validatedLogin(for: origin, at: now))
        }
        XCTAssertThrowsError(try envelope().validatedLogin(for: origin, at: now.addingTimeInterval(16 * 60)))
    }

    func testSimulatorSessionRequiresAnAccessCredential() {
        XCTAssertThrowsError(try envelope(access: "").validatedLogin(for: origin, at: now))
    }
}
#endif
