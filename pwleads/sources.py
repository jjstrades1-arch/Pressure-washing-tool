"""Data sources: geocoding (Nominatim) and prospect lookup (Overpass).

Both are free OpenStreetMap services that require no API key. We keep the
HTTP layer in the standard library so the tool has zero dependencies.

Please be a good OSM citizen: these public endpoints are rate limited and
run on donations. The tool sends a descriptive User-Agent and avoids
hammering the servers.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import __version__, quality, scoring

USER_AGENT = f"pwleads/{__version__} (pressure-washing lead finder)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


class SourceError(RuntimeError):
    """Raised when an upstream service fails or returns nothing usable."""


@dataclass
class Place:
    """A geocoded location to search around."""

    display_name: str
    lat: float
    lon: float


@dataclass
class Prospect:
    """A candidate client discovered from OpenStreetMap."""

    osm_id: str
    name: str
    category: str
    score: int
    note: str
    address: str
    city: str
    phone: str
    website: str
    lat: float
    lon: float
    email: str = ""
    brand: str = ""
    is_chain: int = 0


def _get(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:  # noqa: BLE001 - surface a clean message
        raise SourceError(f"request failed: {exc}") from exc


def fetch_text(url: str, timeout: int = 12, max_bytes: int = 400_000) -> str:
    """Fetch a URL and return decoded text (best effort, size-capped).

    Used by enrichment to read a business website. Returns '' on any failure
    so enrichment never crashes on a dead or hostile page.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
    except Exception:  # noqa: BLE001 - enrichment is best-effort
        return ""
    return raw.decode("utf-8", errors="replace")


def reverse_geocode(lat: float, lon: float, timeout: int = 20) -> tuple[str, str]:
    """Resolve a coordinate to (street address, city) via Nominatim."""
    params = urllib.parse.urlencode(
        {"lat": lat, "lon": lon, "format": "json", "zoom": 18, "addressdetails": 1}
    )
    raw = _get(f"{NOMINATIM_REVERSE_URL}?{params}", timeout)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError("could not parse reverse-geocoding response") from exc
    addr = data.get("address", {})
    house = addr.get("house_number", "")
    road = addr.get("road", "")
    street = f"{house} {road}".strip()
    city = addr.get("city") or addr.get("town") or addr.get("village") or ""
    return street, city


def geocode(query: str, timeout: int = 20) -> Place:
    """Resolve a free-text location (e.g. 'Austin, TX') to a coordinate."""
    params = urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": 1}
    )
    raw = _get(f"{NOMINATIM_URL}?{params}", timeout)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError("could not parse geocoding response") from exc
    if not data:
        raise SourceError(f"no location found for {query!r}")
    top = data[0]
    return Place(
        display_name=top.get("display_name", query),
        lat=float(top["lat"]),
        lon=float(top["lon"]),
    )


def _build_overpass_query(lat: float, lon: float, radius_m: int, timeout: int) -> str:
    """Compose an Overpass QL query for all prospect tags around a point."""
    clauses = []
    for key, value, *_ in scoring.RULES:
        clauses.append(f'  nwr["{key}"="{value}"](around:{radius_m},{lat},{lon});')
    body = "\n".join(clauses)
    return f"[out:json][timeout:{timeout}];\n(\n{body}\n);\nout center tags;"


def _compose_address(tags: dict[str, str]) -> tuple[str, str]:
    """Return (street address, city) from OSM addr:* tags."""
    house = tags.get("addr:housenumber", "").strip()
    street = tags.get("addr:street", "").strip()
    parts = [p for p in [f"{house} {street}".strip()] if p]
    city = tags.get("addr:city", "") or tags.get("addr:town", "") or tags.get(
        "addr:village", ""
    )
    return ", ".join(parts), city.strip()


def find_prospects(
    place: Place,
    radius_km: float,
    timeout: int = 90,
    min_score: int = scoring.DEFAULT_MIN_SCORE,
) -> list[Prospect]:
    """Query Overpass for prospective clients near ``place``."""
    radius_m = int(radius_km * 1000)
    query = _build_overpass_query(place.lat, place.lon, radius_m, timeout)
    data_param = urllib.parse.urlencode({"data": query})
    raw = _get(f"{OVERPASS_URL}?{data_param}", timeout + 10)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError("could not parse Overpass response") from exc

    prospects: list[Prospect] = []
    seen: set[str] = set()
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        classified = scoring.classify(tags)
        if not classified:
            continue
        category, score, note = classified
        if score < min_score:
            continue

        osm_id = f"{el.get('type')}/{el.get('id')}"
        if osm_id in seen:
            continue
        seen.add(osm_id)

        # ways/relations carry a 'center'; nodes carry lat/lon directly.
        center = el.get("center", {})
        lat = el.get("lat", center.get("lat", place.lat))
        lon = el.get("lon", center.get("lon", place.lon))

        address, city = _compose_address(tags)
        name = tags.get("name") or tags.get("operator") or f"(unnamed {category.lower()})"
        brand, is_chain = quality.chain_from_tags(tags)

        prospects.append(
            Prospect(
                osm_id=osm_id,
                name=name,
                category=category,
                score=score,
                note=note,
                address=address,
                city=city,
                phone=tags.get("phone", "") or tags.get("contact:phone", ""),
                website=tags.get("website", "") or tags.get("contact:website", ""),
                lat=float(lat),
                lon=float(lon),
                email=tags.get("email", "") or tags.get("contact:email", ""),
                brand=brand,
                is_chain=is_chain,
            )
        )

    prospects.sort(key=lambda p: p.score, reverse=True)
    return prospects
