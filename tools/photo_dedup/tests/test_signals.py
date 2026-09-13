"""Unit tests for the individual identity signals."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from photodedup import appleexif, hashing, metadata, takeout

from . import helpers


class AppleMakerNoteTests(unittest.TestCase):
    def test_round_trips_content_identifier_and_burst_uuid(self):
        blob = helpers.apple_makernote(
            content_id="1F2E3D4C-5B6A-7988-A9B8-C7D6E5F40312",
            burst_uuid="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
        )
        parsed = appleexif.parse(blob)
        self.assertEqual(parsed["content_identifier"], "1F2E3D4C-5B6A-7988-A9B8-C7D6E5F40312")
        self.assertEqual(parsed["burst_uuid"], "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")

    def test_ignores_foreign_and_malformed_maker_notes(self):
        self.assertEqual(appleexif.parse(None), {})
        self.assertEqual(appleexif.parse(b""), {})
        self.assertEqual(appleexif.parse(b"Nikon\x00\x02"), {})
        self.assertEqual(appleexif.parse(b"Apple iOS\x00\x00\x01MM"), {})
        truncated = helpers.apple_makernote(content_id="X" * 40)[:30]
        self.assertEqual(appleexif.parse(truncated), {})


class HashingTests(unittest.TestCase):
    def test_pixel_hash_ignores_metadata_but_file_hash_does_not(self):
        photo = helpers.make_photo(1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with_exif = helpers.write_jpeg(root / "a.jpg", photo, quality=95,
                                           exif=helpers.build_exif())
            # Same encoder settings, no EXIF: identical pixels, different bytes.
            stripped = helpers.write_jpeg(root / "b.jpg", photo, quality=95)

            self.assertNotEqual(hashing.file_sha256(with_exif), hashing.file_sha256(stripped))
            self.assertEqual(
                hashing.pixel_sha256(hashing.decode(with_exif)),
                hashing.pixel_sha256(hashing.decode(stripped)),
            )

    def test_perceptual_hashes_survive_recompression_and_downscale(self):
        photo = helpers.make_photo(2, size=(800, 600))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = helpers.write_jpeg(root / "orig.jpg", photo, quality=95)
            # What "Storage saver" does: resize, then re-encode much harder.
            degraded = helpers.write_jpeg(
                root / "saver.jpg",
                photo.resize((400, 300), hashing.Image.Resampling.LANCZOS),
                quality=55,
            )

            a, b = hashing.decode(original), hashing.decode(degraded)
            self.assertNotEqual(hashing.pixel_sha256(a), hashing.pixel_sha256(b))
            self.assertLessEqual(hashing.hamming(hashing.phash(a), hashing.phash(b)), 6)
            self.assertLessEqual(hashing.hamming(hashing.dhash(a), hashing.dhash(b)), 12)

    def test_different_photos_are_far_apart(self):
        left = helpers.make_photo(10)
        right = helpers.make_photo(11)
        distance = hashing.hamming(hashing.phash(left), hashing.phash(right))
        self.assertGreater(distance, 12)

    def test_bands_are_an_exact_candidate_filter(self):
        left = helpers.make_photo(20)
        right = helpers.shift(left, dx=2, dy=1)
        a, b = hashing.phash(left), hashing.phash(right)
        threshold = 6
        if hashing.hamming(a, b) <= threshold:
            shared = set(hashing.bands(a, threshold + 1)) & set(hashing.bands(b, threshold + 1))
            self.assertTrue(shared, "pigeonhole guarantee violated")

    def test_bands_cover_all_64_bits(self):
        for count in range(1, 17):
            parts = hashing.bands((1 << 64) - 1, count)
            self.assertEqual(len(parts), count)
            self.assertEqual(sum(chunk.bit_count() for _, chunk in parts), 64)


class MetadataTests(unittest.TestCase):
    def test_reads_capture_time_camera_and_apple_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = helpers.write_jpeg(
                Path(tmp) / "IMG_0001.jpg",
                helpers.make_photo(3),
                exif=helpers.build_exif(content_id="CID-1", burst_uuid="BURST-1"),
            )
            meta = metadata.extract_with_pillow(path)

        self.assertEqual(meta.capture_local, "2023-07-14T11:22:33")
        self.assertEqual(meta.capture_ms, 456)
        self.assertEqual(meta.camera_model, "iPhone 14 Pro")
        self.assertEqual(meta.content_id, "CID-1")
        self.assertEqual(meta.burst_uuid, "BURST-1")
        # 11:22:33 at +02:00 is 09:22:33 UTC.
        self.assertEqual(meta.capture_utc, 1689326553)

    def test_subsecond_padding_is_left_aligned(self):
        self.assertEqual(metadata.parse_subsec("4"), 400)
        self.assertEqual(metadata.parse_subsec("45"), 450)
        self.assertEqual(metadata.parse_subsec("4567"), 456)
        self.assertIsNone(metadata.parse_subsec(""))
        self.assertIsNone(metadata.parse_subsec("abc"))

    def test_rejects_unusable_exif_dates(self):
        self.assertIsNone(metadata.parse_exif_datetime("0000:00:00 00:00:00"))
        self.assertIsNone(metadata.parse_exif_datetime("    "))
        self.assertEqual(
            metadata.parse_exif_datetime("2021:01:02 03:04:05+01:00"),
            "2021-01-02T03:04:05",
        )


class TakeoutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_resolves_every_sidecar_naming_variant(self):
        photo = helpers.make_photo(4)
        cases = {
            "plain.jpg": "plain.jpg.json",
            "supp.jpg": "supp.jpg.supplemental-metadata.json",
            "trunc.jpg": "trunc.jpg.supplemental-me.json",
            "tiny.jpg": "tiny.jpg.s.json",
        }
        for media, sidecar in cases.items():
            helpers.write_jpeg(self.root / media, photo)
            helpers.write_sidecar(self.root / sidecar, title=media, taken=1689326553)

        index = takeout.SidecarIndex(self.root)
        for media, sidecar in cases.items():
            self.assertEqual(index.find(media), self.root / sidecar, media)

    def test_numbered_duplicates_keep_their_own_sidecar(self):
        photo = helpers.make_photo(5)
        helpers.write_jpeg(self.root / "IMG_1.jpg", photo)
        helpers.write_jpeg(self.root / "IMG_1(1).jpg", photo)
        helpers.write_sidecar(self.root / "IMG_1.jpg.supplemental-metadata.json",
                              title="IMG_1.jpg", taken=1000)
        helpers.write_sidecar(self.root / "IMG_1.jpg.supplemental-metadata(1).json",
                              title="IMG_1.jpg", taken=2000)

        index = takeout.SidecarIndex(self.root)
        self.assertEqual(
            index.find("IMG_1.jpg"), self.root / "IMG_1.jpg.supplemental-metadata.json"
        )
        self.assertEqual(
            index.find("IMG_1(1).jpg"), self.root / "IMG_1.jpg.supplemental-metadata(1).json"
        )

    def test_edited_derivatives_fall_back_to_the_original_sidecar(self):
        helpers.write_jpeg(self.root / "IMG_2-edited.jpg", helpers.make_photo(6))
        helpers.write_sidecar(self.root / "IMG_2.jpg.supplemental-metadata.json",
                              title="IMG_2.jpg", taken=1689326553)
        index = takeout.SidecarIndex(self.root)
        self.assertEqual(
            index.find("IMG_2-edited.jpg"),
            self.root / "IMG_2.jpg.supplemental-metadata.json",
        )

    def test_title_index_rescues_unmatched_names(self):
        helpers.write_jpeg(self.root / "weird name.HEIC", helpers.make_photo(7))
        helpers.write_sidecar(self.root / "completely-unrelated.json",
                              title="weird name.HEIC", taken=1689326553)
        index = takeout.SidecarIndex(self.root)
        self.assertEqual(index.find("weird name.HEIC"), self.root / "completely-unrelated.json")

    def test_year_folders_are_not_albums(self):
        self.assertIsNone(takeout.album_name(Path("/x/Photos from 2019")))
        self.assertIsNone(takeout.album_name(Path("/x/Google Photos")))
        self.assertEqual(takeout.album_name(Path("/x/Sarah & Tom Engagement")),
                         "Sarah & Tom Engagement")

    def test_sidecar_fields_are_parsed(self):
        path = helpers.write_sidecar(self.root / "a.jpg.json", title="a.jpg",
                                     taken=1689326553, latitude=51.5, longitude=-0.12)
        record = takeout.load_sidecar(path)
        self.assertEqual(record.title, "a.jpg")
        self.assertEqual(record.photo_taken_utc, 1689326553)
        self.assertEqual(record.creation_utc, 1689330153)
        self.assertEqual(record.latitude, 51.5)
        self.assertEqual(record.longitude, -0.12)

    def test_zero_coordinates_mean_no_location(self):
        path = helpers.write_sidecar(self.root / "b.jpg.json", title="b.jpg", taken=1)
        record = takeout.load_sidecar(path)
        self.assertIsNone(record.latitude)
        self.assertIsNone(record.longitude)

    def test_counter_and_edited_splitting(self):
        self.assertEqual(takeout.split_counter("IMG_1(3).jpg"), ("IMG_1.jpg", 3))
        self.assertEqual(takeout.split_counter("IMG_1.jpg(3)"), ("IMG_1.jpg", 3))
        self.assertEqual(takeout.split_counter("IMG_1.jpg"), ("IMG_1.jpg", None))
        self.assertEqual(takeout.strip_edited("IMG_1-edited.jpg"), "IMG_1.jpg")
        self.assertEqual(takeout.strip_edited("IMG_1.jpg"), "IMG_1.jpg")


if __name__ == "__main__":
    unittest.main()
