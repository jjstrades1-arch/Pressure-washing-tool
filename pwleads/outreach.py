"""Ready-to-use call scripts and email templates per lead category.

Contractors get a list of leads; this gives them the words to actually win the
job, tailored to what that kind of property cares about.
"""

from __future__ import annotations

# Category keyword -> the pain point / pitch angle that resonates for it.
_ANGLES = {
    "gas station": "oil-stained forecourts and fuel islands that scare off customers",
    "fast food": "greasy drive-thru lanes, sidewalks and the dumpster pad",
    "restaurant": "the entry, patio and dumpster area where customers form first impressions",
    "cafe": "patios and sidewalks that set the tone for foot traffic",
    "supermarket": "cart corrals, entryways and a parking lot that gets heavy traffic",
    "hotel": "building exteriors, walkways and pool decks guests judge you on",
    "motel": "walkways and exteriors that affect your reviews",
    "car dealership": "display pads and lots where a clean surface helps sell cars",
    "auto repair": "oil-stained concrete and bay aprons",
    "parking": "decks, stalls and stairwells with oil and gum buildup",
    "shopping mall": "entry plazas, storefronts and parking decks",
    "warehouse": "loading docks and aprons",
    "church": "walkways and gathering areas your congregation uses weekly",
    "school": "walkways, courtyards and play areas",
}
_DEFAULT_ANGLE = "the building exterior, walkways and parking that shape first impressions"


def _angle(category: str) -> str:
    cat = (category or "").lower()
    for key, angle in _ANGLES.items():
        if key in cat:
            return angle
    return _DEFAULT_ANGLE


def call_script(business_name: str, category: str, company: str = "our team") -> str:
    """A short, natural phone opener for this lead."""
    name = business_name or "there"
    return (
        f"Hi, this is {company}. I work with local {category.lower()}s on exterior "
        f"cleaning. I was by {name} and noticed {_angle(category)}. "
        f"We could make it look brand new — would it help if I sent a quick, free "
        f"quote for the manager or owner to look at?"
    )


def email_template(business_name: str, category: str, company: str = "Our team") -> dict:
    """A subject + body email a contractor can copy/paste and send."""
    name = business_name or "your business"
    subject = f"Quick exterior cleaning quote for {name}"
    body = (
        f"Hi {name} team,\n\n"
        f"I run {company}, a local pressure-washing service. I noticed "
        f"{_angle(category)} at your location and wanted to offer a free, no-"
        f"obligation quote to get it looking its best.\n\n"
        f"We're licensed, insured, and can usually work around your hours so there's "
        f"no disruption to customers. Even a one-time clean makes a big difference, "
        f"and many spots like yours put us on a simple recurring schedule.\n\n"
        f"Would it be alright if I stopped by or sent over a quick quote this week?\n\n"
        f"Thanks,\n{company}"
    )
    return {"subject": subject, "body": body}
