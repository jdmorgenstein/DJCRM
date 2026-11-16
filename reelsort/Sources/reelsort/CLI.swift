import Foundation

enum ReelSortError: LocalizedError {
    case photosAccessDenied
    case photosAccessUnknown
    case invalidOption(String)
    case couldNotWriteImage(String)

    var errorDescription: String? {
        switch self {
        case .photosAccessDenied:
            return """
            Photos access was denied. Open System Settings > Privacy & Security > Photos, \
            turn on access for Terminal (or the app you ran reelsort from), then run it again.
            """
        case .photosAccessUnknown:
            return "macOS returned an unknown Photos authorization status."
        case .invalidOption(let message):
            return message
        case .couldNotWriteImage(let name):
            return "Could not save test image \(name)."
        }
    }
}

enum CLI {
    static func printUsage() {
        print("""
        reelsort — sort your Photos library into Reel categories

        USAGE
          reelsort scan [--thumbnails N] [--out DIR]

        COMMANDS
          scan    Connect to Photos and print a summary of the library.

        OPTIONS
          --thumbnails N   Also fetch N recent items as downsized JPEGs, to test
                           that photos (including iCloud-only ones) can be read.
          --out DIR        Where to save test JPEGs (default: ./reelsort-output).
        """)
    }

    static func stringOption(_ name: String, in args: [String]) -> String? {
        guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
        return args[index + 1]
    }

    static func intOption(_ name: String, in args: [String]) throws -> Int? {
        guard args.contains(name) else { return nil }
        guard let raw = stringOption(name, in: args), let value = Int(raw), value >= 0 else {
            throw ReelSortError.invalidOption("\(name) needs a whole number, e.g. \(name) 10")
        }
        return value
    }
}
