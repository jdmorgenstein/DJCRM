"""SQLite index of both libraries.

One row per media file.  The index is the durable artefact: scanning a large
library is slow, matching against it is fast, so the two are separate commands.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id            INTEGER PRIMARY KEY,
    library       TEXT    NOT NULL,
    path          TEXT    NOT NULL,
    relpath       TEXT    NOT NULL,
    filename      TEXT    NOT NULL,
    kind          TEXT    NOT NULL,
    size          INTEGER,
    mtime         REAL,
    file_sha256   TEXT,
    pixel_sha256  TEXT,
    phash         INTEGER,
    dhash         INTEGER,
    width         INTEGER,
    height        INTEGER,
    capture_local TEXT,
    capture_ms    INTEGER,
    capture_utc   INTEGER,
    camera_make   TEXT,
    camera_model  TEXT,
    content_id    TEXT,
    burst_uuid    TEXT,
    apple_uid     TEXT,
    duration      REAL,
    album         TEXT,
    takeout_title TEXT,
    sidecar       TEXT,
    error         TEXT,
    UNIQUE (library, path)
);

CREATE INDEX IF NOT EXISTS idx_media_library    ON media (library);
CREATE INDEX IF NOT EXISTS idx_media_file_hash  ON media (library, file_sha256);
CREATE INDEX IF NOT EXISTS idx_media_pixel_hash ON media (library, pixel_sha256);
CREATE INDEX IF NOT EXISTS idx_media_content_id ON media (library, content_id);
CREATE INDEX IF NOT EXISTS idx_media_capture    ON media (library, capture_local);
CREATE INDEX IF NOT EXISTS idx_media_utc        ON media (library, capture_utc);

CREATE TABLE IF NOT EXISTS matches (
    google_id  INTEGER NOT NULL,
    icloud_id  INTEGER,
    tier       TEXT    NOT NULL,
    confidence TEXT    NOT NULL,
    distance   INTEGER,
    note       TEXT,
    PRIMARY KEY (google_id, icloud_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_conf ON matches (confidence);
"""

COLUMNS = [
    "library", "path", "relpath", "filename", "kind", "size", "mtime",
    "file_sha256", "pixel_sha256", "phash", "dhash", "width", "height",
    "capture_local", "capture_ms", "capture_utc", "camera_make", "camera_model",
    "content_id", "burst_uuid", "apple_uid", "duration", "album",
    "takeout_title", "sidecar", "error",
]


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the index database."""
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()
    return connection


def upsert_many(connection: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert or replace a batch of media rows."""
    if not rows:
        return
    placeholders = ", ".join("?" for _ in COLUMNS)
    statement = (
        f"INSERT INTO media ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT (library, path) DO UPDATE SET "
        + ", ".join(f"{column}=excluded.{column}" for column in COLUMNS if column not in ("library", "path"))
    )
    connection.executemany(statement, [[row.get(column) for column in COLUMNS] for row in rows])
    connection.commit()


def known_paths(connection: sqlite3.Connection, library: str) -> dict[str, tuple[int, float]]:
    """Existing (size, mtime) per path, so re-scans can skip unchanged files."""
    cursor = connection.execute(
        "SELECT path, size, mtime FROM media WHERE library = ? AND error IS NULL",
        (library,),
    )
    return {row["path"]: (row["size"], row["mtime"]) for row in cursor}


def clear_matches(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM matches")
    connection.commit()


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    cursor = connection.execute("SELECT library, COUNT(*) AS n FROM media GROUP BY library")
    return {row["library"]: row["n"] for row in cursor}
