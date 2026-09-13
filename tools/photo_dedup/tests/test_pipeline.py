"""End-to-end: index two libraries, match them, report and stage the result."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from photodedup import db, matching, report, scan

from . import helpers

CAPTURES = {
    1: ("2023:07:14 11:22:33", "100"),
    2: ("2023:07:14 14:05:09", "220"),
    3: ("2023:08:01 09:00:00", "015"),
    4: ("2023:09:20 18:41:02", "700"),
    5: ("2023:09:20 18:41:03", "810"),
}


class PipelineTests(unittest.TestCase):
    """A miniature of the real migration.

    iCloud holds photos 1-3 plus one burst frame.  The Takeout holds:
      * photo 1 byte-identical,
      * photo 2 with its metadata stripped (same pixels, different bytes),
      * photo 3 re-encoded the way "Storage saver" does it,
      * photo 4 and photo 5, which exist only in Google, inside a real album,
      * a second burst frame that must NOT be mistaken for the iCloud one.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.icloud = self.root / "icloud"
        self.google = self.root / "takeout" / "Google Photos"
        self.year = self.google / "Photos from 2023"
        self.album = self.google / "Sarah & Tom Engagement"

        self.photos = {seed: helpers.make_photo(seed, size=(640, 480)) for seed in CAPTURES}
        self._build_icloud()
        self._build_takeout()

        self.connection = db.connect(self.root / "index.sqlite")
        scan.scan_library(self.connection, self.icloud, "icloud", workers=1, use_exiftool=False)
        scan.scan_library(self.connection, self.google, "google", workers=1,
                          use_exiftool=False, read_takeout=True)
        matching.run(self.connection, matching.Options())

    def _exif(self, seed: int, **overrides):
        capture, subsec = CAPTURES[seed]
        return helpers.build_exif(capture=capture, subsec=subsec, **overrides)

    def _build_icloud(self):
        helpers.write_jpeg(self.icloud / "IMG_0001.jpg", self.photos[1],
                           exif=self._exif(1, content_id="CID-0001"))
        helpers.write_jpeg(self.icloud / "IMG_0002.jpg", self.photos[2], exif=self._exif(2))
        helpers.write_jpeg(self.icloud / "IMG_0003.jpg", self.photos[3], exif=self._exif(3))
        # One frame of a burst; the Takeout holds a different frame of the same burst.
        helpers.write_jpeg(
            self.icloud / "IMG_0099.jpg",
            helpers.make_photo(42, size=(640, 480)),
            exif=helpers.build_exif(capture="2023:10:05 12:00:00", subsec="100",
                                    burst_uuid="BURST-A"),
        )

    def _build_takeout(self):
        # 1: byte-identical copy.
        self.year.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.icloud / "IMG_0001.jpg", self.year / "IMG_0001.jpg")
        helpers.write_sidecar(self.year / "IMG_0001.jpg.supplemental-metadata.json",
                              title="IMG_0001.jpg", taken=1689326553)

        # 2: same pixels, metadata stripped -- the classic "hashes differ" case.
        helpers.write_jpeg(self.year / "IMG_0002.jpg", self.photos[2], quality=95)
        helpers.write_sidecar(self.year / "IMG_0002.jpg.supplemental-metadata.json",
                              title="IMG_0002.jpg", taken=1689336309)

        # 3: "Storage saver" -- downscaled and re-compressed, EXIF carried over.
        helpers.write_jpeg(
            self.year / "IMG_0003.jpg",
            self.photos[3].resize((320, 240), Image.Resampling.LANCZOS),
            quality=55,
            exif=self._exif(3),
        )

        # 4 and 5: only ever uploaded to Google, inside a photo-shoot album.
        self.album.mkdir(parents=True, exist_ok=True)
        helpers.write_jpeg(self.album / "shoot-01.jpg", self.photos[4], exif=self._exif(4))
        helpers.write_jpeg(self.album / "shoot-02.jpg", self.photos[5], exif=self._exif(5))
        # Takeout also drops album members into the year folder: same bytes, twice.
        shutil.copy2(self.album / "shoot-01.jpg", self.year / "shoot-01.jpg")

        # A different frame of the same burst as IMG_0099: visually near-identical.
        helpers.write_jpeg(
            self.year / "IMG_0099.jpg",
            helpers.shift(helpers.make_photo(42, size=(640, 480)), dx=5, dy=4),
            exif=helpers.build_exif(capture="2023:10:05 12:00:00", subsec="330",
                                    burst_uuid="BURST-A"),
        )

    def verdict(self, filename: str):
        row = self.connection.execute(
            """
            SELECT m.tier, m.confidence, m.note FROM matches m
            JOIN media g ON g.id = m.google_id
            WHERE g.filename = ? LIMIT 1
            """,
            (filename,),
        ).fetchone()
        self.assertIsNotNone(row, f"{filename} was never indexed")
        return row

    def test_byte_identical_copy_is_certain(self):
        row = self.verdict("IMG_0001.jpg")
        self.assertEqual(row["confidence"], matching.CERTAIN)
        self.assertIn(row["tier"], {matching.TIER_CONTENT_ID, matching.TIER_BYTES})

    def test_metadata_stripped_copy_is_caught_by_the_pixel_hash(self):
        row = self.verdict("IMG_0002.jpg")
        self.assertEqual(row["tier"], matching.TIER_PIXELS)
        self.assertEqual(row["confidence"], matching.CERTAIN)

    def test_storage_saver_recompression_is_caught_by_capture_plus_perceptual(self):
        row = self.verdict("IMG_0003.jpg")
        self.assertEqual(row["tier"], matching.TIER_CAPTURE_PERCEPTUAL)
        self.assertEqual(row["confidence"], matching.CERTAIN)

    def test_google_only_album_photos_are_unique(self):
        for name in ("shoot-01.jpg", "shoot-02.jpg"):
            self.assertEqual(self.verdict(name)["confidence"], matching.UNIQUE, name)

    def test_a_different_burst_frame_is_never_called_a_duplicate(self):
        row = self.verdict("IMG_0099.jpg")
        self.assertEqual(row["confidence"], matching.UNIQUE,
                         "a distinct burst frame was silently discarded")

    def test_report_collapses_takeout_copies_and_keeps_album_membership(self):
        outdir = self.root / "report"
        summary = report.write_reports(self.connection, outdir)

        # shoot-01 exists twice on disk but is one asset.
        self.assertEqual(summary["unique_files"], 4)
        self.assertEqual(summary["unique_assets"], 3)
        self.assertEqual(summary["unique_albums"], ["Sarah & Tom Engagement"])

        for name in ("unique.csv", "duplicates.csv", "review.csv", "albums.csv", "errors.csv"):
            self.assertTrue((outdir / name).exists(), name)

        unique_csv = (outdir / "unique.csv").read_text(encoding="utf-8")
        self.assertIn("Sarah & Tom Engagement", unique_csv)

    def test_staging_builds_one_folder_per_album(self):
        staging = self.root / "staging"
        stats = report.stage(self.connection, staging)

        self.assertEqual(stats["assets"], 3)
        self.assertEqual(stats["failed"], 0)
        self.assertTrue((staging / "Sarah & Tom Engagement" / "shoot-01.jpg").exists())
        self.assertTrue((staging / "Sarah & Tom Engagement" / "shoot-02.jpg").exists())
        self.assertTrue((staging / report.UNSORTED_FOLDER / "IMG_0099.jpg").exists())

    def test_rebuild_albums_stages_the_icloud_original_of_known_duplicates(self):
        # shoot-01 is Google-only, but put an already-in-iCloud photo in the
        # album too and check its iCloud original gets staged beside it.
        shutil.copy2(self.icloud / "IMG_0001.jpg", self.album / "IMG_0001.jpg")
        scan.scan_library(self.connection, self.google, "google", workers=1,
                          use_exiftool=False, read_takeout=True)
        matching.run(self.connection, matching.Options())

        members = report.album_members_in_icloud(self.connection)
        self.assertIn(("Sarah & Tom Engagement", str(self.icloud / "IMG_0001.jpg")), members)

        staging = self.root / "staging-albums"
        stats = report.stage(self.connection, staging, rebuild_albums=True)
        self.assertEqual(stats["existing_album_members"], 1)
        staged = staging / "Sarah & Tom Engagement" / "IMG_0001.jpg"
        self.assertTrue(staged.exists())
        # Staged from the iCloud export, so Photos' own fingerprint will match.
        self.assertEqual(staged.stat().st_ino, (self.icloud / "IMG_0001.jpg").stat().st_ino)

    def test_rescan_is_incremental(self):
        stats = scan.scan_library(self.connection, self.icloud, "icloud",
                                  workers=1, use_exiftool=False)
        self.assertEqual(stats["scanned"], 0)
        self.assertEqual(stats["skipped"], stats["total"])


class LivePhotoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_unique_still_keeps_its_video_half(self):
        icloud = self.root / "icloud"
        google = self.root / "google"
        icloud.mkdir()
        google.mkdir()

        # iCloud has an unrelated photo; Google has a Live Photo pair whose
        # video half would otherwise look like a match for nothing at all.
        helpers.write_jpeg(icloud / "IMG_1000.jpg", helpers.make_photo(70),
                           exif=helpers.build_exif())
        helpers.write_jpeg(google / "IMG_2000.jpg", helpers.make_photo(71),
                           exif=helpers.build_exif(capture="2024:01:01 10:00:00", subsec="500"))
        (google / "IMG_2000.MP4").write_bytes(b"not really a video, but it indexes")

        connection = db.connect(self.root / "index.sqlite")
        scan.scan_library(connection, icloud, "icloud", workers=1, use_exiftool=False)
        scan.scan_library(connection, google, "google", workers=1, use_exiftool=False)
        matching.run(connection, matching.Options())

        rows = {
            row["filename"]: row["confidence"]
            for row in connection.execute(
                "SELECT g.filename, m.confidence FROM matches m JOIN media g ON g.id = m.google_id"
            )
        }
        self.assertEqual(rows["IMG_2000.jpg"], matching.UNIQUE)
        self.assertEqual(rows["IMG_2000.MP4"], matching.UNIQUE)

        staging = self.root / "staging"
        report.stage(connection, staging)
        unsorted = staging / report.UNSORTED_FOLDER
        self.assertTrue((unsorted / "IMG_2000.jpg").exists())
        self.assertTrue((unsorted / "IMG_2000.MP4").exists())


if __name__ == "__main__":
    unittest.main()
