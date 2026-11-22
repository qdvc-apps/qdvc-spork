// swift-tools-version:5.9
// QDVC Spork for macOS. Build the .app with scripts/build-app.sh (see README.md).
import PackageDescription

let package = Package(
    name: "QDVCSpork",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "QDVCSpork", targets: ["QDVCSpork"]),
        .library(name: "SporkCore", targets: ["SporkCore"]),
    ],
    targets: [
        // Toolkit-free logic (git, parsers, certificates, census). Builds and
        // tests on Linux too: `swift test` from this folder.
        .target(name: "SporkCore", path: "Sources/SporkCore"),
        .executableTarget(
            name: "QDVCSpork",
            dependencies: ["SporkCore"],
            path: "Sources/QDVCSpork"
        ),
        .testTarget(
            name: "SporkCoreTests",
            dependencies: ["SporkCore"],
            path: "Tests/SporkCoreTests"
        ),
    ]
)
