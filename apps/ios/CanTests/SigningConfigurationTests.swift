import XCTest
@testable import Can

final class SigningConfigurationTests: XCTestCase {
    func testSideStoreAppAndWidgetSelectTheSameRewrittenGroup() throws {
        let group = "group.com.canbudget.can.ABCDE12345"
        for bundle in ["com.canbudget.can.ABCDE12345", "com.canbudget.can.widget.ABCDE12345"] {
            let rights: [String: Any] = ["application-identifier": "ABCDE12345." + bundle,
                                       "com.apple.security.application-groups": [group]]
            let xml = try PropertyListSerialization.data(fromPropertyList: ["Entitlements": rights], format: .xml, options: 0)
            let cms = Data([0, 128, 255]) + xml + Data([255, 0])
            let parsed = try XCTUnwrap(SigningConfiguration.profileEntitlements(cms))
            XCTAssertEqual(SigningConfiguration.appGroup(parsed), group)
            XCTAssertEqual(parsed["application-identifier"] as? String, "ABCDE12345." + bundle)
        }
    }
    func testMalformedOrAmbiguousProfileNeverSelectsAnUnrelatedGroup() {
        XCTAssertNil(SigningConfiguration.profileEntitlements(Data("not a profile".utf8)))
        XCTAssertNil(SigningConfiguration.appGroup([:]))
        XCTAssertNil(SigningConfiguration.appGroup(["com.apple.security.application-groups": ["group.one", "group.two"]]))
    }
    func testLegacyDeletionIntentRetainsItsIdentityAndReplayProof() throws {
        let json = """
        {"userID":"312fe616-a10a-4f57-8e76-0123456789ab","appleUserID":"legacy-apple-subject",
         "commandID":"b12fe616-a10a-4f57-8e76-0123456789ab","reauthToken":"saved-proof"}
        """
        let intent = try JSONDecoder().decode(AccountDeletionIntent.self, from: Data(json.utf8))
        XCTAssertEqual(intent.identityID, "legacy-apple-subject")
        XCTAssertEqual(intent.replayProofs, ["saved-proof"])
        XCTAssertFalse(intent.needsCleanup)
    }
}
