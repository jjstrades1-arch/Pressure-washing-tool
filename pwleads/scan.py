"""Scheduled scanning: refresh leads for service areas and track freshness.

A scan re-runs the OpenStreetMap search for an area, inserts new prospects,
bumps ``last_seen`` on ones still present, and marks leads that have vanished
(closed businesses) ``is_active = 0``. Keeping the feed fresh is what a
subscription is actually paying for.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import db, sources
from .entitlements import _bbox, haversine_km


def scan_area(
    conn: sqlite3.Connection,
    label: str,
    lat: float,
    lon: float,
    radius_km: float,
    finder=None,
) -> dict:
    """Refresh one area. ``finder`` is injectable so tests avoid the network."""
    finder = finder or sources.find_prospects
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    place = sources.Place(display_name=label, lat=lat, lon=lon)
    error = None
    found = added = refreshed = deactivated = 0
    try:
        prospects = finder(place, radius_km=radius_km)
        found = len(prospects)
        added, refreshed = db.upsert_prospects(conn, prospects, label)
        seen = {p.osm_id for p in prospects}
        deactivated = _deactivate_stale(conn, lat, lon, radius_km, seen)
    except sources.SourceError as exc:
        error = str(exc)

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.record_scan(
        conn, area_label=label, center_lat=lat, center_lon=lon,
        radius_km=radius_km, started_at=started, finished_at=finished,
        found=found, added=added, refreshed=refreshed, error=error,
    )
    return {
        "label": label, "found": found, "added": added,
        "refreshed": refreshed, "deactivated": deactivated, "error": error,
    }


def _deactivate_stale(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    radius_km: float,
    seen_osm_ids: set[str],
) -> int:
    """Flag active in-area leads not seen in this run as inactive (closed)."""
    south, north, west, east = _bbox(lat, lon, radius_km)
    rows = conn.execute(
        """SELECT id, osm_id, lat, lon FROM leads
           WHERE is_active = 1
             AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?""",
        (south, north, west, east),
    ).fetchall()
    stale = [
        r["id"] for r in rows
        if r["osm_id"] not in seen_osm_ids
        and haversine_km(lat, lon, r["lat"], r["lon"]) <= radius_km
    ]
    for lead_id in stale:
        conn.execute("UPDATE leads SET is_active = 0 WHERE id = ?", (lead_id,))
    return len(stale)


def scan_location(
    conn: sqlite3.Connection, location: str, radius_km: float, finder=None
) -> dict:
    """Geocode a free-text location and scan it (used by the CLI)."""
    place = sources.geocode(location)
    return scan_area(conn, place.display_name, place.lat, place.lon, radius_km, finder)


def scan_all_areas(conn: sqlite3.Connection, finder=None) -> list[dict]:
    """Scan every distinct subscribed service area."""
    seen: set[tuple] = set()
    results = []
    for area in db.all_service_areas(conn):
        key = (
            round(area["center_lat"], 3),
            round(area["center_lon"], 3),
            round(area["radius_km"], 1),
        )
        if key in seen:
            continue
        seen.add(key)
        results.append(
            scan_area(
                conn, area["label"], area["center_lat"],
                area["center_lon"], area["radius_km"], finder,
            )
        )
    return results
