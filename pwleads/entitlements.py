"""Subscription gating: what leads a contractor is entitled to see.

Everything the dashboard shows routes through :func:`visible_leads`, so the
plan a contractor pays for (its area radius, minimum lead score, monthly cap
and exclusivity) is enforced in exactly one place.
"""

from __future__ import annotations

import math
import sqlite3

from . import db

EARTH_KM = 6371.0088


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


def visible_leads(conn: sqlite3.Connection, contractor_id: int) -> list[dict]:
    """Leads this contractor may see, ranked by score and capped by plan.

    Returns a list of dicts (lead columns + ``claim_status`` + ``distance_km``).
    Empty if the contractor has no active subscription or no service areas.
    """
    sub = access(conn, contractor_id)
    if sub is None:
        return []
    min_score = sub["min_lead_score"]
    cap = sub["monthly_lead_cap"]

    areas = db.list_service_areas(conn, contractor_id)
    if not areas:
        return []

    claims = db.claims_for_contractor(conn, contractor_id)

    chosen: dict[int, dict] = {}
    for area in areas:
        south, north, west, east = _bbox(
            area["center_lat"], area["center_lon"], area["radius_km"]
        )
        rows = conn.execute(
            """SELECT * FROM leads
               WHERE is_active = 1 AND score >= ?
                 AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?""",
            (min_score, south, north, west, east),
        ).fetchall()
        for row in rows:
            dist = haversine_km(
                area["center_lat"], area["center_lon"], row["lat"], row["lon"]
            )
            if dist > area["radius_km"]:
                continue
            lead_id = row["id"]
            # Respect exclusivity: hide leads exclusively held by someone else.
            owner = db.lead_exclusive_owner(conn, lead_id)
            if owner is not None and owner != contractor_id:
                continue
            prev = chosen.get(lead_id)
            if prev is not None and prev["distance_km"] <= dist:
                continue
            item = dict(row)
            item["distance_km"] = round(dist, 1)
            claim = claims.get(lead_id)
            item["claim_status"] = claim["status"] if claim else None
            chosen[lead_id] = item

    leads = sorted(
        chosen.values(), key=lambda d: (-d["score"], d["distance_km"], d["name"])
    )
    return leads[:cap]


def lead_visible_to(
    conn: sqlite3.Connection, contractor_id: int, lead_id: int
) -> dict | None:
    """Return one lead (with claim/distance info) if the contractor may see it."""
    for lead in visible_leads(conn, contractor_id):
        if lead["id"] == lead_id:
            return lead
    return None
