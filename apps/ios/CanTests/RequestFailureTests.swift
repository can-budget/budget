import XCTest
@testable import Can

final class RequestFailureTests: XCTestCase {
    func testLifecycleAndURLSessionCancellationsAreExpected() {
        XCTAssertTrue(RequestFailure.isCancellation(CancellationError()))
        XCTAssertTrue(RequestFailure.isCancellation(URLError(.cancelled)))
        XCTAssertTrue(RequestFailure.isCancellation(NSError(domain: NSURLErrorDomain, code: NSURLErrorCancelled)))
    }
    func testRealNetworkAndServerFailuresAreNotSilenced() {
        XCTAssertFalse(RequestFailure.isCancellation(URLError(.timedOut)))
        XCTAssertFalse(RequestFailure.isCancellation(URLError(.notConnectedToInternet)))
        XCTAssertFalse(RequestFailure.isCancellation(APIError(code: "unauthorized", message: "отменено", retryable: false)))
        XCTAssertFalse(RequestFailure.isCancellation(NSError(domain: "Can", code: NSURLErrorCancelled)))
    }
}
