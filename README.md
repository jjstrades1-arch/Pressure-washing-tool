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

## Run it as a business (subscription web app)

`pwleads` also ships a small web app that turns the lead finder into a
**subscription business**: pressure washing contractors sign up, subscribe to a
monthly plan, and log in to see a fresh, ranked, contact-ready list of leads in
their service area. You (the owner) keep the leads fresh; you make money on the
subscriptions and never take a cut of anyone's jobs.

> Payments are **simulated** in this version — subscribing activates instantly
> with no charge. The billing layer (`pwleads/billing.py`) has a clean seam where
> real Stripe drops in later with no rebuild.

### Start it locally

```bash
pip install .[web]          # installs Flask (the CLI core stays zero-dependency)
pwleads serve               # http://127.0.0.1:5000
```

Then, as the **owner**, seed the database and refresh leads:

```bash
pwleads plan seed                          # create Starter / Pro / Metro plans
pwleads scan "Kent, WA" --radius 8         # pull fresh leads from OpenStreetMap
pwleads enrich                             # fill in missing phone/email/address (free)
```

Or do it all from the browser: open `http://127.0.0.1:5000`, sign up as a
contractor, subscribe to a plan, add your town as a service area, then visit
`/admin` (password from `PWLEADS_ADMIN_PASSWORD`, default `admin`) to **Scan all
areas** and **Enrich**. Subscribers immediately see ranked leads on their dashboard.

### Plans (edit in `pwleads/db.py` → `DEFAULT_PLANS`)

| Plan | Price | Radius | Areas | Min score | Leads/mo | Leads |
|---|---|---|---|---|---|---|
| Starter | $49/mo | 8 km | 1 | 80+ | 25 | shared |
| Pro | $99/mo | 12 km | 2 | 60+ | 100 | shared |
| Metro | $199/mo | 16 km | 3 | all | 400 | exclusive |

### What a subscriber experiences

- **Instant leads.** Adding a service area kicks off a background scan (free
  OpenStreetMap data), so the dashboard fills on its own — no empty start.
- **Monthly unlocks.** Leads are browsable, but contact details unlock when you
  open a lead, capped per month by your plan. This is also what stops one cheap
  subscription from scraping the whole list — export only includes leads you've
  unlocked.
- **ROI tracking.** Mark a lead *won* with its dollar value and the dashboard
  shows what you've earned vs. your subscription cost.
- **New-since-last-visit** badges so returning users see what's fresh.
- **Fair sharing.** Once you're actively working a shared lead, it's reserved
  for you for two weeks so crews aren't all cold-calling the same business.
- **Outreach help.** Every lead comes with a ready call script and email
  template tailored to that business type — copy, paste, send.

### How the pieces fit

- `auth.py` — contractor accounts (hashed passwords).
- `billing.py` — `SimulatedBilling` now; `StripeBilling` later.
- `entitlements.py` — the gate: what each subscriber may see (area + plan score +
  monthly cap + exclusivity + active subscription).
- `scan.py` — re-scan service areas, track lead freshness (`first_seen`/`last_seen`,
  auto-deactivate closed businesses).
- `enrich.py` — free contact enrichment (extra OSM tags, reverse-geocode, website scrape).
- `web/` — the Flask app (signup → subscribe → dashboard → claim → export).

### Owner CLI commands

| Command | What it does |
|---|---|
| `pwleads serve` | run the web app locally |
| `pwleads plan seed` | create the default subscription plans |
| `pwleads scan <loc>` / `--all-areas` | refresh leads for an area / every subscribed area |
| `pwleads enrich` | fill in missing phone/email/address (free) |
| `pwleads contractor add --name --email --password [--plan]` | create an account from the CLI |

## Development

```bash
python3 -m unittest discover -s tests -v   # run the test suite (no network)
```

## License

MIT — see [LICENSE](LICENSE).
