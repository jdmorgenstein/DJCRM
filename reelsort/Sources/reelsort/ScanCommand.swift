import Foundation

enum ScanCommand {
    // Haiku 5.5 estimates from the planning stage (~1,000-token image, 4 frames per video).
    // Phase 2's pilot replaces these with measured costs.
    static let estimatedCostPerPhoto = 0.0003
    static let estimatedCostPerVideo = 0.0006

    static func run(thumbnailCount: Int, outputPath: String) async throws {
        try await PhotoAccess.ensureAuthorized()
        print("✓ Connected to Photos\n")

        print("Reading library (large libraries can take a minute)...")
        let summary = LibrarySummary.build()
        printSummary(summary)

        if thumbnailCount > 0 {
            let outputURL = URL(fileURLWithPath: outputPath).standardizedFileURL
            print("\nFetching \(thumbnailCount) recent item(s) as test images → \(outputURL.path)")
            let result = try await ThumbnailTest.run(count: thumbnailCount, outputURL: outputURL)
            let average = result.saved + result.failed > 0
                ? result.totalSeconds / Double(result.saved + result.failed) : 0
            print(String(format: "\nSaved %d, failed %d, average %.1fs per item.",
                         result.saved, result.failed, average))
            print("Open the folder to check they look right: open \"\(outputURL.path)\"")
        }
    }

    private static func printSummary(_ s: LibrarySummary) {
        let n = NumberFormatter()
        n.numberStyle = .decimal
        func fmt(_ value: Int) -> String { n.string(from: NSNumber(value: value)) ?? "\(value)" }

        let dateFormatter = DateFormatter()
        dateFormatter.dateStyle = .medium

        let hours = s.totalVideoSeconds / 3600
        print("""

        LIBRARY SUMMARY
          Photos            \(fmt(s.photos))
            screenshots     \(fmt(s.screenshots))   (skipped by the sorter)
            Live Photos     \(fmt(s.livePhotos))
          Videos            \(fmt(s.videos))   (\(String(format: "%.1f", hours)) hours total)
            vertical        \(fmt(s.verticalVideos))   (Reel-ready 9:16-style)
          Favorites         \(fmt(s.favorites))
        """)

        if let oldest = s.oldest, let newest = s.newest {
            print("  Date range        \(dateFormatter.string(from: oldest)) – \(dateFormatter.string(from: newest))")
        }

        if !s.itemsByYear.isEmpty {
            print("\n  Items per year")
            for year in s.itemsByYear.keys.sorted(by: >) {
                print("    \(year)            \(fmt(s.itemsByYear[year] ?? 0))")
            }
        }

        let estimate = Double(s.classifiablePhotos) * estimatedCostPerPhoto
            + Double(s.videos) * estimatedCostPerVideo
        print(String(format: """

        ESTIMATED ONE-TIME SORT COST (Haiku 5.5)
          ~$%.2f standard, ~$%.2f with the batch discount
          (rough estimate — the Phase 2 pilot measures the real cost)
        """, estimate, estimate / 2))
    }
}
