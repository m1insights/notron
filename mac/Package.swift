// swift-tools-version: 5.10
import PackageDescription
import Foundation

// KeychainStore lives in NotronCore because BOTH the app and the credential
// helper need it, and SwiftPM forbids a source file appearing in two targets of
// the same package ("target has overlapping sources"). Sharing the library is
// the only way to keep ONE implementation of this boundary rather than two that
// drift apart — which for a credential path is the whole risk.
//
// History, so nobody repeats it: the first attempt kept KeychainStore in the app
// target and pointed a helper target at the same file. It failed three ways in a
// row, each with a message that did not name the cause:
//   1. with two executable targets and no products declared, SwiftPM built only
//      the app and silently skipped the helper — no error, no warning, just no
//      binary at packaging time;
//   2. declaring products surfaced "target ... is empty", because `exclude`
//      removes the very file `sources` names back — `exclude` wins over `sources`;
//   3. fixing that surfaced the cross-target overlap, which is structural.
let coreSources = ["AccountSession.swift", "ManagedIPCSession.swift", "KeychainStore.swift"]
let appFiles = try! FileManager.default.contentsOfDirectory(atPath: "Sources/Notron")
    .filter { !coreSources.contains($0) }

let package = Package(
    name: "Notron",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Notron", targets: ["Notron"]),
        .executable(name: "NotronKeychainHelper", targets: ["NotronKeychainHelper"]),
    ],
    targets: [
        .executableTarget(
            name: "Notron",
            dependencies: ["NotronCore"],
            path: "Sources/Notron",
            exclude: coreSources
        ),
        .target(
            name: "NotronCore",
            path: "Sources/Notron",
            exclude: appFiles,
            sources: coreSources
        ),
        // The credential helper: its own directory, so no sources are shared with
        // any other target, and it reaches the Keychain through the same
        // KeychainStore the app uses. It ships inside the app bundle and is
        // verified by the Python side before use — see notron/bundle.py.
        .executableTarget(
            name: "NotronKeychainHelper",
            dependencies: ["NotronCore"],
            path: "Sources/NotronKeychainHelper"
        ),
        .testTarget(name: "NotronCoreTests", dependencies: ["NotronCore"]),
    ]
)
