"""Walk a library and record every de-duplication signal into the index."""

from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Iterator

from . import db, hashing, metadata, takeout

BATCH_SIZE = 200
EXIFTOOL_BATCH = 400

# Takeout ships these alongside the media; they are never photos.
SKIP_NAMES = {"print-subscriptions.json", "shared_album_comments.json", "user-generated-memory-titles.json"}
SKIP_DIRS = {".git", "__pycache__", ".photodedup"}


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


def fingerprint(path_text: str) -> dict:
    """Compute every content signal for one file.  Runs in a worker process."""
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
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Index every media file under ``root`` as ``library``.

    ``read_takeout`` turns on Google Takeout sidecar and album resolution.
    """
    root = root.resolve()
    _SIDECAR_CACHE.clear()
    files = list(iter_media(root))
    total = len(files)

    seen = db.known_paths(connection, library) if resume else {}
    pending: list[Path] = []
    for path in files:
        if resume:
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
    workers = workers or min(8, (os.cpu_count() or 2))
    stats = {"total": total, "scanned": 0, "skipped": total - len(pending), "errors": 0}

    for batch in _chunks(pending, BATCH_SIZE):
        rows = _fingerprint_batch(batch, workers)

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


def _fingerprint_batch(batch: list[Path], workers: int) -> list[dict]:
    if workers <= 1 or len(batch) == 1:
        return [fingerprint(str(path)) for path in batch]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fingerprint, [str(path) for path in batch], chunksize=8))


_SIDECAR_CACHE: dict[Path, takeout.SidecarIndex] = {}


def _apply_takeout(row: dict, path: Path, root: Path) -> None:
    """Attach Takeout sidecar facts and the album a file sits in."""
    directory = path.parent
    index = _SIDECAR_CACHE.get(directory)
    if index is None:
        index = takeout.SidecarIndex(directory)
        _SIDECAR_CACHE[directory] = index

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
