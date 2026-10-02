import XCTest
@testable import NotronCore

/// Until 2026-10-02 the app pointed at the developer's own checkout by absolute
/// path, so a fresh clone built fine and then ran nobody's Python. The checkout
/// is found from where the app or its source sits instead.
final class CheckoutTests: XCTestCase {
    private func makeCheckout() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString).resolvingSymlinksInPath()
        let deep = root.appendingPathComponent("mac/Notron.app/Contents/MacOS")
        try FileManager.default.createDirectory(at: deep, withIntermediateDirectories: true)
        try Data("[project]\nname = \"notron\"\n".utf8)
            .write(to: root.appendingPathComponent("pyproject.toml"))
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return root
    }

    func testAFreshCloneIsFoundFromInsideTheAppBundle() throws {
        let root = try makeCheckout()
        let start = root.appendingPathComponent("mac/Notron.app/Contents/MacOS/Notron")
        XCTAssertEqual(Checkout.home(environment: [:], startingPoints: [start]).path, root.path)
        XCTAssertEqual(Checkout.python(environment: [:], home: root),
                       root.appendingPathComponent(".venv/bin/python").path)
    }

    func testTheEnvironmentStillWins() throws {
        let root = try makeCheckout()
        let env = ["NOTRON_HOME": "/elsewhere", "NOTRON_PYTHON": "/usr/bin/python3"]
        XCTAssertEqual(Checkout.home(environment: env, startingPoints: [root]).path, "/elsewhere")
        XCTAssertEqual(Checkout.python(environment: env, home: root), "/usr/bin/python3")
    }

    func testAnotherPythonProjectAboveIsNotMistakenForNotron() throws {
        let other = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString).resolvingSymlinksInPath()
        try FileManager.default.createDirectory(at: other.appendingPathComponent("a/b"), withIntermediateDirectories: true)
        try Data("[project]\nname = \"something-else\"\n".utf8)
            .write(to: other.appendingPathComponent("pyproject.toml"))
        addTeardownBlock { try? FileManager.default.removeItem(at: other) }
        XCTAssertNil(Checkout.find(from: other.appendingPathComponent("a/b")))
    }
}
