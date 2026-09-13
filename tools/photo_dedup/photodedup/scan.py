"""Walk a library and record every de-duplication signal into the index."""

from __future__ import annotations

import functools
import os
import sqlite3
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Iterator

from . import db, hashing, metadata, takeout

BATCH_SIZE = 200
EXIFTOOL_BATCH = 400

# Takeout ships these alongside the media; they are never photos.
SKIP_NAMES = {"print-subscriptions.json", "shared_album_comments.json", "user-generated-memory-titles.json"}
SKIP_DIRS = {".git", "__pycache__", ".photodedup"}


def default_workers() -> int:
    return min(8, os.cpu_count() or 2)


def iter_media(root: Path) -> Iterator[Path]:
    """Yield every image or video under ``root``, depth-first and sorted."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("@"))
        directory = Path(dirpath)
        for name in sorted(filenames):
            if name.startswith(".") or name in SKIP_NAMES:
                continue
            if metadata.media_kind(name) == "other":
                continue
            yield directory / name


def fingerprint(path_text: str, quick: bool = False) -> dict:
    """Compute content signals for one file.  Runs in a worker process.

    ``quick`` skips decoding the image, which is ~90% of the cost.  It still
    yields the file hash and (with exiftool) the capture metadata, which is
    everything tiers 1 and 2 need -- and at Original-quality backup those two
    tiers resolve the bulk of a library on their own.  Whatever they cannot
    resolve is decoded later by ``--upgrade``.
    """
    path = Path(path_text)
    row: dict = {"path": str(path), "error": None}
    try:
        stat = path.stat()
        row["size"] = stat.st_size
        row["mtime"] = stat.st_mtime
        row["file_sha256"] = hashing.file_sha256(path)
    except OSError as exc:
        row["error"] = f"stat/hash: {exc}"
        return row

    kind = metadata.media_kind(path)
    row["kind"] = kind
    if kind != "image":
        return row

    if quick:
        # Metadata still comes back: Image.open reads the header and EXIF
        # without decoding pixels, and the Apple ContentIdentifier that tier 1
        # runs on lives in EXIF.  Only the decode is skipped.
        _apply_metadata(row, metadata.extract_with_pillow(path), overwrite=False)
        return row

    try:
        image = hashing.decode(path)
    except Exception as exc:  # noqa: BLE001 - unreadable image is data, not a crash
        row["error"] = f"decode: {exc}"
        return row

    with image:
        row["pixel_sha256"] = hashing.pixel_sha256(image)
        phash, dhash = hashing.perceptual(image)
        # Stored signed: SQLite's INTEGER is signed 64-bit, the hashes are not.
        row["phash"] = hashing.to_signed(phash)
        row["dhash"] = hashing.to_signed(dhash)
        row["width"], row["height"] = image.size

    meta = metadata.extract_with_pillow(path)
    _apply_metadata(row, meta, overwrite=False)
    return row


def _apply_metadata(row: dict, meta: metadata.MediaMetadata, *, overwrite: bool) -> None:
    for field in (
        "capture_local", "capture_ms", "capture_utc", "camera_make", "camera_model",
        "content_id", "burst_uuid", "apple_uid", "duration",
    ):
        value = getattr(meta, field)
        if value is not None and (overwrite or row.get(field) is None):
            row[field] = value
    for field in ("width", "height"):
        value = getattr(meta, field)
        if value and row.get(field) is None:
            row[field] = value


def _chunks(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def scan_library(
    connection: sqlite3.Connection,
    root: Path,
    library: str,
    *,
    workers: int = 0,
    use_exiftool: bool = True,
    resume: bool = True,
    read_takeout: bool = False,
    quick: bool = False,
    only_paths: set[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Index every media file under ``root`` as ``library``.

    ``read_takeout`` turns on Google Takeout sidecar and album resolution.
    ``quick`` skips image decoding; ``only_paths`` restricts the walk to a
    named set, which is how ``--upgrade`` re-visits just the unresolved files.
    """
    root = root.resolve()
    _SIDECAR_CACHE.clear()
    files = list(iter_media(root))
    total = len(files)
    if only_paths is not None:
        files = [path for path in files if str(path) in only_paths]

    seen = db.known_paths(connection, library) if resume else {}
    pending: list[Path] = []
    for path in files:
        if resume and only_paths is None:
            previous = seen.get(str(path))
            if previous is not None:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if previous[0] == stat.st_size and previous[1] == stat.st_mtime:
                    continue
        pending.append(path)

    exiftool = use_exiftool and metadata.exiftool_available()
    workers = workers or default_workers()
    stats = {"total": total, "scanned": 0, "skipped": total - len(pending), "errors": 0}

    for batch in _chunks(pending, BATCH_SIZE):
        rows = _fingerprint_batch(batch, workers, quick)

        if exiftool:
            for sub in _chunks(batch, EXIFTOOL_BATCH):
                extracted = metadata.extract_with_exiftool(sub)
                for row in rows:
                    meta = extracted.get(row["path"])
                    if meta is not None:
                        _apply_metadata(row, meta, overwrite=True)

        for row, path in zip(rows, batch):
            row["library"] = library
            row["relpath"] = str(path.relative_to(root))
            row["filename"] = path.name
            row.setdefault("kind", metadata.media_kind(path))
            if read_takeout:
                _apply_takeout(row, path, root)
            if row.get("error"):
                stats["errors"] += 1

        db.upsert_many(connection, rows)
        stats["scanned"] += len(rows)
        if progress:
            progress(stats["scanned"], len(pending))

    return stats


def _fingerprint_batch(batch: list[Path], workers: int, quick: bool = False) -> list[dict]:
    if workers <= 1 or len(batch) == 1:
        return [fingerprint(str(path), quick) for path in batch]
    worker = functools.partial(fingerprint, quick=quick)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(worker, [str(path) for path in batch], chunksize=8))


# A --quick pass leaves images without pixel or perceptual hashes.  Only some of
# them ever need one, and which ones depends on how each remaining tier finds
# its candidates.
_UNRESOLVED_GOOGLE = """
    SELECT g.id, g.path, g.capture_local, g.capture_utc, g.width, g.height
    FROM media g
    LEFT JOIN matches m ON m.google_id = g.id
    WHERE g.library = 'google' AND g.kind = 'image' AND g.error IS NULL
      AND (m.confidence IS NULL OR m.confidence IN ('unique', 'review'))
"""


def paths_needing_upgrade(
    connection: sqlite3.Connection, library: str, *, thorough: bool = False
) -> set[str]:
    """Files a --quick pass left unfingerprinted that the match still needs.

    Every unresolved Google image needs decoding.  Which *iCloud* photos need
    decoding follows from the tiers that are still in play:

    * tier 4 blocks on capture time, so an iCloud photo sharing a capture
      instant with an unresolved Google file is a candidate;
    * tier 5 needs perceptual hashes on both sides, and only ever applies to
      photos with no capture time at all;
    * tier 3 compares decoded pixels and ignores capture time entirely -- but
      the pixel hash includes the dimensions, so it can only match an iCloud
      photo of exactly the same size as some unresolved Google file.

    The dimension clause deliberately covers *every* unresolved Google file,
    not just the ones missing a capture time.  Takeout supplies a capture time
    from the JSON sidecar even when it stripped the EXIF, so "has no capture
    time" does not identify the files tier 3 exists for, and using it as the
    predicate silently skips them.

    Tiers 3 and 4 are therefore covered exactly.  Tier 5 is not: it tolerates a
    rescale, so a downscaled counterpart has a different size and would not be
    selected.  Tier 5 only ever produces review-queue entries, so the cost is a
    possible duplicate to eyeball, never a lost photo -- and ``thorough``
    decodes every remaining iCloud image if you would rather rule it out.
    """
    unresolved = list(connection.execute(_UNRESOLVED_GOOGLE))

    if library == "google":
        have = {
            row["path"] for row in connection.execute(
                "SELECT path FROM media WHERE library = 'google' AND pixel_sha256 IS NOT NULL"
            )
        }
        return {row["path"] for row in unresolved} - have

    locals_ = {row["capture_local"] for row in unresolved if row["capture_local"]}
    utcs = {row["capture_utc"] for row in unresolved if row["capture_utc"]}
    sizes = {(row["width"], row["height"]) for row in unresolved if row["width"]}

    wanted = set()
    for row in connection.execute(
        "SELECT path, capture_local, capture_utc, width, height FROM media "
        "WHERE library = 'icloud' AND kind = 'image' AND pixel_sha256 IS NULL AND error IS NULL"
    ):
        if thorough:
            wanted.add(row["path"])
        elif row["capture_local"] is None and row["capture_utc"] is None:
            wanted.add(row["path"])
        elif row["capture_local"] in locals_ or row["capture_utc"] in utcs:
            wanted.add(row["path"])
        elif (row["width"], row["height"]) in sizes:
            wanted.add(row["path"])
    return wanted


# `iter_media` yields a directory's files contiguously, so a tiny cache gets
# the full benefit.  It is bounded because a large Takeout has thousands of
# directories and a year folder's index alone can hold tens of thousands of
# sidecar paths.
_SIDECAR_CACHE: OrderedDict[Path, takeout.SidecarIndex] = OrderedDict()
_SIDECAR_CACHE_SIZE = 4


def _apply_takeout(row: dict, path: Path, root: Path) -> None:
    """Attach Takeout sidecar facts and the album a file sits in."""
    directory = path.parent
    index = _SIDECAR_CACHE.get(directory)
    if index is None:
        index = takeout.SidecarIndex(directory)
        _SIDECAR_CACHE[directory] = index
        while len(_SIDECAR_CACHE) > _SIDECAR_CACHE_SIZE:
            _SIDECAR_CACHE.popitem(last=False)
    else:
        _SIDECAR_CACHE.move_to_end(directory)

    if directory != root:
        row["album"] = takeout.album_name(directory, index)

    sidecar = index.find(path.name)
    if sidecar is None:
        return
    record = takeout.load_sidecar(sidecar)
    row["sidecar"] = record.sidecar_path
    row["takeout_title"] = record.title
    # EXIF wins when present; the sidecar fills the gap for screenshots,
    # WhatsApp images and web uploads that carry no EXIF at all.
    if row.get("capture_utc") is None:
        row["capture_utc"] = record.photo_taken_utc or record.creation_utc
