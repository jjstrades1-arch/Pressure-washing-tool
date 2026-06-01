"""Local SQLite storage for the lead pipeline.

The database is a single file (default: ``pwleads.db`` in the working
directory). It is created on first use, so there's no setup step.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .sources import Prospect

DEFAULT_DB = "pwleads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    osm_id      TEXT UNIQUE,
    name        TEXT NOT NULL,
    category    TEXT,
    score       INTEGER DEFAULT 0,
    note        TEXT,
    address     TEXT,
    city        TEXT,
    phone       TEXT,
    website     TEXT,
    lat         REAL,
    lon         REAL,
    status      TEXT DEFAULT 'new',
    notes       TEXT DEFAULT '',
    source_area TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_score  ON leads(score);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: str = DEFAULT_DB):
    """Open (and initialize) the database, yielding a connection."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_prospects(
    conn: sqlite3.Connection, prospects: list[Prospect], source_area: str
) -> tuple[int, int]:
    """Insert new prospects, skipping ones already stored (by osm_id).

    Returns (added, skipped). Existing leads are left untouched so your
    status and notes are never clobbered by a re-scan.
    """
    added = skipped = 0
    now = _now()
    for p in prospects:
        cur = conn.execute("SELECT 1 FROM leads WHERE osm_id = ?", (p.osm_id,))
        if cur.fetchone() is not None:
            skipped += 1
            continue
        conn.execute(
            """
            INSERT INTO leads (osm_id, name, category, score, note, address,
                               city, phone, website, lat, lon, status, notes,
                               source_area, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?, ?)
            """,
            (
                p.osm_id, p.name, p.category, p.score, p.note, p.address,
                p.city, p.phone, p.website, p.lat, p.lon, source_area, now, now,
            ),
        )
        added += 1
    return added, skipped


def query_leads(
    conn: sqlite3.Connection,
    status: str | None = None,
    min_score: int = 0,
    category: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM leads WHERE score >= ?"
    args: list = [min_score]
    if status:
        sql += " AND status = ?"
        args.append(status)
    if category:
        sql += " AND lower(category) LIKE ?"
        args.append(f"%{category.lower()}%")
    sql += " ORDER BY score DESC, name ASC"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return conn.execute(sql, args).fetchall()


def get_lead(conn: sqlite3.Connection, lead_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


def update_lead(
    conn: sqlite3.Connection,
    lead_id: int,
    status: str | None = None,
    notes: str | None = None,
) -> bool:
    """Update a lead's status and/or notes. Returns True if a row changed."""
    sets, args = [], []
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if notes is not None:
        sets.append("notes = ?")
        args.append(notes)
    if not sets:
        return False
    sets.append("updated_at = ?")
    args.append(_now())
    args.append(lead_id)
    cur = conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?", args)
    return cur.rowcount > 0


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM leads GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def db_exists(path: str = DEFAULT_DB) -> bool:
    return Path(path).exists()
