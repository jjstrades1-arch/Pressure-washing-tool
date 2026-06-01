"""Subscription gating: what leads a contractor is entitled to see and unlock.

Two layers:

* **Candidates** — the leads a contractor may *browse* (in their area, above
  their plan's score floor, not exclusively owned or actively worked by someone
  else). Contact info is hidden here.
* **Reveals** — unlocking a candidate's contact details. The plan's
  ``monthly_lead_cap`` limits distinct reveals per billing period, which is what
  stops one cheap subscription from draining the whole inventory.

This module is the single place all of that is enforced.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone

from . import db

EARTH_KM = 6371.0088
# How long an actively-worked shared lead stays reserved for one contractor.
LOCK_DAYS = 14
# Most candidates to render on the dashboard at once (browse, not unlock).
DISPLAY_LIMIT = 250


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def access(conn: sqlite3.Connection, contractor_id: int):
    """Return the contractor's active subscription row, or None if locked."""
    return db.get_active_subscription(conn, contractor_id)


def _bbox(lat: float, lon: float, radius_km: float):
    """Rough lat/lon bounding box for a radius, for a cheap SQL prefilter."""
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def _lock_since() -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=LOCK_DAYS)
    ).isoformat(timespec="seconds")


def candidate_leads(conn: sqlite3.Connection, contractor_id: int) -> list[dict]:
    """Leads a contractor may browse (contact info hidden until revealed).

    Each dict carries the lead columns plus ``distance_km``, ``claim_status``,
    ``is_new`` (appeared since last login) and ``revealed`` (already unlocked).
    """
    sub = access(conn, contractor_id)
    if sub is None:
        return []
    areas = db.list_service_areas(conn, contractor_id)
    if not areas:
        return []

    contractor = db.get_contractor(conn, contractor_id)
    prev_login = contractor["prev_login"] if contractor else None
    period_start = sub["current_period_start"]
    revealed = db.revealed_lead_ids(conn, contractor_id, period_start)
    claims = db.claims_for_contractor(conn, contractor_id)
    lock_since = _lock_since()

    chosen: dict[int, dict] = {}
    for area in areas:
        south, north, west, east = _bbox(
            area["center_lat"], area["center_lon"], area["radius_km"]
        )
        rows = conn.execute(
            """SELECT * FROM leads
               WHERE is_active = 1 AND score >= ?
                 AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?""",
            (sub["min_lead_score"], south, north, west, east),
        ).fetchall()
        for row in rows:
            dist = haversine_km(
                area["center_lat"], area["center_lon"], row["lat"], row["lon"]
            )
            if dist > area["radius_km"]:
                continue
            lead_id = row["id"]
            # Exclusive leads belong to their first claimer forever.
            owner = db.lead_exclusive_owner(conn, lead_id)
            if owner is not None and owner != contractor_id:
                continue
            # Shared leads being actively worked are temporarily reserved.
            lock = db.lead_lock_owner(conn, lead_id, lock_since)
            if lock is not None and lock != contractor_id:
                continue
            prev = chosen.get(lead_id)
            if prev is not None and prev["distance_km"] <= dist:
                continue
            item = dict(row)
            item["distance_km"] = round(dist, 1)
            claim = claims.get(lead_id)
            item["claim_status"] = claim["status"] if claim else None
            item["is_new"] = bool(prev_login and row["first_seen"]
                                  and row["first_seen"] > prev_login)
            item["revealed"] = lead_id in revealed
            chosen[lead_id] = item

    leads = sorted(
        chosen.values(),
        key=lambda d: (not d["revealed"], -d["score"], d["distance_km"], d["name"]),
    )
    return leads[:DISPLAY_LIMIT]


def get_candidate(
    conn: sqlite3.Connection, contractor_id: int, lead_id: int
) -> dict | None:
    """Return one candidate lead (or None if the contractor may not see it)."""
    for lead in candidate_leads(conn, contractor_id):
        if lead["id"] == lead_id:
            return lead
    return None


def reveal_lead(conn: sqlite3.Connection, contractor_id: int, lead_id: int) -> str:
    """Try to unlock a lead's contact info. Returns a status string:

    'ok'      -> newly revealed (counts against the monthly cap)
    'already' -> was already revealed this period
    'limit'   -> monthly reveal cap reached
    'invalid' -> not a candidate for this contractor
    """
    sub = access(conn, contractor_id)
    if sub is None or get_candidate(conn, contractor_id, lead_id) is None:
        return "invalid"
    period_start = sub["current_period_start"]
    revealed = db.revealed_lead_ids(conn, contractor_id, period_start)
    if lead_id in revealed:
        return "already"
    if len(revealed) >= sub["monthly_lead_cap"]:
        return "limit"
    db.add_reveal(conn, contractor_id, lead_id, period_start)
    return "ok"


def reveal_usage(conn: sqlite3.Connection, contractor_id: int) -> tuple[int, int]:
    """Return (reveals used this period, monthly cap)."""
    sub = access(conn, contractor_id)
    if sub is None:
        return 0, 0
    used = db.reveal_count(conn, contractor_id, sub["current_period_start"])
    return used, sub["monthly_lead_cap"]


def revealed_leads(conn: sqlite3.Connection, contractor_id: int) -> list[dict]:
    """Leads the contractor has unlocked this period (with contact + claim)."""
    sub = access(conn, contractor_id)
    if sub is None:
        return []
    ids = db.revealed_lead_ids(conn, contractor_id, sub["current_period_start"])
    if not ids:
        return []
    claims = db.claims_for_contractor(conn, contractor_id)
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM leads WHERE id IN ({placeholders})", tuple(ids)
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        claim = claims.get(row["id"])
        item["claim_status"] = claim["status"] if claim else None
        out.append(item)
    out.sort(key=lambda d: (-d["score"], d["name"]))
    return out


def roi(conn: sqlite3.Connection, contractor_id: int) -> dict:
    """Return won-job value vs. subscription price for the ROI panel."""
    sub = access(conn, contractor_id)
    won_cents, won_count = db.won_summary(conn, contractor_id)
    return {
        "won_cents": won_cents,
        "won_count": won_count,
        "price_cents": sub["price_monthly_cents"] if sub else 0,
        "plan_name": sub["plan_name"] if sub else None,
    }
