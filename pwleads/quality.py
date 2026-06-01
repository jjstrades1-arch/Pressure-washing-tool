"""Lead *contactability* signals — separate from the job-value lead score.

A lead can be valuable (a chain gas station has a big oily forecourt) yet hard
to actually sell to (the only listed number is a national 1-800 line). These
helpers expose that distinction so the dashboard can tell a contractor which
leads they can call directly versus which ones to visit in person.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

STALE_DAYS = 45

# US/Canada toll-free area codes: a number on one of these never reaches a
# local manager -- it's corporate customer service.
TOLLFREE_CODES = {"800", "888", "877", "866", "855", "844", "833", "822"}


def classify_phone(phone: str) -> str:
    """Return 'direct', 'tollfree', 'unknown', or '' for a phone string.

    'direct'   -> a normal 10-digit local number (likely the location itself)
    'tollfree' -> an 8xx number (corporate / customer service, not the buyer)
    'unknown'  -> present but not a recognizable NANP number
    ''         -> no number
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return "unknown"
    return "tollfree" if digits[:3] in TOLLFREE_CODES else "direct"


def chain_from_tags(tags: dict) -> tuple[str, int]:
    """Detect a chain/franchise from OpenStreetMap tags.

    Returns (brand_name, is_chain). A ``brand`` tag (or a brand wikidata id)
    almost always means a national chain whose pressure-washing decision is
    made by a franchisee or corporate, not by whoever answers the store phone.
    """
    brand = (tags.get("brand") or tags.get("operator") or "").strip()
    is_chain = 1 if (tags.get("brand") or tags.get("brand:wikidata")) else 0
    return brand, is_chain


def confidence(lead, now: datetime | None = None) -> dict:
    """How likely a lead is to be *real and reachable* (distinct from its
    job-value score). Shown BEFORE a contractor unlocks it, so they know what
    they're paying for. Returns {'score', 'label', 'reasons'}.

    ``lead`` is any mapping with the lead columns (sqlite Row or dict).
    """
    now = now or datetime.now(timezone.utc)
    pts = 10  # baseline: a real, categorized business we recently scanned
    reasons: list[str] = []

    phone_type = _get(lead, "phone_type") or ""
    if phone_type == "direct":
        pts += 35
        reasons.append("Direct local phone line")
    elif _get(lead, "phone"):
        pts += 12
        reasons.append("Phone on file")
    if _get(lead, "email"):
        pts += 15
        reasons.append("Email on file")
    if _get(lead, "address"):
        pts += 15
        reasons.append("Street address known")
    if _get(lead, "website"):
        pts += 10
        reasons.append("Website on file")
    if not _get(lead, "is_chain"):
        pts += 10
        reasons.append("Independent — owner/manager reachable")
    else:
        reasons.append("Chain — decision often made off-site")

    last_seen = _get(lead, "last_seen")
    if last_seen:
        cutoff = (now - timedelta(days=STALE_DAYS)).isoformat(timespec="seconds")
        if last_seen < cutoff:
            pts -= 15
            reasons.append("Not seen recently — may be stale")
        else:
            reasons.append("Recently confirmed active")

    bad = _get(lead, "bad_reports") or 0
    if bad:
        pts -= min(bad, 3) * 15
        reasons.append(f"{bad} contractor(s) reported issues")

    score = max(0, min(100, pts))
    label = "High" if score >= 70 else "Medium" if score >= 45 else "Low"
    return {"score": score, "label": label, "reasons": reasons}


def _get(lead, key):
    """Read a key from a sqlite Row or dict, tolerating missing columns."""
    try:
        return lead[key]
    except (KeyError, IndexError):
        return None


# How to act on each combination, shown to contractors as guidance.
def outreach_hint(is_chain: int, phone_type: str) -> str:
    if phone_type == "tollfree":
        return "Toll-free line — visit in person and ask for the manager."
    if is_chain:
        return "Chain location — best reached in person; ask for the manager."
    if phone_type == "direct":
        return "Direct local line — call and ask for the owner/manager."
    return "No phone yet — use the address to visit or look them up."
