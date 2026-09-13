"""Parser for the Apple maker note IFD embedded in iPhone EXIF.

Apple writes a handful of capture-scoped UUIDs into the maker note.  They are
the single most valuable de-duplication signal available, because they identify
the *capture event* rather than the file, and they survive any re-encode that
copies EXIF through (which Google Photos does).

Layout, per ExifTool's ``MakerNotes.pm``: the blob starts with ``Apple iOS\\0``,
a TIFF byte-order marker sits at offset 12, the IFD starts at offset 14, and
value offsets inside the IFD are relative to the *start of the blob*
(ExifTool: ``Start => $valuePtr + 14``, ``Base => $start - 14``).
"""

from __future__ import annotations

import struct

HEADER = b"Apple iOS\x00"
BYTE_ORDER_OFFSET = 12
IFD_OFFSET = 14

# Tag numbers from ExifTool's Image::ExifTool::Apple::Main table.
TAG_BURST_UUID = 0x000B  # shared by every frame of one burst
TAG_CONTENT_IDENTIFIER = 0x0011  # per-capture asset id; shared by a Live Photo pair
TAG_IMAGE_UNIQUE_ID = 0x0015  # Apple "ImageGroupIdentifier"
TAG_CAPTURE_REQUEST_ID = 0x0020

TAG_NAMES = {
    TAG_BURST_UUID: "burst_uuid",
    TAG_CONTENT_IDENTIFIER: "content_identifier",
    TAG_IMAGE_UNIQUE_ID: "image_unique_id",
    TAG_CAPTURE_REQUEST_ID: "capture_request_id",
}

# TIFF field type -> size in bytes of a single component.
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_ASCII = 2


def parse(blob: bytes | None) -> dict[str, str]:
    """Return the Apple maker note string tags we care about, keyed by name.

    Returns an empty dict for anything that is not a recognisable Apple maker
    note.  Never raises: a malformed maker note is a missing signal, not an
    error worth failing a whole scan over.
    """
    if not blob or not blob.startswith(HEADER) or len(blob) < IFD_OFFSET + 2:
        return {}

    marker = blob[BYTE_ORDER_OFFSET:BYTE_ORDER_OFFSET + 2]
    if marker == b"MM":
        endian = ">"
    elif marker == b"II":
        endian = "<"
    else:
        return {}

    try:
        (entry_count,) = struct.unpack_from(endian + "H", blob, IFD_OFFSET)
    except struct.error:
        return {}

    found: dict[str, str] = {}
    for index in range(entry_count):
        offset = IFD_OFFSET + 2 + index * 12
        if offset + 12 > len(blob):
            break
        try:
            tag, field_type, count = struct.unpack_from(endian + "HHI", blob, offset)
        except struct.error:
            break

        name = TAG_NAMES.get(tag)
        if name is None or field_type != _ASCII:
            continue

        size = _TYPE_SIZES[_ASCII] * count
        if size == 0 or size > len(blob):
            continue
        if size <= 4:
            data = blob[offset + 8:offset + 8 + size]
        else:
            try:
                (pointer,) = struct.unpack_from(endian + "I", blob, offset + 8)
            except struct.error:
                continue
            if pointer + size > len(blob):
                continue
            data = blob[pointer:pointer + size]

        text = data.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        if text:
            found[name] = text

    return found
