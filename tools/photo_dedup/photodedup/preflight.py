"""Pre-flight checks.

At a hundred thousand photos, indexing is an overnight job and the export is
most of a terabyte.  The failures worth catching are the ones that waste that
time: a missing exiftool, no HEIC decoder, a disk that cannot hold the staging
tree, or -- the classic -- a Takeout that was only partly extracted, which makes
whole albums look unique and would import thousands of duplicates.
"""

from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import hashing, metadata, scan, takeout

OK, WARN, FAIL = "ok", "warn", "fail"

# Throughput measured on 12 MP JPEGs; HEIC is slower.
FILES_PER_SECOND_PER_CORE = 4.8
SIDECAR_SAMPLE = 400
SIDECAR_COVERAGE_FLOOR = 0.80


@dataclass
class Check:
    level: str
    title: str
    detail: str


@dataclass
class Survey:
    files: int = 0
    total_bytes: int = 0
    kinds: Counter = None
    albums: set = None

    def __post_init__(self):
        self.kinds = self.kinds or Counter()
        self.albums = self.albums if self.albums is not None else set()


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}"
        n /= 1024


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def check_tools() -> list[Check]:
    checks = []

    exiftool = shutil.which("exiftool")
    if exiftool:
        try:
            version = subprocess.run([exiftool, "-ver"], capture_output=True, text=True,
                                     timeout=20, check=False).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            version = "?"
        checks.append(Check(OK, "exiftool", f"{exiftool} (v{version})"))
    else:
        checks.append(Check(WARN, "exiftool", "not on PATH -- HEIC and video metadata will be "
                                              "limited, and HEIC is what iPhones shoot. "
                                              "`brew install exiftool`"))

    if hashing.HEIF_SUPPORTED:
        checks.append(Check(OK, "HEIC decoding", "pillow-heif registered"))
    else:
        checks.append(Check(FAIL, "HEIC decoding", "pillow-heif missing -- .HEIC files cannot be "
                                                   "fingerprinted at all. `pip install pillow-heif`"))

    if hashing.numpy is not None:
        checks.append(Check(OK, "numpy", "DCT accelerated"))
    else:
        checks.append(Check(WARN, "numpy", "missing -- pHash falls back to pure Python "
                                           "(roughly 2x slower overall)"))
    return checks


def check_disk(path: Path, needed_bytes: int = 0) -> Check:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    usage = shutil.disk_usage(target)
    detail = f"{_human(usage.free)} free on {target}"
    if needed_bytes and usage.free < needed_bytes:
        return Check(FAIL, "disk space", f"{detail}; need at least {_human(needed_bytes)}")
    if needed_bytes and usage.free < needed_bytes * 2:
        return Check(WARN, "disk space", f"{detail}; {_human(needed_bytes)} wanted, little headroom")
    return Check(OK, "disk space", detail)


def survey(root: Path) -> Survey:
    result = Survey()
    for path in scan.iter_media(root):
        result.files += 1
        result.kinds[metadata.media_kind(path)] += 1
        try:
            result.total_bytes += path.stat().st_size
        except OSError:
            pass
    return result


def check_takeout(root: Path, sample_size: int = SIDECAR_SAMPLE) -> list[Check]:
    """Sample the export and report sidecar coverage and album count."""
    checks: list[Check] = []
    seen = 0
    resolved = 0
    albums: set[str] = set()
    indexes: dict[Path, takeout.SidecarIndex] = {}

    for path in scan.iter_media(root):
        directory = path.parent
        index = indexes.get(directory)
        if index is None:
            if len(indexes) > 64:
                indexes.clear()
            index = takeout.SidecarIndex(directory)
            indexes[directory] = index
        if directory != root:
            album = takeout.album_name(directory, index)
            if album:
                albums.add(album)
        if seen < sample_size:
            seen += 1
            if index.find(path.name) is not None:
                resolved += 1

    if seen == 0:
        checks.append(Check(FAIL, "Takeout layout", f"no media found under {root} -- point --google "
                                                    "at the 'Google Photos' folder inside Takeout"))
        return checks

    coverage = resolved / seen
    detail = f"{resolved}/{seen} sampled files have a sidecar ({coverage:.0%})"
    if coverage < SIDECAR_COVERAGE_FLOOR:
        checks.append(Check(WARN, "Takeout sidecars", detail + " -- low coverage usually means not "
                                                              "every archive part was extracted, or "
                                                              "they were extracted to separate folders"))
    else:
        checks.append(Check(OK, "Takeout sidecars", detail))

    checks.append(Check(OK if albums else WARN, "Takeout albums",
                        f"{len(albums)} album folder(s) detected"
                        + ("" if albums else " -- album membership cannot be preserved")))
    return checks


def run(icloud: Path | None, google: Path | None, staging: Path | None, workers: int) -> list[Check]:
    checks = check_tools()
    staged_bytes = 0

    for label, root, is_takeout in (("iCloud", icloud, False), ("Google", google, True)):
        if root is None:
            continue
        if not root.is_dir():
            checks.append(Check(FAIL, f"{label} export", f"{root} is not a directory"))
            continue
        found = survey(root)
        estimate = found.files / (FILES_PER_SECOND_PER_CORE * max(1, workers))
        checks.append(Check(
            OK if found.files else FAIL, f"{label} export",
            f"{found.files:,} media files, {_human(found.total_bytes)} "
            f"({found.kinds.get('image', 0):,} images, {found.kinds.get('video', 0):,} videos); "
            f"indexing ~{_duration(estimate)} on {max(1, workers)} workers",
        ))
        if is_takeout and found.files:
            checks.extend(check_takeout(root))
            staged_bytes = found.total_bytes

    if staging is not None:
        # Staging hard-links, so it only costs real bytes across a filesystem
        # boundary; report the worst case so an --copy run is not a surprise.
        checks.append(check_disk(staging, staged_bytes))
    return checks
