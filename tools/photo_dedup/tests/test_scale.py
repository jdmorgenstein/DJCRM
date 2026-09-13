"""Regression tests for candidate generation at library scale.

The bug these pin down: banding a 64-bit hash for a Hamming threshold of 6
needs 7 bands of ~9 bits, i.e. at most 512 buckets.  At 100k+ photos every
bucket is populated, the "index" stops filtering, and matching collapses into
an all-pairs scan.  Tier 4 blocks on capture time instead, and tier 5 bands for
the strict threshold, where ~13-bit bands stay selective.
"""

from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from photodedup import db, hashing, matching


def row(**overrides) -> sqlite3.Row:
    base = {
        "id": 1, "library": "icloud", "path": "/x/a.jpg", "relpath": "a.jpg",
        "filename": "a.jpg", "kind": "image", "size": 1, "file_sha256": None,
        "pixel_sha256": None, "phash": None, "dhash": None, "width": 4032,
        "height": 3024, "capture_local": None, "capture_ms": None,
        "capture_utc": None, "camera_make": "Apple", "camera_model": "iPhone 14 Pro",
        "content_id": None, "burst_uuid": None, "duration": None, "album": None,
        "takeout_title": None,
    }
    base.update(overrides)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    columns = ", ".join(f'"{k}"' for k in base)
    placeholders = ", ".join("?" for _ in base)
    connection.execute(f"CREATE TABLE t ({', '.join(f'{k} BLOB' for k in base)})")
    connection.execute(f"INSERT INTO t ({columns}) VALUES ({placeholders})", list(base.values()))
    return connection.execute("SELECT * FROM t").fetchone()


class BandSelectivityTests(unittest.TestCase):
    def test_strict_banding_stays_selective_at_library_scale(self):
        """~13-bit bands must still partition 120k hashes into many buckets."""
        rng = random.Random(11)
        count = 120_000
        buckets = set()
        for _ in range(count):
            buckets.update(hashing.bands(rng.getrandbits(64), matching.STRICT_PHASH_THRESHOLD + 1))

        bands_per_row = matching.STRICT_PHASH_THRESHOLD + 1
        average_bucket = count * bands_per_row / len(buckets)
        # The old 7-band split gave ~4k buckets and ~200 rows each, before the
        # 13-band dHash index piled another ~3900 on top.
        self.assertGreater(len(buckets), 30_000, "bands are too narrow to filter")
        self.assertLess(average_bucket, 30, f"{average_bucket:.0f} rows per bucket is not blocking")

    def test_load_library_builds_only_the_phash_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = db.connect(Path(tmp) / "i.sqlite")
            rng = random.Random(3)
            db.upsert_many(connection, [
                {
                    "library": "icloud", "path": f"/x/{i}.jpg", "relpath": f"{i}.jpg",
                    "filename": f"{i}.jpg", "kind": "image", "size": 1, "mtime": 1.0,
                    "file_sha256": None, "pixel_sha256": None,
                    "phash": hashing.to_signed(rng.getrandbits(64)),
                    "dhash": hashing.to_signed(rng.getrandbits(64)),
                    "width": 4032, "height": 3024, "capture_local": None,
                    "capture_ms": None, "capture_utc": None, "camera_make": None,
                    "camera_model": None, "content_id": None, "burst_uuid": None,
                    "apple_uid": None, "duration": None, "album": None,
                    "takeout_title": None, "sidecar": None, "error": None,
                }
                for i in range(2000)
            ])
            library = matching.load_library(connection, "icloud", matching.Options())

            wide = matching.load_library(connection, "icloud", matching.Options(phash_threshold=12))

        self.assertFalse(hasattr(library, "dhash_index"))
        self.assertEqual(library.phash_bands, matching.STRICT_PHASH_THRESHOLD + 1)
        # Raising the tier-4 threshold must not widen the index.
        self.assertEqual(wide.phash_bands, library.phash_bands)
        self.assertEqual(len(wide.phash_index), len(library.phash_index))


class CaptureBlockingTests(unittest.TestCase):
    """Tier 4 must not depend on the perceptual index to find its candidates."""

    def test_tier4_matches_beyond_the_strict_band_threshold(self):
        # Distance 6, with the differing bits spread so that every one of the
        # five bands is dirty -- inside the tier-4 threshold, unreachable
        # through the tier-5 band index.  It can only match via capture time.
        base = 0x0F0F0F0F0F0F0F0F
        far = base
        for bit in (60, 45, 30, 20, 6, 2):  # one per band, plus one spare
            far ^= 1 << bit
        self.assertEqual(hashing.hamming(base, far), 6)
        self.assertGreater(hashing.hamming(base, far), matching.STRICT_PHASH_THRESHOLD)

        icloud_row = row(id=10, phash=hashing.to_signed(base), dhash=hashing.to_signed(0),
                         capture_local="2023-07-14T11:22:33", capture_ms=456)
        google_row = row(id=20, library="google", path="/g/a.jpg",
                         phash=hashing.to_signed(far), dhash=hashing.to_signed(0),
                         capture_local="2023-07-14T11:22:33", capture_ms=456)

        library = matching.Library([icloud_row], matching.STRICT_PHASH_THRESHOLD + 1)
        self.assertNotIn(10, library.perceptual_candidates(google_row),
                         "fixture no longer exercises the capture-time path")

        match = matching.match_one(google_row, library, matching.Options())
        self.assertEqual(match.tier, matching.TIER_CAPTURE_PERCEPTUAL)
        self.assertEqual(match.icloud_id, 10)
        self.assertEqual(match.distance, 6)

    def test_capture_candidates_respect_the_time_tolerance(self):
        icloud_row = row(id=11, capture_utc=1_689_326_553)
        library = matching.Library([icloud_row], matching.STRICT_PHASH_THRESHOLD + 1)
        google_row = row(id=21, library="google", capture_utc=1_689_326_555)

        self.assertEqual(library.capture_candidates(google_row, 0), {})
        self.assertIn(11, library.capture_candidates(google_row, 2))

    def test_a_still_is_never_matched_to_a_clip_taken_at_the_same_instant(self):
        # A Live Photo's still and its video share a capture time to the
        # sub-second; tier 4b must not call one a duplicate of the other.
        video = row(id=12, kind="video", capture_local="2023-07-14T11:22:33", capture_ms=1)
        library = matching.Library([video], matching.STRICT_PHASH_THRESHOLD + 1)
        google_row = row(id=22, library="google", kind="image",
                         phash=hashing.to_signed(7), dhash=hashing.to_signed(7),
                         capture_local="2023-07-14T11:22:33", capture_ms=1)
        self.assertEqual(matching.match_one(google_row, library, matching.Options()).confidence,
                         matching.UNIQUE)


if __name__ == "__main__":
    unittest.main()


class QuickPassTests(unittest.TestCase):
    """A --quick pass, then `match`, then --upgrade only what is left."""

    def setUp(self):
        import shutil
        from photodedup import report, scan
        from . import helpers

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.scan, self.helpers, self.report = scan, helpers, report

        self.icloud = self.root / "icloud"
        self.google = self.root / "google"
        self.google.mkdir(parents=True, exist_ok=True)

        # 1: shared, carries an Apple ContentIdentifier -> tier 1, no decode.
        helpers.write_jpeg(self.icloud / "IMG_1.jpg", helpers.make_photo(1, size=(400, 300)),
                           exif=helpers.build_exif(content_id="CID-1"))
        helpers.write_jpeg(self.google / "IMG_1.jpg", helpers.make_photo(1, size=(400, 300)), quality=70,
                           exif=helpers.build_exif(content_id="CID-1"))
        # 2: shared, byte-identical -> tier 2, no decode.
        helpers.write_jpeg(self.icloud / "IMG_2.jpg", helpers.make_photo(2, size=(410, 300)),
                           exif=helpers.build_exif(capture="2023:01:02 03:04:05", subsec="200"))
        shutil.copy2(self.icloud / "IMG_2.jpg", self.google / "IMG_2.jpg")
        # 3: shared, metadata stripped -> needs the decoded-pixel hash.
        helpers.write_jpeg(self.icloud / "IMG_3.jpg", helpers.make_photo(3, size=(420, 300)),
                           exif=helpers.build_exif(capture="2023:03:04 05:06:07", subsec="300"))
        helpers.write_jpeg(self.google / "IMG_3.jpg", helpers.make_photo(3, size=(420, 300)), quality=95)
        # 4: Google only.
        helpers.write_jpeg(self.google / "IMG_4.jpg", helpers.make_photo(4, size=(430, 300)),
                           exif=helpers.build_exif(capture="2023:05:06 07:08:09", subsec="400"))

        self.connection = db.connect(self.root / "i.sqlite")

    def quick_index(self):
        for library, root in (("icloud", self.icloud), ("google", self.google)):
            self.scan.scan_library(self.connection, root, library, workers=1,
                                   use_exiftool=False, quick=True)

    def confidences(self):
        return {
            row["filename"]: row["confidence"]
            for row in self.connection.execute(
                "SELECT g.filename, m.confidence FROM matches m JOIN media g ON g.id = m.google_id"
            )
        }

    def test_quick_pass_decodes_nothing(self):
        self.quick_index()
        decoded = self.connection.execute(
            "SELECT COUNT(*) AS n FROM media WHERE pixel_sha256 IS NOT NULL").fetchone()["n"]
        self.assertEqual(decoded, 0)
        hashed = self.connection.execute(
            "SELECT COUNT(*) AS n FROM media WHERE file_sha256 IS NOT NULL").fetchone()["n"]
        self.assertEqual(hashed, 7)

    def test_content_id_and_file_hash_resolve_without_decoding(self):
        self.quick_index()
        matching.run(self.connection, matching.Options())
        found = self.confidences()
        self.assertEqual(found["IMG_1.jpg"], matching.CERTAIN)
        self.assertEqual(found["IMG_2.jpg"], matching.CERTAIN)
        # These two could not be settled without pixels.
        self.assertEqual(found["IMG_3.jpg"], matching.UNIQUE)
        self.assertEqual(found["IMG_4.jpg"], matching.UNIQUE)

    def test_upgrade_only_touches_what_the_match_could_not_resolve(self):
        self.quick_index()
        matching.run(self.connection, matching.Options())

        google_pending = self.scan.paths_needing_upgrade(self.connection, "google")
        self.assertEqual({Path(p).name for p in google_pending}, {"IMG_3.jpg", "IMG_4.jpg"})

        icloud_pending = self.scan.paths_needing_upgrade(self.connection, "icloud")
        # Tier 3 blocks on dimensions, so only iCloud photos the size of an
        # unresolved Google file are selected.  IMG_1 and IMG_2 are settled and
        # a different size, so they are never decoded.
        self.assertEqual({Path(p).name for p in icloud_pending}, {"IMG_3.jpg"})

        thorough = self.scan.paths_needing_upgrade(self.connection, "icloud", thorough=True)
        self.assertEqual({Path(p).name for p in thorough},
                         {"IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg"})

    def test_upgrade_then_rematch_resolves_the_residue(self):
        self.quick_index()
        matching.run(self.connection, matching.Options())
        for library, root in (("icloud", self.icloud), ("google", self.google)):
            pending = self.scan.paths_needing_upgrade(self.connection, library)
            self.scan.scan_library(self.connection, root, library, workers=1,
                                   use_exiftool=False, only_paths=pending)
        matching.run(self.connection, matching.Options())

        found = self.confidences()
        self.assertEqual(found["IMG_3.jpg"], matching.CERTAIN)
        self.assertEqual(found["IMG_4.jpg"], matching.UNIQUE)
        # IMG_1/IMG_2 were never decoded, on either side.
        never = self.connection.execute(
            "SELECT COUNT(*) AS n FROM media WHERE pixel_sha256 IS NULL").fetchone()["n"]
        self.assertEqual(never, 4)


class TwoPassEquivalenceTests(unittest.TestCase):
    """--quick then --upgrade must reach the same verdicts as a full index.

    This is the acceptance test for the optimisation. An earlier version of the
    upgrade selection used "has no capture time" to find the files tier 3 exists
    for, which silently skipped every metadata-stripped file, because Takeout
    supplies a capture time from the JSON sidecar even when it stripped the EXIF.
    Twenty duplicates went undetected and only a run like this one caught it.
    """

    def _verdicts(self, connection):
        return {
            row["path"]: (row["confidence"], row["tier"])
            for row in connection.execute(
                "SELECT g.path, m.confidence, m.tier FROM matches m "
                "JOIN media g ON g.id = m.google_id"
            )
        }

    def test_two_pass_matches_a_full_index(self):
        import shutil
        from PIL import Image
        from photodedup import scan
        from . import helpers

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            icloud, google = root / "icloud", root / "google" / "Photos from 2023"
            google.mkdir(parents=True)

            for i in range(9):
                photo = helpers.make_photo(i, size=(640, 480))
                exif = helpers.build_exif(capture=f"2023:05:{i + 1:02d} 10:00:00",
                                          subsec=f"{i}00", content_id=f"CID-{i}")
                helpers.write_jpeg(icloud / f"IMG_{i}.jpg", photo, exif=exif)
                if i % 3 == 0:                        # byte-identical
                    shutil.copy2(icloud / f"IMG_{i}.jpg", google / f"IMG_{i}.jpg")
                elif i % 3 == 1:                      # metadata stripped
                    helpers.write_jpeg(google / f"IMG_{i}.jpg", photo, quality=95)
                else:                                 # re-encoded, EXIF kept
                    helpers.write_jpeg(google / f"IMG_{i}.jpg",
                                       photo.resize((320, 240), Image.Resampling.LANCZOS),
                                       quality=50, exif=exif)
                # Takeout hands back a capture time even where EXIF was stripped.
                helpers.write_sidecar(google / f"IMG_{i}.jpg.supplemental-metadata.json",
                                      title=f"IMG_{i}.jpg", taken=1683000000 + i)
            helpers.write_jpeg(google / "only-google.jpg", helpers.make_photo(99, size=(640, 480)),
                               exif=helpers.build_exif(capture="2023:09:09 09:09:09", subsec="900"))

            options = matching.Options()
            full_db = db.connect(root / "full.sqlite")
            for library, path, takeout in (("icloud", icloud, False), ("google", google, True)):
                scan.scan_library(full_db, path, library, workers=1, use_exiftool=False,
                                  read_takeout=takeout)
            matching.run(full_db, options)

            quick_db = db.connect(root / "quick.sqlite")
            for library, path, takeout in (("icloud", icloud, False), ("google", google, True)):
                scan.scan_library(quick_db, path, library, workers=1, use_exiftool=False,
                                  read_takeout=takeout, quick=True)
            matching.run(quick_db, options)
            for library, path, takeout in (("icloud", icloud, False), ("google", google, True)):
                pending = scan.paths_needing_upgrade(quick_db, library)
                scan.scan_library(quick_db, path, library, workers=1, use_exiftool=False,
                                  read_takeout=takeout, only_paths=pending)
            matching.run(quick_db, options)

            full, two_pass = self._verdicts(full_db), self._verdicts(quick_db)

        self.assertEqual(len(full), 10)
        self.assertEqual(two_pass, full)
        self.assertEqual(sum(1 for c, _ in full.values() if c == matching.UNIQUE), 1)
