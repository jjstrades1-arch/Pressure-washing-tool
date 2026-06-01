"""Lead *contactability* signals — separate from the job-value lead score.

A lead can be valuable (a chain gas station has a big oily forecourt) yet hard
to actually sell to (the only listed number is a national 1-800 line). These
helpers expose that distinction so the dashboard can tell a contractor which
leads they can call directly versus which ones to visit in person.
"""

from __future__ import annotations

import re

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


# How to act on each combination, shown to contractors as guidance.
def outreach_hint(is_chain: int, phone_type: str) -> str:
    if phone_type == "tollfree":
        return "Toll-free line — visit in person and ask for the manager."
    if is_chain:
        return "Chain location — best reached in person; ask for the manager."
    if phone_type == "direct":
        return "Direct local line — call and ask for the owner/manager."
    return "No phone yet — use the address to visit or look them up."
