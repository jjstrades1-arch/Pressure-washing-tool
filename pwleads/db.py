"""Local SQLite storage for the lead pipeline.

The database is a single file (default: ``pwleads.db`` in the working
directory). It is created on first use, so there's no setup step.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import quality
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

# --- Business layer (multi-tenant subscription model) ---
# The `leads` table above becomes shared inventory; everything below is the
# per-contractor business wrapped around it.
SCHEMA_BUSINESS = """
CREATE TABLE IF NOT EXISTS contractors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name TEXT NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    home_lat      REAL,
    home_lon      REAL,
    status        TEXT DEFAULT 'active',
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT UNIQUE NOT NULL,
    price_monthly_cents INTEGER NOT NULL,
    radius_km           REAL DEFAULT 8,
    max_areas           INTEGER DEFAULT 1,
    min_lead_score      INTEGER DEFAULT 0,
    monthly_lead_cap    INTEGER DEFAULT 50,
    exclusivity         TEXT DEFAULT 'shared'
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    contractor_id        INTEGER NOT NULL REFERENCES contractors(id),
    plan_id              INTEGER NOT NULL REFERENCES plans(id),
    provider             TEXT DEFAULT 'simulated',
    provider_ref         TEXT,
    status               TEXT DEFAULT 'active',
    current_period_start TEXT,
    current_period_end   TEXT
);
CREATE INDEX IF NOT EXISTS idx_subs_contractor ON subscriptions(contractor_id);

CREATE TABLE IF NOT EXISTS service_areas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contractor_id INTEGER NOT NULL REFERENCES contractors(id),
    label         TEXT NOT NULL,
    center_lat    REAL NOT NULL,
    center_lon    REAL NOT NULL,
    radius_km     REAL DEFAULT 8,
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_areas_contractor ON service_areas(contractor_id);

CREATE TABLE IF NOT EXISTS claims (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id        INTEGER NOT NULL REFERENCES leads(id),
    contractor_id  INTEGER NOT NULL REFERENCES contractors(id),
    status         TEXT DEFAULT 'new',
    notes          TEXT DEFAULT '',
    job_value_cents INTEGER DEFAULT 0,
    claimed_at     TEXT,
    updated_at     TEXT,
    UNIQUE(lead_id, contractor_id)
);
CREATE INDEX IF NOT EXISTS idx_claims_contractor ON claims(contractor_id);
CREATE INDEX IF NOT EXISTS idx_claims_lead ON claims(lead_id);

CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    area_label  TEXT,
    center_lat  REAL,
    center_lon  REAL,
    radius_km   REAL,
    started_at  TEXT,
    finished_at TEXT,
    found       INTEGER DEFAULT 0,
    added       INTEGER DEFAULT 0,
    refreshed   INTEGER DEFAULT 0,
    error       TEXT
);

-- A 'reveal' = a contractor unlocking a lead's contact info this billing
-- period. The plan's monthly_lead_cap limits distinct reveals, which is what
-- stops someone subscribing once and scraping the whole inventory.
CREATE TABLE IF NOT EXISTS reveals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contractor_id INTEGER NOT NULL REFERENCES contractors(id),
    lead_id       INTEGER NOT NULL REFERENCES leads(id),
    period_start  TEXT NOT NULL,
    created_at    TEXT,
    UNIQUE(contractor_id, lead_id, period_start)
);
CREATE INDEX IF NOT EXISTS idx_reveals_period
    ON reveals(contractor_id, period_start);

-- A 'bad lead' report under the credit-back guarantee. Covers lead *defects*
-- (dead number, closed, duplicate, wrong info) -- never "I didn't win the job".
CREATE TABLE IF NOT EXISTS lead_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contractor_id INTEGER NOT NULL REFERENCES contractors(id),
    lead_id       INTEGER NOT NULL REFERENCES leads(id),
    reason        TEXT NOT NULL,
    status        TEXT DEFAULT 'approved',
    period_start  TEXT,
    created_at    TEXT,
    UNIQUE(contractor_id, lead_id)
);
CREATE INDEX IF NOT EXISTS idx_reports_contractor ON lead_reports(contractor_id);
"""

# Columns added to `contractors` for "new since last login" tracking.
CONTRACTOR_COLUMNS_V3 = [
    ("prev_login", "TEXT"),
    ("last_login", "TEXT"),
]

# Columns added to `reveals` so a refunded unlock frees its monthly-cap slot.
REVEAL_COLUMNS = [
    ("refunded", "INTEGER DEFAULT 0"),
]

# Columns added to `leads` for freshness + enrichment. Applied idempotently so
# databases created by the original single-user CLI upgrade in place.
LEAD_COLUMNS_V2 = [
    ("first_seen", "TEXT"),
    ("last_seen", "TEXT"),
    ("is_active", "INTEGER DEFAULT 1"),
    ("email", "TEXT DEFAULT ''"),
    ("contact_name", "TEXT DEFAULT ''"),
    ("enriched_at", "TEXT"),
    ("exclusivity", "TEXT DEFAULT 'shared'"),
    ("brand", "TEXT DEFAULT ''"),
    ("is_chain", "INTEGER DEFAULT 0"),
    ("phone_type", "TEXT DEFAULT ''"),
    ("bad_reports", "INTEGER DEFAULT 0"),
]

# Default subscription tiers seeded on first run. Prices in cents.
DEFAULT_PLANS = [
    # name,      price,  radius, max_areas, min_score, cap, exclusivity
    ("Starter", 4900, 8.0, 1, 80, 25, "shared"),
    ("Pro", 9900, 12.0, 2, 60, 100, "shared"),
    ("Metro", 19900, 16.0, 3, 0, 400, "exclusive"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns) -> None:
    have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col, decl in columns:
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def migrate(conn: sqlite3.Connection) -> None:
    """Create/upgrade all tables idempotently."""
    conn.executescript(SCHEMA)
    conn.executescript(SCHEMA_BUSINESS)
    _add_missing_columns(conn, "leads", LEAD_COLUMNS_V2)
    _add_missing_columns(conn, "contractors", CONTRACTOR_COLUMNS_V3)
    _add_missing_columns(conn, "reveals", REVEAL_COLUMNS)


@contextmanager
def connect(path: str = DEFAULT_DB):
    """Open (and initialize) the database, yielding a connection."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_prospects(
    conn: sqlite3.Connection, prospects: list[Prospect], source_area: str
) -> tuple[int, int]:
    """Insert new prospects; refresh freshness on ones already stored.

    Returns (added, refreshed). New leads are inserted; existing leads (matched
    by osm_id) keep their status/notes but have ``last_seen``/``is_active``
    bumped so the feed reflects that the business is still there.
    """
    added = refreshed = 0
    now = _now()
    for p in prospects:
        cur = conn.execute("SELECT id FROM leads WHERE osm_id = ?", (p.osm_id,))
        row = cur.fetchone()
        if row is not None:
            conn.execute(
                "UPDATE leads SET last_seen = ?, is_active = 1 WHERE id = ?",
                (now, row["id"]),
            )
            refreshed += 1
            continue
        conn.execute(
            """
            INSERT INTO leads (osm_id, name, category, score, note, address,
                               city, phone, website, email, lat, lon, status,
                               notes, source_area, created_at, updated_at,
                               first_seen, last_seen, is_active,
                               brand, is_chain, phone_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?, ?, ?, ?, 1,
                    ?, ?, ?)
            """,
            (
                p.osm_id, p.name, p.category, p.score, p.note, p.address,
                p.city, p.phone, p.website, getattr(p, "email", ""), p.lat, p.lon,
                source_area, now, now, now, now,
                getattr(p, "brand", ""), getattr(p, "is_chain", 0),
                quality.classify_phone(p.phone),
            ),
        )
        added += 1
    return added, refreshed


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


# --------------------------------------------------------------------------- #
# Business layer CRUD (contractors, plans, subscriptions, areas, claims, scans)
# --------------------------------------------------------------------------- #

def seed_plans(conn: sqlite3.Connection) -> int:
    """Insert the default subscription tiers if the plans table is empty."""
    if conn.execute("SELECT COUNT(*) AS n FROM plans").fetchone()["n"]:
        return 0
    conn.executemany(
        """INSERT INTO plans (name, price_monthly_cents, radius_km, max_areas,
                              min_lead_score, monthly_lead_cap, exclusivity)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        DEFAULT_PLANS,
    )
    return len(DEFAULT_PLANS)


def list_plans(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM plans ORDER BY price_monthly_cents ASC"
    ).fetchall()


def get_plan(conn: sqlite3.Connection, plan_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()


# --- contractors ---
def create_contractor(
    conn: sqlite3.Connection,
    business_name: str,
    email: str,
    password_hash: str,
    home_lat: float | None = None,
    home_lon: float | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO contractors (business_name, email, password_hash,
                                    home_lat, home_lon, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (business_name, email.lower(), password_hash, home_lat, home_lon, _now()),
    )
    return int(cur.lastrowid)


def get_contractor(conn: sqlite3.Connection, contractor_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM contractors WHERE id = ?", (contractor_id,)
    ).fetchone()


def get_contractor_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM contractors WHERE email = ?", (email.lower(),)
    ).fetchone()


# --- service areas ---
def add_service_area(
    conn: sqlite3.Connection,
    contractor_id: int,
    label: str,
    center_lat: float,
    center_lon: float,
    radius_km: float,
) -> int:
    cur = conn.execute(
        """INSERT INTO service_areas (contractor_id, label, center_lat,
                                      center_lon, radius_km, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (contractor_id, label, center_lat, center_lon, radius_km, _now()),
    )
    return int(cur.lastrowid)


def list_service_areas(
    conn: sqlite3.Connection, contractor_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM service_areas WHERE contractor_id = ? ORDER BY id",
        (contractor_id,),
    ).fetchall()


def all_service_areas(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every service area across all contractors (used by the scan runner)."""
    return conn.execute("SELECT * FROM service_areas ORDER BY id").fetchall()


# --- subscriptions ---
def create_subscription(
    conn: sqlite3.Connection,
    contractor_id: int,
    plan_id: int,
    provider: str,
    provider_ref: str,
    period_start: str,
    period_end: str,
    status: str = "active",
) -> int:
    cur = conn.execute(
        """INSERT INTO subscriptions (contractor_id, plan_id, provider,
               provider_ref, status, current_period_start, current_period_end)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (contractor_id, plan_id, provider, provider_ref, status,
         period_start, period_end),
    )
    return int(cur.lastrowid)


def get_active_subscription(
    conn: sqlite3.Connection, contractor_id: int
) -> sqlite3.Row | None:
    """Most recent active subscription joined with its plan."""
    return conn.execute(
        """SELECT s.*, p.name AS plan_name, p.radius_km AS plan_radius_km,
                  p.max_areas, p.min_lead_score, p.monthly_lead_cap,
                  p.exclusivity, p.price_monthly_cents
           FROM subscriptions s JOIN plans p ON p.id = s.plan_id
           WHERE s.contractor_id = ? AND s.status = 'active'
           ORDER BY s.id DESC LIMIT 1""",
        (contractor_id,),
    ).fetchone()


def set_subscription_status(
    conn: sqlite3.Connection, subscription_id: int, status: str
) -> bool:
    cur = conn.execute(
        "UPDATE subscriptions SET status = ? WHERE id = ?",
        (status, subscription_id),
    )
    return cur.rowcount > 0


# --- claims (per-contractor pipeline) ---
def get_claim(
    conn: sqlite3.Connection, lead_id: int, contractor_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM claims WHERE lead_id = ? AND contractor_id = ?",
        (lead_id, contractor_id),
    ).fetchone()


def upsert_claim(
    conn: sqlite3.Connection,
    lead_id: int,
    contractor_id: int,
    status: str | None = None,
    notes: str | None = None,
    job_value_cents: int | None = None,
) -> int:
    """Create or update a contractor's claim on a lead. Returns the claim id."""
    now = _now()
    existing = get_claim(conn, lead_id, contractor_id)
    if existing is None:
        cur = conn.execute(
            """INSERT INTO claims (lead_id, contractor_id, status, notes,
                                   job_value_cents, claimed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, contractor_id, status or "new", notes or "",
             job_value_cents or 0, now, now),
        )
        return int(cur.lastrowid)
    sets, args = [], []
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if notes is not None:
        sets.append("notes = ?")
        args.append(notes)
    if job_value_cents is not None:
        sets.append("job_value_cents = ?")
        args.append(job_value_cents)
    if sets:
        sets.append("updated_at = ?")
        args.append(now)
        args.append(existing["id"])
        conn.execute(f"UPDATE claims SET {', '.join(sets)} WHERE id = ?", args)
    return int(existing["id"])


def claims_for_contractor(
    conn: sqlite3.Connection, contractor_id: int
) -> dict[int, sqlite3.Row]:
    """Map of lead_id -> claim row for one contractor."""
    rows = conn.execute(
        "SELECT * FROM claims WHERE contractor_id = ?", (contractor_id,)
    ).fetchall()
    return {row["lead_id"]: row for row in rows}


def lead_exclusive_owner(conn: sqlite3.Connection, lead_id: int) -> int | None:
    """Return the contractor_id that exclusively holds a lead, if any."""
    row = conn.execute(
        """SELECT c.contractor_id FROM claims c
           JOIN leads l ON l.id = c.lead_id
           WHERE c.lead_id = ? AND l.exclusivity = 'exclusive'
           ORDER BY c.claimed_at ASC LIMIT 1""",
        (lead_id,),
    ).fetchone()
    return row["contractor_id"] if row else None


# Statuses that mean a contractor is actively working a lead (soft-locks it).
WORKING_STATUSES = ("contacted", "quoted", "won")


def lead_lock_owner(
    conn: sqlite3.Connection, lead_id: int, since_iso: str
) -> int | None:
    """Contractor temporarily reserving a (shared) lead by actively working it.

    Returns the contractor_id whose working claim was updated since ``since_iso``,
    so other contractors don't all cold-call the same business at once.
    """
    placeholders = ", ".join("?" for _ in WORKING_STATUSES)
    row = conn.execute(
        f"""SELECT contractor_id FROM claims
            WHERE lead_id = ? AND status IN ({placeholders})
              AND updated_at >= ?
            ORDER BY updated_at ASC LIMIT 1""",
        (lead_id, *WORKING_STATUSES, since_iso),
    ).fetchone()
    return row["contractor_id"] if row else None


# --- login tracking ("new since last login") ---
def touch_login(conn: sqlite3.Connection, contractor_id: int) -> str | None:
    """Roll last_login -> prev_login and stamp a new last_login.

    Returns the previous login time (what 'new since last login' compares to).
    """
    row = get_contractor(conn, contractor_id)
    prev = row["last_login"] if row else None
    now = _now()
    conn.execute(
        "UPDATE contractors SET prev_login = ?, last_login = ? WHERE id = ?",
        (prev, now, contractor_id),
    )
    return prev


# --- reveals (cap accounting / anti-scraping) ---
def reveal_count(conn: sqlite3.Connection, contractor_id: int, period_start: str) -> int:
    """Reveals that count against the cap (refunded ones free their slot)."""
    return conn.execute(
        """SELECT COUNT(*) AS n FROM reveals
           WHERE contractor_id = ? AND period_start = ? AND refunded = 0""",
        (contractor_id, period_start),
    ).fetchone()["n"]


def revealed_lead_ids(
    conn: sqlite3.Connection, contractor_id: int, period_start: str
) -> set[int]:
    """Active (non-refunded) reveals -- what shows as unlocked and exports."""
    rows = conn.execute(
        """SELECT lead_id FROM reveals
           WHERE contractor_id = ? AND period_start = ? AND refunded = 0""",
        (contractor_id, period_start),
    ).fetchall()
    return {r["lead_id"] for r in rows}


def has_reveal(
    conn: sqlite3.Connection, contractor_id: int, lead_id: int, period_start: str
) -> bool:
    """Whether the contractor unlocked this lead this period (refunded or not).

    Used so revisiting a lead never double-charges, even after a refund.
    """
    return conn.execute(
        """SELECT 1 FROM reveals
           WHERE contractor_id = ? AND lead_id = ? AND period_start = ?""",
        (contractor_id, lead_id, period_start),
    ).fetchone() is not None


def reveal_time(
    conn: sqlite3.Connection, contractor_id: int, lead_id: int, period_start: str
) -> str | None:
    row = conn.execute(
        """SELECT created_at FROM reveals
           WHERE contractor_id = ? AND lead_id = ? AND period_start = ?""",
        (contractor_id, lead_id, period_start),
    ).fetchone()
    return row["created_at"] if row else None


def add_reveal(
    conn: sqlite3.Connection, contractor_id: int, lead_id: int, period_start: str
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO reveals (contractor_id, lead_id, period_start, created_at)
           VALUES (?, ?, ?, ?)""",
        (contractor_id, lead_id, period_start, _now()),
    )


def refund_reveal(
    conn: sqlite3.Connection, contractor_id: int, lead_id: int, period_start: str
) -> None:
    """Mark a reveal refunded, returning its slot to the monthly cap."""
    conn.execute(
        """UPDATE reveals SET refunded = 1
           WHERE contractor_id = ? AND lead_id = ? AND period_start = ?""",
        (contractor_id, lead_id, period_start),
    )


# --- credit-back guarantee (bad-lead reports) ---
def get_report(
    conn: sqlite3.Connection, contractor_id: int, lead_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM lead_reports WHERE contractor_id = ? AND lead_id = ?",
        (contractor_id, lead_id),
    ).fetchone()


def add_report(
    conn: sqlite3.Connection, contractor_id: int, lead_id: int,
    reason: str, status: str, period_start: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO lead_reports (contractor_id, lead_id, reason, status,
                                     period_start, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (contractor_id, lead_id, reason, status, period_start, _now()),
    )
    return int(cur.lastrowid)


def approved_report_count(
    conn: sqlite3.Connection, contractor_id: int, period_start: str
) -> int:
    return conn.execute(
        """SELECT COUNT(*) AS n FROM lead_reports
           WHERE contractor_id = ? AND period_start = ? AND status = 'approved'""",
        (contractor_id, period_start),
    ).fetchone()["n"]


def flag_bad_report(conn: sqlite3.Connection, lead_id: int) -> int:
    """Increment a lead's bad-report counter; return distinct reporter count."""
    conn.execute(
        "UPDATE leads SET bad_reports = bad_reports + 1 WHERE id = ?", (lead_id,)
    )
    return conn.execute(
        "SELECT COUNT(DISTINCT contractor_id) AS n FROM lead_reports "
        "WHERE lead_id = ? AND status = 'approved'",
        (lead_id,),
    ).fetchone()["n"]


def deactivate_lead(conn: sqlite3.Connection, lead_id: int) -> None:
    conn.execute("UPDATE leads SET is_active = 0 WHERE id = ?", (lead_id,))


def pending_reports(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.*, l.name AS lead_name, c.business_name
           FROM lead_reports r
           JOIN leads l ON l.id = r.lead_id
           JOIN contractors c ON c.id = r.contractor_id
           WHERE r.status = 'review' ORDER BY r.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()


def won_summary(conn: sqlite3.Connection, contractor_id: int) -> tuple[int, int]:
    """Return (total job value cents, number of won jobs) for a contractor."""
    row = conn.execute(
        """SELECT COALESCE(SUM(job_value_cents), 0) AS total, COUNT(*) AS n
           FROM claims WHERE contractor_id = ? AND status = 'won'""",
        (contractor_id,),
    ).fetchone()
    return int(row["total"]), int(row["n"])


# --- scans audit log ---
def record_scan(conn: sqlite3.Connection, **fields) -> int:
    cols = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO scans ({cols}) VALUES ({placeholders})",
        tuple(fields.values()),
    )
    return int(cur.lastrowid)


def recent_scans(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
