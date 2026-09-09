// swift-tools-version: 5.10
import PackageDescription
import Foundation

let appFiles = try! FileManager.default.contentsOfDirectory(atPath: "Sources/Notron").filter { !["AccountSession.swift","ManagedIPCSession.swift"].contains($0) }

let package = Package(
    name: "Notron",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "Notron",
            dependencies: ["NotronCore"],
            path: "Sources/Notron",
            exclude: ["AccountSession.swift","ManagedIPCSession.swift"]
        ),
        .target(name: "NotronCore", path: "Sources/Notron", exclude: appFiles, sources: ["AccountSession.swift","ManagedIPCSession.swift"]),
        .testTarget(name: "NotronCoreTests", dependencies: ["NotronCore"])
    ]
)
