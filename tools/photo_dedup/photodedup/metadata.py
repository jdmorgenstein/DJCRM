"""Capture metadata extraction.

``exiftool`` is used when it is on PATH -- it is the ground truth, and it is the
only thing that reliably reads HEIC, MOV and QuickTime ``content.identifier``.
A pure-Pillow fallback keeps the tool usable without it, at the cost of video
metadata.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import appleexif

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".heic", ".heif", ".avif", ".gif", ".tif",
    ".tiff", ".webp", ".bmp", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf",
    ".orf", ".rw2", ".srw", ".pef",
}
VIDEO_EXTENSIONS = {
    ".mov", ".mp4", ".m4v", ".avi", ".3gp", ".3g2", ".mkv", ".mpg", ".mpeg",
    ".wmv", ".mts", ".m2ts", ".webm",
}

# EXIF tag numbers (Exif IFD unless noted).
_TAG_MAKE = 0x010F  # IFD0
_TAG_MODEL = 0x0110  # IFD0
_TAG_EXIF_IFD = 0x8769  # IFD0 pointer
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_SUBSEC_ORIGINAL = 0x9291
_TAG_OFFSET_ORIGINAL = 0x9011
_TAG_MAKERNOTE = 0x927C

EXIFTOOL_TAGS = [
    "-DateTimeOriginal",
    "-SubSecTimeOriginal",
    "-OffsetTimeOriginal",
    "-CreateDate",
    "-Make",
    "-Model",
    "-ImageWidth",
    "-ImageHeight",
    "-ContentIdentifier",
    "-BurstUUID",
    "-MediaGroupUUID",
    "-Duration",
]


def media_kind(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "other"


@dataclass
class MediaMetadata:
    """Capture-scoped facts about one media file."""

    capture_local: str | None = None  # "YYYY-MM-DDTHH:MM:SS" as written by the camera
    capture_ms: int | None = None  # sub-second component, 0-999
    capture_utc: int | None = None  # epoch seconds, when a UTC offset is known
    camera_make: str | None = None
    camera_model: str | None = None
    width: int | None = None
    height: int | None = None
    content_id: str | None = None
    burst_uuid: str | None = None
    apple_uid: str | None = None
    duration: float | None = None
    warnings: list[str] = field(default_factory=list)


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip("\x00")
    return text or None


def parse_exif_datetime(value: object) -> str | None:
    """Normalise an EXIF ``YYYY:MM:DD HH:MM:SS`` string to ISO-ish form."""
    text = _clean(value)
    if not text:
        return None
    text = text.split("\x00", 1)[0].strip()
    # exiftool may append a UTC offset; the offset is read from its own tag.
    for separator in ("+", "-"):
        if len(text) > 19 and separator in text[19:]:
            text = text[:19]
            break
    text = text[:19]
    if len(text) < 19:
        return None
    normalised = text[:10].replace(":", "-") + "T" + text[11:19]
    try:
        dt.datetime.fromisoformat(normalised)
    except ValueError:
        return None
    return normalised


def parse_subsec(value: object) -> int | None:
    """EXIF SubSecTimeOriginal is a digit string of arbitrary length."""
    text = _clean(value)
    if not text or not text.isdigit():
        return None
    return int((text + "000")[:3])


def _parse_offset(value: object) -> dt.timezone | None:
    text = _clean(value)
    if not text or len(text) < 6 or text[0] not in "+-":
        return None
    try:
        hours, minutes = int(text[1:3]), int(text[4:6])
    except ValueError:
        return None
    delta = dt.timedelta(hours=hours, minutes=minutes)
    return dt.timezone(-delta if text[0] == "-" else delta)


def _to_utc(capture_local: str | None, offset: object) -> int | None:
    tzinfo = _parse_offset(offset)
    if not capture_local or tzinfo is None:
        return None
    return int(dt.datetime.fromisoformat(capture_local).replace(tzinfo=tzinfo).timestamp())


def extract_with_pillow(path: str | Path) -> MediaMetadata:
    """Read capture metadata using Pillow only.  Images only."""
    meta = MediaMetadata()
    try:
        with Image.open(path) as image:
            meta.width, meta.height = image.size
            exif = image.getexif()
            if not exif:
                return meta

            meta.camera_make = _clean(exif.get(_TAG_MAKE))
            meta.camera_model = _clean(exif.get(_TAG_MODEL))

            try:
                exif_ifd = exif.get_ifd(_TAG_EXIF_IFD)
            except Exception:  # pragma: no cover - malformed EXIF
                exif_ifd = {}

            meta.capture_local = parse_exif_datetime(exif_ifd.get(_TAG_DATETIME_ORIGINAL))
            meta.capture_ms = parse_subsec(exif_ifd.get(_TAG_SUBSEC_ORIGINAL))
            meta.capture_utc = _to_utc(
                meta.capture_local, exif_ifd.get(_TAG_OFFSET_ORIGINAL)
            )

            maker_note = exif_ifd.get(_TAG_MAKERNOTE)
            if isinstance(maker_note, bytes):
                apple = appleexif.parse(maker_note)
                meta.content_id = apple.get("content_identifier")
                meta.burst_uuid = apple.get("burst_uuid")
                meta.apple_uid = apple.get("image_unique_id")
    except Exception as exc:  # noqa: BLE001 - a bad file must not abort a scan
        meta.warnings.append(f"pillow: {exc}")
    return meta


def exiftool_available() -> bool:
    return shutil.which("exiftool") is not None


def extract_with_exiftool(paths: list[Path]) -> dict[str, MediaMetadata]:
    """Batch-extract metadata for many files in one exiftool invocation."""
    if not paths:
        return {}

    with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False, encoding="utf-8") as handle:
        arg_file = Path(handle.name)
        handle.write("-json\n-n\n-charset\nfilename=utf8\n")
        handle.write("-api\nlargefilesupport=1\n")
        for tag in EXIFTOOL_TAGS:
            handle.write(tag + "\n")
        for path in paths:
            handle.write(str(path) + "\n")

    try:
        completed = subprocess.run(
            ["exiftool", "-@", str(arg_file)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        arg_file.unlink(missing_ok=True)

    if not completed.stdout.strip():
        return {}
    try:
        records = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}

    results: dict[str, MediaMetadata] = {}
    for record in records:
        source = record.get("SourceFile")
        if not source:
            continue
        meta = MediaMetadata()
        meta.capture_local = parse_exif_datetime(
            record.get("DateTimeOriginal") or record.get("CreateDate")
        )
        meta.capture_ms = parse_subsec(record.get("SubSecTimeOriginal"))
        meta.capture_utc = _to_utc(meta.capture_local, record.get("OffsetTimeOriginal"))
        meta.camera_make = _clean(record.get("Make"))
        meta.camera_model = _clean(record.get("Model"))
        meta.width = record.get("ImageWidth")
        meta.height = record.get("ImageHeight")
        meta.content_id = _clean(
            record.get("ContentIdentifier") or record.get("MediaGroupUUID")
        )
        meta.burst_uuid = _clean(record.get("BurstUUID"))
        duration = record.get("Duration")
        meta.duration = float(duration) if isinstance(duration, (int, float)) else None
        results[str(Path(source))] = meta
    return results
