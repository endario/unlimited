// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "Unlimited",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "UnlimitedKit"),
        .executableTarget(name: "Unlimited", dependencies: ["UnlimitedKit"]),
        .testTarget(name: "UnlimitedKitTests", dependencies: ["UnlimitedKit"], resources: [.copy("Fixtures")]),
    ]
)
