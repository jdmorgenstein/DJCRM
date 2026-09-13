"""The matcher: decide whether a Google Photos file already exists in iCloud.

The cascade runs strongest evidence first and stops at the first tier that
fires.  Every tier above ``perceptual`` is exact in the sense that it cannot
produce a false positive on real camera output; ``perceptual`` is the only
judgement call, and it defaults to the review queue rather than to deletion.

The dangerous failure mode is not missing a duplicate -- that only costs
storage.  It is *falsely* calling two different photos duplicates, which
silently drops a photo you can no longer recover once Google Photos is gone.
Every threshold here is chosen with that asymmetry in mind.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from .hashing import bands, hamming

CERTAIN = "certain"
HIGH = "high"
REVIEW = "review"
UNIQUE = "unique"

TIER_CONTENT_ID = "apple-content-id"
TIER_BYTES = "file-bytes"
TIER_PIXELS = "decoded-pixels"
TIER_CAPTURE_PERCEPTUAL = "capture+perceptual"
TIER_CAPTURE_EXACT = "capture-exact"
TIER_PERCEPTUAL = "perceptual-only"
TIER_VIDEO = "video-capture"
TIER_NONE = "none"

DEFAULT_PHASH_THRESHOLD = 6
DEFAULT_DHASH_THRESHOLD = 12
# Perceptual evidence with no corroborating capture time has to clear a much
# higher bar, because burst frames are perceptually near-identical.
STRICT_PHASH_THRESHOLD = 4
STRICT_DHASH_THRESHOLD = 8

ASPECT_TOLERANCE = 0.02

FIELDS = (
    "id, library, path, relpath, filename, kind, size, file_sha256, pixel_sha256, "
    "phash, dhash, width, height, capture_local, capture_ms, capture_utc, "
    "camera_make, camera_model, content_id, burst_uuid, duration, album, takeout_title"
)


@dataclass
class Match:
    google_id: int
    icloud_id: int | None
    tier: str
    confidence: str
    distance: int | None = None
    note: str = ""


@dataclass
class Options:
    phash_threshold: int = DEFAULT_PHASH_THRESHOLD
    dhash_threshold: int = DEFAULT_DHASH_THRESHOLD
    time_tolerance: int = 0  # seconds of slack on UTC capture comparison
    allow_perceptual_only: bool = False  # promote tier 5 from review to high


class Library:
    """In-memory, query-shaped view of one indexed library."""

    def __init__(self, rows: list[sqlite3.Row], phash_bands: int, dhash_bands: int):
        self.rows = rows
        self.by_id = {row["id"]: row for row in rows}
        self.by_content_id: dict[str, list[sqlite3.Row]] = defaultdict(list)
        self.by_file_hash: dict[str, list[sqlite3.Row]] = defaultdict(list)
        self.by_pixel_hash: dict[str, list[sqlite3.Row]] = defaultdict(list)
        self.by_local: dict[str, list[sqlite3.Row]] = defaultdict(list)
        self.by_utc: dict[int, list[sqlite3.Row]] = defaultdict(list)
        self.phash_bands = phash_bands
        self.dhash_bands = dhash_bands
        self.phash_index: dict[tuple[int, int], list[sqlite3.Row]] = defaultdict(list)
        self.dhash_index: dict[tuple[int, int], list[sqlite3.Row]] = defaultdict(list)

        for row in rows:
            if row["content_id"]:
                self.by_content_id[row["content_id"]].append(row)
            if row["file_sha256"]:
                self.by_file_hash[row["file_sha256"]].append(row)
            if row["pixel_sha256"]:
                self.by_pixel_hash[row["pixel_sha256"]].append(row)
            if row["capture_local"]:
                self.by_local[row["capture_local"]].append(row)
            if row["capture_utc"]:
                self.by_utc[row["capture_utc"]].append(row)
            if row["phash"] is not None:
                for key in bands(row["phash"], phash_bands):
                    self.phash_index[key].append(row)
            if row["dhash"] is not None:
                for key in bands(row["dhash"], dhash_bands):
                    self.dhash_index[key].append(row)

    def perceptual_candidates(self, row: sqlite3.Row) -> dict[int, sqlite3.Row]:
        """Rows sharing at least one hash band -- an exact candidate superset."""
        found: dict[int, sqlite3.Row] = {}
        if row["phash"] is not None:
            for key in bands(row["phash"], self.phash_bands):
                for candidate in self.phash_index.get(key, ()):
                    found[candidate["id"]] = candidate
        if row["dhash"] is not None:
            for key in bands(row["dhash"], self.dhash_bands):
                for candidate in self.dhash_index.get(key, ()):
                    found[candidate["id"]] = candidate
        return found


def load_library(connection: sqlite3.Connection, library: str, options: Options) -> Library:
    rows = list(connection.execute(f"SELECT {FIELDS} FROM media WHERE library = ?", (library,)))
    return Library(rows, options.phash_threshold + 1, options.dhash_threshold + 1)


def same_instant(google: sqlite3.Row, icloud: sqlite3.Row, tolerance: int) -> bool | None:
    """True / False / None (unknown) for "these are the same moment of capture"."""
    g_local, i_local = google["capture_local"], icloud["capture_local"]
    if g_local and i_local:
        if g_local != i_local:
            return False
        g_ms, i_ms = google["capture_ms"], icloud["capture_ms"]
        if g_ms is not None and i_ms is not None and g_ms != i_ms:
            return False  # different frames of the same burst
        return True

    g_utc, i_utc = google["capture_utc"], icloud["capture_utc"]
    if g_utc and i_utc:
        return abs(g_utc - i_utc) <= tolerance
    return None


def cameras_agree(google: sqlite3.Row, icloud: sqlite3.Row) -> bool:
    """False only when both sides name a camera and the names differ."""
    g_model, i_model = google["camera_model"], icloud["camera_model"]
    if g_model and i_model and g_model.strip() != i_model.strip():
        return False
    return True


def aspects_agree(google: sqlite3.Row, icloud: sqlite3.Row) -> bool:
    """True unless both sides have dimensions and the shapes differ."""
    if not all((google["width"], google["height"], icloud["width"], icloud["height"])):
        return True
    g_ratio = google["width"] / google["height"]
    i_ratio = icloud["width"] / icloud["height"]
    return abs(g_ratio - i_ratio) <= ASPECT_TOLERANCE * max(g_ratio, i_ratio)


def bursts_agree(google: sqlite3.Row, icloud: sqlite3.Row) -> bool:
    """False when both carry a burst id and the ids differ."""
    g_burst, i_burst = google["burst_uuid"], icloud["burst_uuid"]
    if g_burst and i_burst and g_burst != i_burst:
        return False
    return True


def _perceptual_distance(google: sqlite3.Row, icloud: sqlite3.Row) -> tuple[int | None, int | None]:
    p_distance = (
        hamming(google["phash"], icloud["phash"])
        if google["phash"] is not None and icloud["phash"] is not None
        else None
    )
    d_distance = (
        hamming(google["dhash"], icloud["dhash"])
        if google["dhash"] is not None and icloud["dhash"] is not None
        else None
    )
    return p_distance, d_distance


def match_one(google: sqlite3.Row, icloud: Library, options: Options) -> Match:
    """Run the cascade for a single Google file."""
    # Tier 1: Apple's per-capture asset id.  Identical id means identical
    # capture, whatever the codec, resolution or metadata say.
    if google["content_id"]:
        for candidate in icloud.by_content_id.get(google["content_id"], ()):
            if candidate["kind"] == google["kind"]:
                return Match(google["id"], candidate["id"], TIER_CONTENT_ID, CERTAIN,
                             note="Apple ContentIdentifier")

    # Tier 2: byte-identical file.
    if google["file_sha256"]:
        for candidate in icloud.by_file_hash.get(google["file_sha256"], ()):
            return Match(google["id"], candidate["id"], TIER_BYTES, CERTAIN, 0,
                         "identical bytes")

    # Tier 3: identical decoded pixels -- same picture, rewritten container or
    # stripped metadata.  This is the tier that file hashing misses.
    if google["pixel_sha256"]:
        for candidate in icloud.by_pixel_hash.get(google["pixel_sha256"], ()):
            return Match(google["id"], candidate["id"], TIER_PIXELS, CERTAIN, 0,
                         "identical decoded pixels")

    if google["kind"] == "video":
        return _match_video(google, icloud, options)

    # Tiers 4 and 5 both need perceptual candidates.
    candidates = icloud.perceptual_candidates(google)

    # Tier 4: same capture instant, corroborated perceptually.  This is the
    # workhorse for HEIC->JPEG and "Storage saver" re-encodes.
    best: tuple[int, sqlite3.Row] | None = None
    for candidate in candidates.values():
        if same_instant(google, candidate, options.time_tolerance) is not True:
            continue
        if not cameras_agree(google, candidate) or not bursts_agree(google, candidate):
            continue
        p_distance, d_distance = _perceptual_distance(google, candidate)
        if p_distance is None and d_distance is None:
            continue
        if p_distance is not None and p_distance > options.phash_threshold:
            continue
        if d_distance is not None and d_distance > options.dhash_threshold:
            continue
        score = p_distance if p_distance is not None else d_distance
        if best is None or score < best[0]:
            best = (score, candidate)
    if best is not None:
        return Match(google["id"], best[1]["id"], TIER_CAPTURE_PERCEPTUAL, CERTAIN,
                     best[0], "same capture instant, perceptually identical")

    # Tier 4b: capture instant matches to the sub-second but the picture could
    # not be compared (unreadable, or one side has no perceptual hash).
    exact_time = _capture_only_match(google, icloud, options)
    if exact_time is not None:
        return exact_time

    # Tier 5: perceptual evidence alone.  Held for review by default.
    near: tuple[int, int, sqlite3.Row] | None = None
    conflicts = 0
    for candidate in candidates.values():
        if same_instant(google, candidate, options.time_tolerance) is False:
            continue
        if not cameras_agree(google, candidate) or not bursts_agree(google, candidate):
            continue
        if not aspects_agree(google, candidate):
            continue
        p_distance, d_distance = _perceptual_distance(google, candidate)
        if p_distance is None or d_distance is None:
            continue
        if p_distance > STRICT_PHASH_THRESHOLD or d_distance > STRICT_DHASH_THRESHOLD:
            continue
        conflicts += 1
        if near is None or (p_distance, d_distance) < (near[0], near[1]):
            near = (p_distance, d_distance, candidate)

    if near is not None:
        note = "perceptually identical, capture time unconfirmed"
        if conflicts > 1:
            note += f" ({conflicts} candidates -- possible burst)"
        confidence = HIGH if options.allow_perceptual_only and conflicts == 1 else REVIEW
        return Match(google["id"], near[2]["id"], TIER_PERCEPTUAL, confidence, near[0], note)

    return Match(google["id"], None, TIER_NONE, UNIQUE, None, "no counterpart in iCloud")


def _capture_only_match(google: sqlite3.Row, icloud: Library, options: Options) -> Match | None:
    """Sub-second capture identity with no usable perceptual comparison.

    Requires a sub-second timestamp on both sides: whole-second equality alone
    is not enough to separate burst frames.
    """
    if google["capture_ms"] is None or not google["capture_local"]:
        return None
    for candidate in icloud.by_local.get(google["capture_local"], ()):
        if candidate["capture_ms"] != google["capture_ms"]:
            continue
        if not cameras_agree(google, candidate) or not bursts_agree(google, candidate):
            continue
        if candidate["phash"] is not None and google["phash"] is not None:
            continue  # tier 4 already had its chance and declined
        return Match(google["id"], candidate["id"], TIER_CAPTURE_EXACT, HIGH, None,
                     "identical sub-second capture time")
    return None


def _match_video(google: sqlite3.Row, icloud: Library, options: Options) -> Match:
    """Videos have no perceptual hash here; identity leans on capture + duration."""
    pools: list[sqlite3.Row] = []
    if google["capture_local"]:
        pools += icloud.by_local.get(google["capture_local"], [])
    if google["capture_utc"]:
        pools += icloud.by_utc.get(google["capture_utc"], [])

    for candidate in pools:
        if candidate["kind"] != "video":
            continue
        if same_instant(google, candidate, options.time_tolerance) is False:
            continue
        g_duration, i_duration = google["duration"], candidate["duration"]
        if g_duration and i_duration and abs(g_duration - i_duration) > 1.0:
            continue
        confidence = HIGH if (g_duration and i_duration) else REVIEW
        return Match(google["id"], candidate["id"], TIER_VIDEO, confidence, None,
                     "same capture time and duration")
    return Match(google["id"], None, TIER_NONE, UNIQUE, None, "no counterpart in iCloud")


def link_live_pairs(rows: list[sqlite3.Row], results: list[Match]) -> int:
    """Keep a Live Photo's still and clip on the same side of the verdict.

    Takeout splits a Live Photo into ``IMG_1.HEIC`` plus ``IMG_1.MP4``.  If one
    half is a duplicate and the other is not, importing only the survivor
    produces a broken asset -- so if either half is unique, both are kept.
    """
    from pathlib import Path

    verdicts = {match.google_id: match for match in results}
    pairs: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        path = Path(row["path"])
        pairs[(str(path.parent), path.stem.lower())].append(row)

    promoted = 0
    for members in pairs.values():
        kinds = {member["kind"] for member in members}
        if len(members) < 2 or not {"image", "video"} <= kinds:
            continue
        if not any(verdicts[member["id"]].confidence == UNIQUE for member in members):
            continue
        for member in members:
            match = verdicts[member["id"]]
            if match.confidence != UNIQUE:
                match.icloud_id = None
                match.tier = TIER_NONE
                match.confidence = UNIQUE
                match.note = "kept: Live Photo partner is unique"
                promoted += 1
    return promoted


def run(connection: sqlite3.Connection, options: Options) -> dict[str, int]:
    """Match every Google row against the iCloud index and store the verdicts."""
    from . import db

    icloud = load_library(connection, "icloud", options)
    google = list(connection.execute(f"SELECT {FIELDS} FROM media WHERE library = 'google'"))

    db.clear_matches(connection)
    results = [match_one(row, icloud, options) for row in google]
    live_pairs_kept = link_live_pairs(google, results)

    connection.executemany(
        "INSERT OR REPLACE INTO matches (google_id, icloud_id, tier, confidence, distance, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(m.google_id, m.icloud_id, m.tier, m.confidence, m.distance, m.note) for m in results],
    )
    connection.commit()

    tally: dict[str, int] = defaultdict(int)
    for match in results:
        tally[match.confidence] += 1
        tally[f"tier:{match.tier}"] += 1
    tally["google_total"] = len(google)
    tally["icloud_total"] = len(icloud.rows)
    tally["live_pairs_kept"] = live_pairs_kept
    return dict(tally)
