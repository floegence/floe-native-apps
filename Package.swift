// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "FloeNativeDesktop",
    platforms: [.macOS(.v13)],
    products: [.library(name: "FloeNativeDesktop", targets: ["FloeNativeDesktop"])],
    targets: [
        .target(name: "FloeNativeDesktop"),
        .executableTarget(name: "NativeDesktopQualification", dependencies: ["FloeNativeDesktop"],
                          path: "qualification/macos-desktop"),
        .testTarget(name: "FloeNativeDesktopTests", dependencies: ["FloeNativeDesktop"]),
    ]
)
