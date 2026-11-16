import Foundation
import Photos

struct LibrarySummary {
    var photos = 0
    var videos = 0
    var screenshots = 0
    var livePhotos = 0
    var favorites = 0
    var verticalVideos = 0
    var totalVideoSeconds: Double = 0
    var oldest: Date?
    var newest: Date?
    var itemsByYear: [Int: Int] = [:]

    /// Items worth sending to the AI classifier (screenshots are filtered out for free).
    var classifiablePhotos: Int { max(photos - screenshots, 0) }

    static func build() -> LibrarySummary {
        var summary = LibrarySummary()

        summary.photos = PHAsset.fetchAssets(with: .image, options: baseOptions()).count
        summary.screenshots = PHAsset.fetchAssets(
            with: .image,
            options: baseOptions(subtype: .photoScreenshot)
        ).count
        summary.livePhotos = PHAsset.fetchAssets(
            with: .image,
            options: baseOptions(subtype: .photoLive)
        ).count

        let favoritesOptions = baseOptions()
        favoritesOptions.predicate = NSPredicate(format: "favorite == YES")
        summary.favorites = PHAsset.fetchAssets(with: favoritesOptions).count

        let videos = PHAsset.fetchAssets(with: .video, options: baseOptions())
        summary.videos = videos.count
        videos.enumerateObjects { asset, _, _ in
            summary.totalVideoSeconds += asset.duration
            if asset.pixelHeight > asset.pixelWidth {
                summary.verticalVideos += 1
            }
        }

        let calendar = Calendar.current
        let everything = PHAsset.fetchAssets(with: baseOptions())
        everything.enumerateObjects { asset, _, _ in
            guard let date = asset.creationDate else { return }
            if summary.oldest == nil || date < summary.oldest! { summary.oldest = date }
            if summary.newest == nil || date > summary.newest! { summary.newest = date }
            summary.itemsByYear[calendar.component(.year, from: date), default: 0] += 1
        }

        return summary
    }

    /// Visible library items only: hidden items and non-representative burst
    /// frames are excluded, matching what you see in the Photos app.
    private static func baseOptions(subtype: PHAssetMediaSubtype? = nil) -> PHFetchOptions {
        let options = PHFetchOptions()
        options.includeHiddenAssets = false
        options.includeAllBurstAssets = false
        if let subtype {
            options.predicate = NSPredicate(format: "(mediaSubtypes & %d) != 0", Int(subtype.rawValue))
        }
        return options
    }
}
