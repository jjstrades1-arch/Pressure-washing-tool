"""Command line interface for pwleads."""

from __future__ import annotations

import argparse
import sys

from . import __version__, db, export, scoring, sources


def _fmt_phone_site(row) -> str:
    bits = []
    if row["phone"]:
        bits.append(row["phone"])
    if row["website"]:
        bits.append(row["website"])
    return "  |  ".join(bits)


def _print_table(rows) -> None:
    if not rows:
        print("No leads match. Try 'pwleads find <location>' first.")
        return
    print(f"{'ID':>4}  {'SCORE':>5}  {'STATUS':<9}  {'CATEGORY':<22}  NAME")
    print("-" * 78)
    for r in rows:
        print(
            f"{r['id']:>4}  {r['score']:>5}  {r['status']:<9}  "
            f"{(r['category'] or ''):<22.22}  {r['name']:.40}"
        )
    print(f"\n{len(rows)} lead(s).")


def cmd_find(args: argparse.Namespace) -> int:
    print(f"Geocoding {args.location!r} ...")
    try:
        place = sources.geocode(args.location)
    except sources.SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"  -> {place.display_name}")
    print(f"Searching within {args.radius} km for prospects (this can take a moment) ...")
    try:
        prospects = sources.find_prospects(
            place, radius_km=args.radius, min_score=args.min_score
        )
    except sources.SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not prospects:
        print("No prospects found in that area. Try a larger --radius.")
        return 0

    with db.connect(args.db) as conn:
        added, skipped = db.upsert_prospects(conn, prospects, place.display_name)

    print(
        f"\nFound {len(prospects)} prospect(s): "
        f"{added} new, {skipped} already in your database."
    )
    top = prospects[: args.show]
    print(f"\nTop {len(top)} by lead score:")
    print(f"{'SCORE':>5}  {'CATEGORY':<22}  NAME")
    print("-" * 60)
    for p in top:
        print(f"{p.score:>5}  {p.category:<22.22}  {p.name:.40}")
    print(f"\nSaved to {args.db}. Use 'pwleads list' to work the pipeline.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    with db.connect(args.db) as conn:
        rows = db.query_leads(
            conn,
            status=args.status,
            min_score=args.min_score,
            category=args.category,
            limit=args.limit,
        )
    _print_table(rows)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with db.connect(args.db) as conn:
        r = db.get_lead(conn, args.id)
    if not r:
        print(f"No lead with id {args.id}.", file=sys.stderr)
        return 1
    width = 14
    fields = [
        ("Name", r["name"]), ("Category", r["category"]),
        ("Score", r["score"]), ("Status", r["status"]),
        ("Phone", r["phone"] or "-"), ("Website", r["website"] or "-"),
        ("Address", r["address"] or "-"), ("City", r["city"] or "-"),
        ("Opportunity", r["note"] or "-"),
        ("Map", f"https://www.openstreetmap.org/?mlat={r['lat']}&mlon={r['lon']}#map=18/{r['lat']}/{r['lon']}"),
        ("Source area", r["source_area"] or "-"),
        ("Your notes", r["notes"] or "-"),
        ("Added", r["created_at"]), ("Updated", r["updated_at"]),
    ]
    print(f"Lead #{r['id']}")
    print("-" * 60)
    for label, value in fields:
        print(f"{label:<{width}}: {value}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    if args.status and args.status not in scoring.STATUSES:
        print(
            f"error: status must be one of {', '.join(scoring.STATUSES)}",
            file=sys.stderr,
        )
        return 1
    if args.status is None and args.notes is None:
        print("Nothing to update: pass --status and/or --notes.", file=sys.stderr)
        return 1
    with db.connect(args.db) as conn:
        ok = db.update_lead(conn, args.id, status=args.status, notes=args.notes)
    if not ok:
        print(f"No lead with id {args.id}.", file=sys.stderr)
        return 1
    print(f"Updated lead #{args.id}.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with db.connect(args.db) as conn:
        rows = db.query_leads(
            conn, status=args.status, min_score=args.min_score, category=args.category
        )
    n = export.write_csv(rows, args.output)
    print(f"Exported {n} lead(s) to {args.output}.")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    with db.connect(args.db) as conn:
        counts = db.status_counts(conn)
        total = sum(counts.values())
    if total == 0:
        print("No leads yet. Run 'pwleads find <location>' to get started.")
        return 0
    print(f"Pipeline ({total} lead(s)):")
    peak = max(counts.values()) or 1
    max_bar = 40
    for status in scoring.STATUSES:
        n = counts.get(status, 0)
        bar = "#" * round(n / peak * max_bar) if n else ""
        print(f"  {status:<9} {n:>5}  {bar}")
    won = counts.get("won", 0)
    worked = total - counts.get("new", 0)
    if worked:
        print(f"\nWin rate (of worked leads): {won / worked * 100:.0f}%")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pwleads",
        description="Find and manage leads for a pressure washing business.",
    )
    p.add_argument("--version", action="version", version=f"pwleads {__version__}")
    p.add_argument(
        "--db", default=db.DEFAULT_DB,
        help=f"database file (default: {db.DEFAULT_DB})",
    )
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("find", help="search an area for prospective clients")
    f.add_argument("location", help="city/area to search, e.g. 'Austin, TX'")
    f.add_argument("--radius", type=float, default=5.0,
                   help="search radius in km around the location (default: 5)")
    f.add_argument("--min-score", type=int, default=scoring.DEFAULT_MIN_SCORE,
                   help="ignore prospects below this lead score")
    f.add_argument("--show", type=int, default=15,
                   help="how many top prospects to print (default: 15)")
    f.set_defaults(func=cmd_find)

    ls = sub.add_parser("list", help="list saved leads")
    ls.add_argument("--status", choices=scoring.STATUSES, help="filter by status")
    ls.add_argument("--min-score", type=int, default=0, help="minimum lead score")
    ls.add_argument("--category", help="filter by category text (substring)")
    ls.add_argument("--limit", type=int, help="max rows to show")
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="show full detail for one lead")
    sh.add_argument("id", type=int, help="lead id (from 'list')")
    sh.set_defaults(func=cmd_show)

    up = sub.add_parser("update", help="update a lead's status/notes")
    up.add_argument("id", type=int, help="lead id (from 'list')")
    up.add_argument("--status", help=f"new status ({', '.join(scoring.STATUSES)})")
    up.add_argument("--notes", help="freeform notes to attach")
    up.set_defaults(func=cmd_update)

    ex = sub.add_parser("export", help="export leads to CSV")
    ex.add_argument("-o", "--output", default="leads.csv", help="output CSV path")
    ex.add_argument("--status", choices=scoring.STATUSES, help="filter by status")
    ex.add_argument("--min-score", type=int, default=0, help="minimum lead score")
    ex.add_argument("--category", help="filter by category text (substring)")
    ex.set_defaults(func=cmd_export)

    st = sub.add_parser("stats", help="show pipeline summary")
    st.set_defaults(func=cmd_stats)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
