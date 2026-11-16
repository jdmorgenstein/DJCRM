import AppKit
import Foundation
import Photos

/// Fetches the most recent items as ~1024px JPEGs. This proves reelsort can
/// read pixels (downloading from iCloud when needed) — the same request the
/// classifier will make in Phase 2.
enum ThumbnailTest {
    struct Result {
        var saved = 0
        var failed = 0
        var totalSeconds: Double = 0
    }

    static func run(count: Int, outputURL: URL) async throws -> Result {
        try FileManager.default.createDirectory(at: outputURL, withIntermediateDirectories: true)

        let options = PHFetchOptions()
        options.includeHiddenAssets = false
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
        options.fetchLimit = count
        let assets = PHAsset.fetchAssets(with: options)

        var list: [PHAsset] = []
        assets.enumerateObjects { asset, _, _ in list.append(asset) }

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd_HHmmss"

        var result = Result()
        for (index, asset) in list.enumerated() {
            let kind = asset.mediaType == .video ? "video" : "photo"
            let dateText = asset.creationDate.map { formatter.string(from: $0) } ?? "nodate"
            let name = String(format: "%02d_%@_%@.jpg", index + 1, kind, dateText)

            let start = Date()
            let image = await requestImage(for: asset)
            let elapsed = Date().timeIntervalSince(start)
            result.totalSeconds += elapsed

            guard let image, let jpeg = jpegData(from: image) else {
                result.failed += 1
                print("  ✗ \(name) — could not load (it may still be downloading from iCloud)")
                continue
            }
            do {
                try jpeg.write(to: outputURL.appendingPathComponent(name))
                result.saved += 1
                print(String(format: "  ✓ %@  (%.1fs)", name, elapsed))
            } catch {
                result.failed += 1
                print("  ✗ \(name) — \(ReelSortError.couldNotWriteImage(name).localizedDescription)")
            }
        }
        return result
    }

    private static func requestImage(for asset: PHAsset) async -> NSImage? {
        let options = PHImageRequestOptions()
        options.deliveryMode = .highQualityFormat // exactly one callback
        options.resizeMode = .fast
        options.isNetworkAccessAllowed = true      // fetch iCloud-only items
        options.isSynchronous = false

        return await withCheckedContinuation { continuation in
            PHImageManager.default().requestImage(
                for: asset,
                targetSize: CGSize(width: 1024, height: 1024),
                contentMode: .aspectFit,
                options: options
            ) { image, _ in
                continuation.resume(returning: image)
            }
        }
    }

    private static func jpegData(from image: NSImage) -> Data? {
        guard let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff) else { return nil }
        return bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.8])
    }
}
