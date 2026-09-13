"""Tests for the pre-flight checks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from photodedup import preflight

from . import helpers


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def levels(self, checks, title):
        return [c.level for c in checks if c.title == title]

    def test_survey_counts_images_videos_and_bytes(self):
        helpers.write_jpeg(self.root / "a.jpg", helpers.make_photo(1))
        helpers.write_jpeg(self.root / "b.jpg", helpers.make_photo(2))
        (self.root / "c.MP4").write_bytes(b"x" * 1234)
        (self.root / "notes.txt").write_text("ignored")

        found = preflight.survey(self.root)
        self.assertEqual(found.files, 3)
        self.assertEqual(found.kinds["image"], 2)
        self.assertEqual(found.kinds["video"], 1)
        self.assertGreater(found.total_bytes, 1234)

    def test_a_partly_extracted_takeout_is_flagged(self):
        year = self.root / "Photos from 2023"
        for i in range(10):
            helpers.write_jpeg(year / f"IMG_{i}.jpg", helpers.make_photo(i))
        # Only two of ten carry a sidecar.
        for i in range(2):
            helpers.write_sidecar(year / f"IMG_{i}.jpg.supplemental-metadata.json",
                                  title=f"IMG_{i}.jpg", taken=1689326553)

        checks = preflight.check_takeout(self.root)
        self.assertEqual(self.levels(checks, "Takeout sidecars"), [preflight.WARN])

    def test_a_complete_takeout_passes(self):
        year = self.root / "Photos from 2023"
        album = self.root / "Beach Shoot"
        for i in range(10):
            helpers.write_jpeg(year / f"IMG_{i}.jpg", helpers.make_photo(i))
            helpers.write_sidecar(year / f"IMG_{i}.jpg.supplemental-metadata.json",
                                  title=f"IMG_{i}.jpg", taken=1689326553)
        helpers.write_jpeg(album / "shot.jpg", helpers.make_photo(99))
        helpers.write_sidecar(album / "shot.jpg.supplemental-metadata.json",
                              title="shot.jpg", taken=1689326553)

        checks = preflight.check_takeout(self.root)
        self.assertEqual(self.levels(checks, "Takeout sidecars"), [preflight.OK])
        self.assertEqual(self.levels(checks, "Takeout albums"), [preflight.OK])
        self.assertIn("1 album", next(c.detail for c in checks if c.title == "Takeout albums"))

    def test_pointing_at_an_empty_directory_is_a_blocking_failure(self):
        checks = preflight.check_takeout(self.root)
        self.assertEqual(self.levels(checks, "Takeout layout"), [preflight.FAIL])

    def test_disk_check_fails_when_the_export_cannot_fit(self):
        impossible = preflight.check_disk(self.root, needed_bytes=1 << 60)
        self.assertEqual(impossible.level, preflight.FAIL)
        self.assertEqual(preflight.check_disk(self.root, needed_bytes=0).level, preflight.OK)

    def test_disk_check_walks_up_to_an_existing_parent(self):
        # The staging directory usually does not exist yet.
        check = preflight.check_disk(self.root / "not" / "created" / "yet")
        self.assertEqual(check.level, preflight.OK)

    def test_run_reports_a_missing_directory_rather_than_raising(self):
        checks = preflight.run(self.root / "nope", None, None, workers=4)
        self.assertEqual(self.levels(checks, "iCloud export"), [preflight.FAIL])
