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
