import XCTest
@testable import NotronCore

/// The helper's own allowlist must agree with `CONNECTOR_SECRET` in
/// notron/credentials.py: a connector token is accepted, and the pattern never
/// reopens a fixed name or lets one server address another's item.
final class KeychainStoreNameTests: XCTestCase {
    func testConnectorSecretsAreAllowedAndLookalikesAreNot() {
        XCTAssertTrue(KeychainStore.allowed("connector.github.GITHUB_TOKEN"))
        XCTAssertTrue(KeychainStore.allowed("storage-key"))
        for name in ["connector.x.lower", "connector.github.GITHUB_TOKEN\n", "connector.a.b.C",
                     "connector..TOKEN", "xconnector.github.TOKEN", "anything-at-all"] {
            XCTAssertFalse(KeychainStore.allowed(name), name.debugDescription)
        }
    }
}
