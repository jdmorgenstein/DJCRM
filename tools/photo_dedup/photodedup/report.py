"""Reporting and staging: turn match verdicts into something you can import.

Takeout physically copies a photo into every album folder it belongs to *and*
into ``Photos from YYYY``, so the same picture can appear a dozen times in one
export.  Grouping collapses those copies back into one asset and unions their
album memberships, which is what makes the staged tree importable in a single
pass.
"""

from __future__ import annotations

import csv
import os
import shutil
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .matching import CERTAIN, HIGH, REVIEW, UNIQUE

UNSORTED_FOLDER = "_Unsorted"


@dataclass
class Group:
    """One distinct picture in the Google export, with every album it sits in."""

    key: str
    representative: sqlite3.Row
    albums: set[str] = field(default_factory=set)
    copies: list[sqlite3.Row] = field(default_factory=list)
    edited: bool = False


def _group_key(row: sqlite3.Row) -> str:
    if row["content_id"]:
        return "cid:" + row["content_id"]
    if row["pixel_sha256"]:
        return "px:" + row["pixel_sha256"]
    if row["file_sha256"]:
        return "sha:" + row["file_sha256"]
    return "path:" + row["path"]


def _is_edited(row: sqlite3.Row) -> bool:
    stem = Path(row["filename"]).stem.lower()
    return stem.endswith(("-edited", "-edite", "_edited", "-bearbeitet", "-editado"))


def fetch(connection: sqlite3.Connection, confidences: tuple[str, ...]) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in confidences)
    return list(connection.execute(
        f"""
        SELECT g.*, m.confidence, m.tier, m.distance, m.note, m.icloud_id
        FROM matches m
        JOIN media g ON g.id = m.google_id
        WHERE m.confidence IN ({placeholders})
        ORDER BY g.path
        """,
        confidences,
    ))


def group_unique(rows: list[sqlite3.Row]) -> list[Group]:
    """Collapse Takeout's duplicated copies into one group per picture."""
    groups: dict[str, Group] = {}
    for row in rows:
        key = _group_key(row)
        group = groups.get(key)
        if group is None:
            group = Group(key=key, representative=row)
            groups[key] = group
        elif (row["size"] or 0) > (group.representative["size"] or 0):
            group.representative = row
        group.copies.append(row)
        if row["album"]:
            group.albums.add(row["album"])
        group.edited = group.edited or _is_edited(row)
    return list(groups.values())


def summarise(connection: sqlite3.Connection) -> dict:
    tally = {row["confidence"]: row["n"] for row in connection.execute(
        "SELECT confidence, COUNT(*) AS n FROM matches GROUP BY confidence"
    )}
    tiers = {row["tier"]: row["n"] for row in connection.execute(
        "SELECT tier, COUNT(*) AS n FROM matches GROUP BY tier ORDER BY n DESC"
    )}
    totals = {row["library"]: row["n"] for row in connection.execute(
        "SELECT library, COUNT(*) AS n FROM media GROUP BY library"
    )}
    unique_rows = fetch(connection, (UNIQUE,))
    groups = group_unique(unique_rows)
    return {
        "totals": totals,
        "confidence": tally,
        "tiers": tiers,
        "unique_files": len(unique_rows),
        "unique_assets": len(groups),
        "unique_albums": sorted({album for group in groups for album in group.albums}),
    }


def _write_csv(path: Path, header: list[str], rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def write_reports(connection: sqlite3.Connection, outdir: Path) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    media = {row["id"]: row for row in connection.execute("SELECT * FROM media")}

    duplicates = fetch(connection, (CERTAIN, HIGH))
    _write_csv(
        outdir / "duplicates.csv",
        ["google_path", "icloud_path", "tier", "confidence", "distance", "note"],
        [
            [
                row["path"],
                media[row["icloud_id"]]["path"] if row["icloud_id"] in media else "",
                row["tier"], row["confidence"], row["distance"] or "", row["note"],
            ]
            for row in duplicates
        ],
    )

    review = fetch(connection, (REVIEW,))
    _write_csv(
        outdir / "review.csv",
        ["google_path", "icloud_path", "tier", "distance", "note"],
        [
            [
                row["path"],
                media[row["icloud_id"]]["path"] if row["icloud_id"] in media else "",
                row["tier"], row["distance"] or "", row["note"],
            ]
            for row in review
        ],
    )

    groups = group_unique(fetch(connection, (UNIQUE,)))
    _write_csv(
        outdir / "unique.csv",
        ["path", "filename", "albums", "capture_local", "capture_utc", "width", "height", "edited", "copies"],
        [
            [
                group.representative["path"], group.representative["filename"],
                "|".join(sorted(group.albums)),
                group.representative["capture_local"] or "",
                group.representative["capture_utc"] or "",
                group.representative["width"] or "", group.representative["height"] or "",
                "yes" if group.edited else "",
                len(group.copies),
            ]
            for group in groups
        ],
    )

    album_counts: dict[str, int] = defaultdict(int)
    for group in groups:
        for album in group.albums or {UNSORTED_FOLDER}:
            album_counts[album] += 1
    _write_csv(
        outdir / "albums.csv",
        ["album", "unique_assets"],
        sorted(album_counts.items(), key=lambda item: (-item[1], item[0])),
    )

    errors = list(connection.execute("SELECT library, path, error FROM media WHERE error IS NOT NULL"))
    _write_csv(outdir / "errors.csv", ["library", "path", "error"],
               [[row["library"], row["path"], row["error"]] for row in errors])

    return summarise(connection)


def _safe_name(name: str) -> str:
    cleaned = "".join("_" if character in '/\\:*?"<>|' else character for character in name)
    return cleaned.strip(" .") or "Album"


def album_members_in_icloud(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    """(album, iCloud file) for Google album members iCloud already holds."""
    return [
        (row["album"], row["path"])
        for row in connection.execute(
            """
            SELECT DISTINCT g.album AS album, i.path AS path
            FROM matches m
            JOIN media g ON g.id = m.google_id
            JOIN media i ON i.id = m.icloud_id
            WHERE m.confidence IN (?, ?) AND g.album IS NOT NULL AND g.album != ''
            ORDER BY g.album, i.path
            """,
            (CERTAIN, HIGH),
        )
    ]


def stage(
    connection: sqlite3.Connection,
    outdir: Path,
    *,
    include_review: bool = False,
    skip_edited: bool = False,
    copy: bool = False,
    rebuild_albums: bool = False,
) -> dict[str, int]:
    """Build an importable tree of the unique assets, one folder per album.

    Files are hard-linked by default, so staging a 200 GB export costs no extra
    disk.  A photo in several albums is linked into each of them; osxphotos
    then adds it to every matching album on import.

    ``rebuild_albums`` also stages the *iCloud* original of every album member
    that iCloud already has.  Those files are byte-identical to what Photos
    stored, so ``osxphotos import --skip-dups --dup-albums`` recognises them by
    fingerprint, imports nothing, and adds the photo already in the library to
    the album -- which reproduces the Google album in full without creating a
    single duplicate asset.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    confidences = (UNIQUE, REVIEW) if include_review else (UNIQUE,)
    groups = group_unique(fetch(connection, confidences))

    stats = {"assets": 0, "links": 0, "skipped_edited": 0, "failed": 0, "existing_album_members": 0}
    # (folder, original stem) -> staged stem, so a Live Photo's still and clip
    # keep a shared base name even when a collision forces a rename.
    stems: dict[tuple[str, str], str] = {}

    for group in groups:
        if skip_edited and group.edited:
            stats["skipped_edited"] += 1
            continue
        source = Path(group.representative["path"])
        if not source.exists():
            stats["failed"] += 1
            continue

        folders = sorted(_safe_name(album) for album in group.albums) or [UNSORTED_FOLDER]
        linked = False
        for folder in folders:
            directory = outdir / folder
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / (_staged_stem(stems, folder, source, directory) + source.suffix)
            if destination.exists():
                continue
            if _place(source, destination, copy=copy):
                stats["links"] += 1
                linked = True
            else:
                stats["failed"] += 1
        if linked:
            stats["assets"] += 1

    if rebuild_albums:
        for album, source_text in album_members_in_icloud(connection):
            source = Path(source_text)
            if not source.exists():
                stats["failed"] += 1
                continue
            folder = _safe_name(album)
            directory = outdir / folder
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / (_staged_stem(stems, folder, source, directory) + source.suffix)
            if destination.exists():
                continue
            if _place(source, destination, copy=copy):
                stats["existing_album_members"] += 1
                stats["links"] += 1
            else:
                stats["failed"] += 1

    return stats


def _staged_stem(stems: dict[tuple[str, str], str], folder: str, source: Path, directory: Path) -> str:
    """Stable staged stem for ``source`` inside ``folder``, avoiding collisions."""
    key = (folder, source.stem)
    if key in stems:
        return stems[key]

    stem = source.stem
    counter = 1
    while any(existing.stem == stem for existing in directory.glob(f"{stem}.*")):
        stem = f"{source.stem}~{counter}"
        counter += 1
    stems[key] = stem
    return stem


def _place(source: Path, destination: Path, *, copy: bool) -> bool:
    """Hard-link (or copy) ``source`` to ``destination``.  False on failure."""
    if not copy:
        try:
            os.link(source, destination)
            return True
        except OSError:
            pass  # different filesystem, or a filesystem without hard links
    try:
        shutil.copy2(source, destination)
        return True
    except OSError:
        return False
