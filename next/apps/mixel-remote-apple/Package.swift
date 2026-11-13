// swift-tools-version: 6.0
// Mixel Remote for Mac, iPhone and iPad: the shared app code (MixelRemoteKit), the Rust engine
// (MixelCore, built by scripts/build-core.sh) and their tests. The two apps (Mac/, iOS/) are thin
// shells generated with xcodegen from project.yml.
import PackageDescription

let package = Package(
    name: "MixelRemoteApple",
    defaultLocalization: "en",
    platforms: [.macOS(.v13), .iOS(.v16)],
    products: [
        .library(name: "MixelRemoteKit", targets: ["MixelRemoteKit"]),
        .library(name: "MixelCore", targets: ["MixelCore"]),
    ],
    targets: [
        .target(
            name: "MixelRemoteKit",
            resources: [.process("Resources")]
        ),
        .binaryTarget(name: "mixel_core_ffiFFI", path: "Core/MixelCoreFFI.xcframework"),
        .target(
            name: "MixelCore",
            dependencies: ["MixelRemoteKit", "mixel_core_ffiFFI"],
            linkerSettings: [
                .linkedFramework("SystemConfiguration"),
                .linkedFramework("Security"),
                .linkedFramework("CoreAudio", .when(platforms: [.macOS])),
                .linkedFramework("AudioToolbox"),
                .linkedFramework("AVFoundation"),
                .linkedFramework("CoreMedia"),
                .linkedFramework("CoreVideo"),
                .linkedFramework("VideoToolbox"),
                .linkedFramework("ScreenCaptureKit", .when(platforms: [.macOS])),
                .linkedFramework("ApplicationServices", .when(platforms: [.macOS])),
                .linkedFramework("AppKit", .when(platforms: [.macOS])),
                .linkedFramework("IOKit", .when(platforms: [.macOS])),
                .linkedFramework("CoreGraphics"),
                .linkedFramework("Metal"),
                .linkedFramework("IOSurface"),
                .linkedLibrary("c++"),
                .linkedLibrary("resolv"),
                .linkedLibrary("cups", .when(platforms: [.macOS])),
            ]
        ),
        .testTarget(
            name: "MixelRemoteKitTests",
            dependencies: ["MixelRemoteKit"]
        ),
    ],
    swiftLanguageModes: [.v5]
)
