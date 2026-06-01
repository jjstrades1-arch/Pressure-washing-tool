"""Free lead enrichment: fill in missing address and contact details.

Strategy (all free, no paid APIs):
1. Reverse-geocode the lead's coordinates to fill a missing street/city.
2. If the lead has a website, fetch it and extract an email / phone number,
   respecting the site's robots.txt.

Enrichment is best-effort and polite: it is rate limited, caches the
robots.txt per host, and records ``enriched_at`` so re-runs skip recently
processed leads.
"""

from __future__ import annotations

import re
import sqlite3
import time
import urllib.parse
import urllib.robotparser
from datetime import datetime, timedelta, timezone

from . import db, quality, sources

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# US-style phone numbers; tolerant of (), spaces, dots and dashes.
PHONE_RE = re.compile(
    r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}"
)
# Junk that the email regex tends to catch on real pages.
_EMAIL_REJECT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
_EMAIL_REJECT_SUB = ("example.", "sentry.", "wixpress.", "@2x", "@3x", "your@", "name@")


def extract_email(text: str) -> str:
    """Return the first plausible email address in ``text``, or ''."""
    for match in EMAIL_RE.findall(text):
        low = match.lower()
        if low.endswith(_EMAIL_REJECT):
            continue
        if any(bad in low for bad in _EMAIL_REJECT_SUB):
            continue
        return match
    return ""


def extract_phone(text: str) -> str:
    """Return the first plausible phone number in ``text``, or ''."""
    for match in PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", match)
        if 10 <= len(digits) <= 11:
            return match.strip()
    return ""


def _robots_allows(url: str, cache: dict) -> bool:
    """Best-effort robots.txt check for a URL. Allows on any failure."""
    try:
        parts = urllib.parse.urlparse(url)
        host = f"{parts.scheme}://{parts.netloc}"
        rp = cache.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{host}/robots.txt")
            try:
                rp.read()
            except Exception:  # noqa: BLE001 - missing robots.txt => allowed
                cache[host] = True
                return True
            cache[host] = rp
        elif rp is True:
            return True
        return rp.can_fetch(sources.USER_AGENT, url)
    except Exception:  # noqa: BLE001
        return True


def enrich_lead(
    conn: sqlite3.Connection,
    lead: sqlite3.Row,
    robots_cache: dict | None = None,
    fetch=sources.fetch_text,
    reverse=sources.reverse_geocode,
) -> dict:
    """Enrich a single lead in place. Returns what changed.

    ``fetch`` and ``reverse`` are injectable so tests run without network.
    """
    robots_cache = robots_cache if robots_cache is not None else {}
    changes: dict[str, str] = {}

    # 1. Address backfill via reverse geocoding.
    if not lead["address"]:
        try:
            street, city = reverse(lead["lat"], lead["lon"])
        except sources.SourceError:
            street, city = "", ""
        if street:
            changes["address"] = street
        if city and not lead["city"]:
            changes["city"] = city

    # 2. Website scrape for email / phone.
    website = lead["website"]
    need_email = not lead["email"]
    need_phone = not lead["phone"]
    if website and (need_email or need_phone) and _robots_allows(website, robots_cache):
        html = fetch(website)
        if html:
            if need_email:
                email = extract_email(html)
                if email:
                    changes["email"] = email
            if need_phone:
                phone = extract_phone(html)
                if phone:
                    changes["phone"] = phone
                    changes["phone_type"] = quality.classify_phone(phone)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sets = [f"{col} = ?" for col in changes]
    args = list(changes.values())
    sets.append("enriched_at = ?")
    args.append(now)
    args.append(lead["id"])
    conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?", args)
    return changes


def pending_leads(
    conn: sqlite3.Connection, limit: int, stale_days: int = 30
) -> list[sqlite3.Row]:
    """Leads worth (re-)enriching: missing contact info and not done recently."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=stale_days)
    ).isoformat(timespec="seconds")
    return conn.execute(
        """SELECT * FROM leads
           WHERE is_active = 1
             AND (enriched_at IS NULL OR enriched_at < ?)
             AND ((email IS NULL OR email = '')
                  OR (phone IS NULL OR phone = '')
                  OR (address IS NULL OR address = ''))
           ORDER BY score DESC
           LIMIT ?""",
        (cutoff, limit),
    ).fetchall()


def enrich_pending(
    conn: sqlite3.Connection,
    limit: int = 50,
    delay: float = 1.0,
    fetch=sources.fetch_text,
    reverse=sources.reverse_geocode,
) -> dict:
    """Enrich a batch of pending leads. Returns a summary counter dict."""
    robots_cache: dict = {}
    leads = pending_leads(conn, limit)
    processed = updated = 0
    for lead in leads:
        changes = enrich_lead(conn, lead, robots_cache, fetch=fetch, reverse=reverse)
        processed += 1
        if changes:
            updated += 1
        if delay:
            time.sleep(delay)  # be polite to OSM and websites
    return {"processed": processed, "updated": updated}
