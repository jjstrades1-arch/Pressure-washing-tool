# pwleads — pressure washing lead finder

Find and manage prospective clients for a pressure washing business, straight
from your terminal. `pwleads` searches an area for the kinds of properties that
routinely need pressure washing — gas stations, fast food, restaurants,
storefronts, parking lots, hotels, auto shops, warehouses and more — scores
them as leads, and tracks them through your sales pipeline.

- **Free data, no API key.** Uses public [OpenStreetMap](https://www.openstreetmap.org)
  services (Nominatim for geocoding, Overpass for business data).
- **Zero dependencies.** Pure Python standard library. Runs anywhere with
  Python 3.9+.
- **Local & private.** Everything lives in a single SQLite file on your machine.

## Install

No install required — run it straight from the repo:

```bash
python3 -m pwleads --help
```

Or install it so you get a `pwleads` command on your PATH:

```bash
pip install .
pwleads --help
```

## Quick start

```bash
# 1. Find prospects within 5 km of a location
pwleads find "Austin, TX" --radius 5

# 2. Look at your hottest leads
pwleads list --min-score 85 --limit 20

# 3. See full detail (address, phone, website, map link, opportunity)
pwleads show 42

# 4. Work the pipeline as you call/quote them
pwleads update 42 --status contacted --notes "Spoke to GM, send quote Friday"
pwleads update 42 --status quoted
pwleads update 42 --status won

# 5. Check your pipeline and export for outreach
pwleads stats
pwleads export --min-score 80 -o hot_leads.csv
```

## How leads are scored

Each property is matched to a category with a base **lead score** (0–100) that
reflects how likely it is to need pressure washing and how valuable the work
tends to be. A few examples:

| Category | Score | Typical opportunity |
|---|---|---|
| Gas station | 95 | Forecourt concrete, fuel islands, canopy, oil & gum stains |
| Fast food | 92 | Drive-thru lanes, sidewalks, dumpster pad, grease |
| Shopping mall | 90 | Parking decks, entry plazas, storefronts |
| Restaurant / supermarket | 88 | Entryways, patios, dumpster pads, large lots |
| Hotel | 86 | Building exterior, walkways, pool deck, parking |
| Car dealership | 85 | Large lots, showroom exterior, display pads |
| Parking lot / garage | 84 | Decks, stalls, stairwells; oil & gum removal |

The full list lives in [`pwleads/scoring.py`](pwleads/scoring.py) — it's just a
table, so edit the scores to match the jobs *your* business actually wins.

## Commands

| Command | What it does |
|---|---|
| `find <location>` | Search an area and save new prospects (`--radius`, `--min-score`, `--show`) |
| `list` | List saved leads (`--status`, `--min-score`, `--category`, `--limit`) |
| `show <id>` | Full detail for one lead, including a map link |
| `update <id>` | Set `--status` and/or `--notes` |
| `export` | Write leads to CSV (`-o`, plus the same filters as `list`) |
| `stats` | Pipeline summary and win rate |

Pipeline statuses: `new → contacted → quoted → won` (or `lost` / `skip`).

Use `--db path/to/file.db` (before the command) to keep separate databases,
e.g. one per city or per crew.

## Notes & etiquette

- Re-running `find` on an area you've already scanned **won't overwrite** your
  status or notes — it only adds prospects that are new to your database.
- Some listings have no phone/website in OpenStreetMap; the `show` command
  always gives you a map link so you can find the property and door-knock.
- Nominatim and Overpass are free, donation-funded services with rate limits.
  Be reasonable — search the areas you actually work rather than scraping
  whole regions in a loop.

## Development

```bash
python3 -m unittest discover -s tests -v   # run the test suite (no network)
```

## License

MIT — see [LICENSE](LICENSE).
