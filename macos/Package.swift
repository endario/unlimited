// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "Unlimited",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "UnlimitedKit", plugins: [.plugin(name: "UsageMessages")]),
        .plugin(name: "UsageMessages", capability: .buildTool()),
        .executableTarget(name: "Unlimited", dependencies: ["UnlimitedKit"]),
        .testTarget(name: "UnlimitedKitTests", dependencies: ["UnlimitedKit"], resources: [.copy("Fixtures")]),
        .testTarget(name: "UnlimitedTests", dependencies: ["Unlimited", "UnlimitedKit"]),
    ]
)
