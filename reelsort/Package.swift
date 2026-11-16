// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "reelsort",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "reelsort",
            path: "Sources/reelsort",
            exclude: ["Info.plist"],
            linkerSettings: [
                .linkedFramework("Photos"),
                .linkedFramework("AppKit"),
                // Embed Info.plist in the binary so macOS can show the
                // Photos permission prompt with our usage description.
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Sources/reelsort/Info.plist",
                ]),
            ]
        ),
    ]
)
