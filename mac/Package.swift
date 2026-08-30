// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "Notron",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "Notron",
            path: "Sources/Notron"
        )
    ]
)
