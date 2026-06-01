"""CSV export for leads."""

from __future__ import annotations

import csv
import sqlite3

COLUMNS = [
    "id", "name", "category", "score", "status", "phone", "website",
    "address", "city", "note", "notes", "lat", "lon", "osm_id",
    "source_area", "created_at", "updated_at",
]


def write_csv(rows: list[sqlite3.Row], path: str) -> int:
    """Write lead rows to ``path`` as CSV. Returns the number written."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row[col] for col in COLUMNS})
    return len(rows)
