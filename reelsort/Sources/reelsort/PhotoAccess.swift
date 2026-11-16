import Foundation
import Photos

enum PhotoAccess {
    /// Asks for read/write access to the Photos library (write is needed in
    /// Phase 3 to create albums). Throws if the user has denied access.
    static func ensureAuthorized() async throws {
        var status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        if status == .notDetermined {
            print("Requesting Photos access — approve the macOS prompt to continue...")
            status = await PHPhotoLibrary.requestAuthorization(for: .readWrite)
        }

        switch status {
        case .authorized:
            return
        case .limited:
            print("""
            Note: Photos access is LIMITED — reelsort only sees the items you selected.
            For full sorting, allow Full Access in System Settings > Privacy & Security > Photos.

            """)
        case .denied, .restricted:
            throw ReelSortError.photosAccessDenied
        case .notDetermined:
            throw ReelSortError.photosAccessDenied
        @unknown default:
            throw ReelSortError.photosAccessUnknown
        }
    }
}
