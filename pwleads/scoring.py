"""Lead categories and scoring for a pressure washing business.

Each rule maps an OpenStreetMap tag (key=value) to a human friendly
category, a base lead score (0-100) reflecting how likely that kind of
property is to need pressure washing and how valuable the job tends to be,
and a short note describing the typical service opportunity.

Higher scores = hotter leads. Scores are deliberately opinionated; tweak
them to match the jobs your business actually wins.
"""

from __future__ import annotations

# (key, value, category, score, service_note)
# Ordered roughly by lead quality. The first matching rule wins.
RULES = [
    # --- Fuel & automotive: big concrete forecourts, oil stains, canopies ---
    ("amenity", "fuel", "Gas station", 95,
     "Forecourt concrete, fuel islands, canopy, oil & gum stains"),
    ("amenity", "car_wash", "Car wash", 70,
     "Bays, lot, and equipment cleaning; recurring upkeep"),
    ("shop", "car_repair", "Auto repair shop", 80,
     "Oil-stained concrete, bay floors, exterior walls"),
    ("shop", "car", "Car dealership", 85,
     "Large lots, showroom exterior, display pads"),
    ("shop", "tyres", "Tire shop", 78,
     "Heavy rubber/oil staining on concrete aprons"),

    # --- Food service: grease, dumpster pads, sidewalks (recurring work) ---
    ("amenity", "fast_food", "Fast food", 92,
     "Drive-thru lanes, sidewalks, dumpster pad, grease on concrete"),
    ("amenity", "restaurant", "Restaurant", 88,
     "Entryways, patios, dumpster pad, kitchen exhaust grease"),
    ("amenity", "cafe", "Cafe / coffee shop", 80,
     "Patios, sidewalks, drive-thru; frequent repeat work"),
    ("amenity", "bar", "Bar", 75,
     "Sidewalks, patios, gum and spill staining"),
    ("amenity", "pub", "Pub", 75,
     "Sidewalks, patios, gum and spill staining"),
    ("amenity", "food_court", "Food court", 82,
     "High-traffic dining concourse and service areas"),

    # --- Retail & commercial: storefronts and parking lots ---
    ("shop", "mall", "Shopping mall", 90,
     "Huge parking decks, entry plazas, storefronts"),
    ("shop", "department_store", "Department store", 84,
     "Storefront, sidewalks, large parking lot"),
    ("shop", "supermarket", "Supermarket", 88,
     "Cart corrals, entry, dumpster pad, large lot"),
    ("shop", "doityourself", "Home improvement store", 82,
     "Garden center concrete, loading areas, large lot"),
    ("shop", "hardware", "Hardware store", 72,
     "Storefront and sidewalk cleaning"),
    ("shop", "convenience", "Convenience store", 80,
     "Forecourt, sidewalks, dumpster pad"),
    ("shop", "wholesale", "Wholesale / warehouse club", 85,
     "Massive lots and loading docks"),

    # --- Hospitality: image-sensitive, often on a maintenance schedule ---
    ("tourism", "hotel", "Hotel", 86,
     "Building exterior, walkways, pool deck, parking"),
    ("tourism", "motel", "Motel", 80,
     "Walkways, breezeways, parking, exterior"),
    ("amenity", "fuel_station", "Fuel station", 95,
     "Forecourt concrete and canopy"),

    # --- Institutional: large footprints, periodic contracts ---
    ("amenity", "school", "School", 74,
     "Walkways, courtyards, play areas, building exterior"),
    ("amenity", "hospital", "Hospital / clinic", 76,
     "Entrances, walkways, parking structures"),
    ("amenity", "place_of_worship", "Church / place of worship", 68,
     "Walkways, gathering areas, building exterior"),
    ("amenity", "parking", "Parking lot / garage", 84,
     "Decks, stalls, stairwells; oil and gum removal"),

    # --- Industrial / property managers (high job value) ---
    ("building", "warehouse", "Warehouse", 78,
     "Loading docks, aprons, exterior walls"),
    ("building", "industrial", "Industrial building", 76,
     "Equipment pads, docks, building washdowns"),
    ("building", "commercial", "Commercial building", 72,
     "Building exterior, entries, walkways"),
    ("building", "retail", "Retail building", 74,
     "Storefronts and sidewalks"),
    ("office", "company", "Office / business park", 70,
     "Building exterior, walkways, parking"),
    ("landuse", "industrial", "Industrial site", 70,
     "Yards, pads, and structures"),
]

# Score floor for a lead to be considered worth keeping by default.
DEFAULT_MIN_SCORE = 0

# Valid pipeline statuses, in workflow order.
STATUSES = ["new", "contacted", "quoted", "won", "lost", "skip"]


def _index():
    """Build a lookup: key -> {value: (category, score, note)}."""
    idx: dict[str, dict[str, tuple[str, int, str]]] = {}
    for key, value, category, score, note in RULES:
        idx.setdefault(key, {})[value] = (category, score, note)
    return idx


_INDEX = _index()


def tag_keys() -> list[str]:
    """OSM tag keys we care about (used to build the Overpass query)."""
    seen: list[str] = []
    for key, *_ in RULES:
        if key not in seen:
            seen.append(key)
    return seen


def classify(tags: dict[str, str]):
    """Return (category, score, note) for a set of OSM tags, or None.

    The first matching rule (in RULES order) wins, so higher-value
    categories take precedence when an element carries several tags.
    """
    for key, value, category, score, note in RULES:
        if tags.get(key) == value:
            return category, score, note
    return None
