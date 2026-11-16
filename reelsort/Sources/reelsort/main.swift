import Foundation

// reelsort — sorts your Photos library into Instagram Reel categories.
// Phase 1: connect to Photos, summarize the library, and test fetching
// downsized images (including iCloud-only items).

let arguments = Array(CommandLine.arguments.dropFirst())
let command = arguments.first ?? "help"

do {
    switch command {
    case "scan":
        let thumbnailCount = try CLI.intOption("--thumbnails", in: arguments) ?? 0
        let outputPath = CLI.stringOption("--out", in: arguments) ?? "reelsort-output"
        try await ScanCommand.run(thumbnailCount: thumbnailCount, outputPath: outputPath)
    case "help", "-h", "--help":
        CLI.printUsage()
    default:
        fputs("Unknown command: \(command)\n\n", stderr)
        CLI.printUsage()
        exit(64)
    }
} catch {
    fputs("error: \(error.localizedDescription)\n", stderr)
    exit(1)
}
