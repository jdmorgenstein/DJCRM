"""Synthetic fixtures: photos, EXIF, Apple maker notes and Takeout layouts."""

from __future__ import annotations

import json
import random
import struct
from pathlib import Path

from PIL import Image, ImageDraw

from photodedup import appleexif

TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_EXIF_IFD = 0x8769
TAG_DATETIME_ORIGINAL = 0x9003
TAG_SUBSEC_ORIGINAL = 0x9291
TAG_OFFSET_ORIGINAL = 0x9011
TAG_MAKERNOTE = 0x927C


def apple_makernote(content_id: str | None = None, burst_uuid: str | None = None) -> bytes:
    """Build a big-endian Apple maker note carrying the given string tags."""
    entries: list[tuple[int, bytes]] = []
    if content_id:
        entries.append((appleexif.TAG_CONTENT_IDENTIFIER, content_id.encode() + b"\x00"))
    if burst_uuid:
        entries.append((appleexif.TAG_BURST_UUID, burst_uuid.encode() + b"\x00"))
    entries.sort()

    header = b"Apple iOS\x00" + b"\x00\x01" + b"MM"
    ifd_start = len(header)  # 14
    data_start = ifd_start + 2 + 12 * len(entries) + 4

    ifd = struct.pack(">H", len(entries))
    values = b""
    for tag, payload in entries:
        if len(payload) <= 4:
            slot = payload.ljust(4, b"\x00")
        else:
            slot = struct.pack(">I", data_start + len(values))
            values += payload
        ifd += struct.pack(">HHI", tag, 2, len(payload)) + slot
    ifd += struct.pack(">I", 0)  # next-IFD pointer
    return header + ifd + values


def make_photo(seed: int, size: tuple[int, int] = (480, 360)) -> Image.Image:
    """A deterministic, visually distinctive fake photograph."""
    rng = random.Random(seed)
    image = Image.new("RGB", size, (rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    draw = ImageDraw.Draw(image)
    for _ in range(60):
        x0, y0 = rng.randrange(size[0]), rng.randrange(size[1])
        box = [x0, y0, x0 + rng.randrange(20, 140), y0 + rng.randrange(20, 140)]
        draw.rectangle(box, fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    return image


def shift(image: Image.Image, dx: int = 4, dy: int = 3) -> Image.Image:
    """A near-identical frame, as the next shot in a burst would be."""
    return image.transform(image.size, Image.Transform.AFFINE, (1, 0, dx, 0, 1, dy))


def build_exif(
    *,
    capture: str | None = "2023:07:14 11:22:33",
    subsec: str | None = "456",
    offset: str | None = "+02:00",
    make: str | None = "Apple",
    model: str | None = "iPhone 14 Pro",
    content_id: str | None = None,
    burst_uuid: str | None = None,
) -> bytes:
    exif = Image.Exif()
    if make:
        exif[TAG_MAKE] = make
    if model:
        exif[TAG_MODEL] = model
    sub = exif.get_ifd(TAG_EXIF_IFD)
    if capture:
        sub[TAG_DATETIME_ORIGINAL] = capture
    if subsec:
        sub[TAG_SUBSEC_ORIGINAL] = subsec
    if offset:
        sub[TAG_OFFSET_ORIGINAL] = offset
    if content_id or burst_uuid:
        sub[TAG_MAKERNOTE] = apple_makernote(content_id, burst_uuid)
    return exif.tobytes()


def write_jpeg(path: Path, image: Image.Image, *, quality: int = 95, exif: bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"quality": quality}
    if exif is not None:
        kwargs["exif"] = exif
    image.save(path, "JPEG", **kwargs)
    return path


def write_sidecar(path: Path, *, title: str, taken: int, latitude: float = 0.0,
                  longitude: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "title": title,
        "description": "",
        "photoTakenTime": {"timestamp": str(taken), "formatted": ""},
        "creationTime": {"timestamp": str(taken + 3600), "formatted": ""},
        "geoData": {"latitude": latitude, "longitude": longitude, "altitude": 0.0},
        "googlePhotosOrigin": {"mobileUpload": {"deviceType": "IOS_PHONE"}},
    }), encoding="utf-8")
    return path
