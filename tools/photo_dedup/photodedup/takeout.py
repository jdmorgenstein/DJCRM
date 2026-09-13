"""Google Takeout layout: sidecar resolution and album detection.

Takeout pairs every media file with a JSON sidecar, but the pairing rules are
genuinely hostile:

* the suffix changed from ``.json`` to ``.supplemental-metadata.json`` in 2024;
* the whole sidecar filename is truncated (historically at 51 characters), so
  the suffix arrives shortened -- ``.supplemental-metad.json``, ``.s.json``;
* numbered duplicates move the counter: ``IMG_1.jpg(1)`` pairs with
  ``IMG_1.jpg.supplemental-metadata(1).json``;
* ``-edited`` derivatives carry no sidecar of their own.

Resolution therefore runs candidate names first, then prefix matching, then a
reverse index built from each sidecar's own ``title`` field -- which is
authoritative, because Google writes the original filename into it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

SIDECAR_TOKEN = ".supplemental-metadata"
ALBUM_METADATA_NAMES = {"metadata.json", "metadata(1).json", "album-metadata.json"}

# Folders Takeout generates that are not user albums.
_YEAR_FOLDER = re.compile(r"^Photos from \d{4}$", re.IGNORECASE)
_NON_ALBUM_FOLDERS = {
    "google photos", "takeout", "archive", "trash", "bin",
    "untitled", "photos", "failed videos",
}

_COUNTER = re.compile(r"^(?P<stem>.*?)\((?P<counter>\d+)\)(?P<ext>\.[^.]*)$")
_EDITED = re.compile(r"[-_](edited|edite|editado|bearbeitet|modifié|ha editado)$", re.IGNORECASE)


@dataclass
class TakeoutRecord:
    """The subset of a Takeout sidecar that helps identify a capture."""

    title: str | None = None
    photo_taken_utc: int | None = None
    creation_utc: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    description: str | None = None
    favorited: bool = False
    sidecar_path: str | None = None


def _as_int(value: object) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number or None


def _as_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Takeout writes 0.0/0.0 for "no location"; treat that as absent.
    return number if number else None


def load_sidecar(path: Path) -> TakeoutRecord:
    """Parse a Takeout sidecar.  Returns an empty record for unreadable files."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return TakeoutRecord(sidecar_path=str(path))
    if not isinstance(payload, dict):
        return TakeoutRecord(sidecar_path=str(path))

    geo = payload.get("geoDataExif") or payload.get("geoData") or {}
    if not isinstance(geo, dict):
        geo = {}

    return TakeoutRecord(
        title=(payload.get("title") or None),
        photo_taken_utc=_as_int((payload.get("photoTakenTime") or {}).get("timestamp")),
        creation_utc=_as_int((payload.get("creationTime") or {}).get("timestamp")),
        latitude=_as_float(geo.get("latitude")),
        longitude=_as_float(geo.get("longitude")),
        description=(payload.get("description") or None),
        favorited=bool(payload.get("favorited")),
        sidecar_path=str(path),
    )


def split_counter(filename: str) -> tuple[str, int | None]:
    """``IMG_1.jpg(1)`` and ``IMG_1(1).jpg`` both reduce to ``IMG_1.jpg``, 1."""
    match = _COUNTER.match(filename)
    if match:
        return match["stem"] + match["ext"], int(match["counter"])
    if filename.endswith(")"):
        head, _, tail = filename.rpartition("(")
        digits = tail[:-1]
        if head and digits.isdigit():
            return head, int(digits)
    return filename, None


def strip_edited(filename: str) -> str:
    """``IMG_1-edited.jpg`` -> ``IMG_1.jpg`` (edited copies share a sidecar)."""
    stem, dot, extension = filename.rpartition(".")
    if not dot:
        return filename
    return _EDITED.sub("", stem) + dot + extension


class SidecarIndex:
    """Sidecar lookup for one Takeout directory."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.by_name: dict[str, Path] = {}
        self.album_title: str | None = None
        self._by_title: dict[str, list[Path]] | None = None

        for entry in sorted(directory.glob("*.json")):
            if entry.name in ALBUM_METADATA_NAMES:
                self.album_title = _read_album_title(entry) or self.album_title
                continue
            self.by_name[entry.name] = entry

    @property
    def by_title(self) -> dict[str, list[Path]]:
        """Reverse index built from each sidecar's ``title``.

        Built on demand: it costs a JSON parse per sidecar, and the name-based
        candidates resolve the overwhelming majority of files without it.
        """
        if self._by_title is None:
            self._by_title = {}
            for path in self.by_name.values():
                title = load_sidecar(path).title
                if title:
                    self._by_title.setdefault(title, []).append(path)
        return self._by_title

    def candidate_names(self, filename: str) -> list[str]:
        """Sidecar filenames Google may have used for ``filename``."""
        base, counter = split_counter(filename)
        names = [f"{filename}.json", f"{filename}{SIDECAR_TOKEN}.json"]
        if counter is not None:
            names += [
                f"{base}{SIDECAR_TOKEN}({counter}).json",
                f"{base}({counter}).json",
                f"{base}.json",
            ]
        unedited = strip_edited(base)
        if unedited != base:
            names += [f"{unedited}.json", f"{unedited}{SIDECAR_TOKEN}.json"]
        # Preserve order, drop repeats.
        return list(dict.fromkeys(names))

    def find(self, filename: str) -> Path | None:
        for candidate in self.candidate_names(filename):
            hit = self.by_name.get(candidate)
            if hit is not None:
                return hit

        # Truncated suffix: the sidecar name is a prefix of the full form.
        base, counter = split_counter(filename)
        full = f"{filename}{SIDECAR_TOKEN}"
        prefixes = [full]
        if counter is not None:
            prefixes.append(f"{base}{SIDECAR_TOKEN}")
        for name, path in self.by_name.items():
            stem = name[:-5] if name.endswith(".json") else name
            stem_base, stem_counter = split_counter(stem)
            if counter is not None and stem_counter is not None and stem_counter != counter:
                continue
            probe = stem_base if stem_counter is not None else stem
            if len(probe) > len(filename) and any(p.startswith(probe) for p in prefixes):
                return path

        # Last resort: the sidecar names the media file in its own `title`.
        for title_key in (filename, base, strip_edited(base)):
            matches = self.by_title.get(title_key)
            if matches:
                return matches[0]
        return None


def _read_album_title(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    title = payload.get("title") if isinstance(payload, dict) else None
    return title or None


def album_name(directory: Path, index: SidecarIndex | None = None) -> str | None:
    """Album a Takeout directory represents, or None for year/system folders."""
    if index is not None and index.album_title:
        return index.album_title
    name = directory.name
    if _YEAR_FOLDER.match(name) or name.strip().lower() in _NON_ALBUM_FOLDERS:
        return None
    return name or None
