"""Tests for pwleads. No network access required.

Run with:  python -m pytest   (or)   python -m unittest
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pwleads import db, export, scoring, sources  # noqa: E402


class ScoringTests(unittest.TestCase):
    def test_classify_known_tag(self):
        cat, score, note = scoring.classify({"amenity": "fuel"})
        self.assertEqual(cat, "Gas station")
        self.assertGreater(score, 0)
        self.assertTrue(note)

    def test_classify_unknown_tag_returns_none(self):
        self.assertIsNone(scoring.classify({"amenity": "bench"}))

    def test_first_rule_wins_for_multi_tagged(self):
        # fast_food (92) should beat a generic commercial building (72)
        cat, score, _ = scoring.classify(
            {"amenity": "fast_food", "building": "commercial"}
        )
        self.assertEqual(cat, "Fast food")
        self.assertEqual(score, 92)

    def test_tag_keys_unique_and_nonempty(self):
        keys = scoring.tag_keys()
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("amenity", keys)


class OverpassParsingTests(unittest.TestCase):
    def test_compose_address(self):
        addr, city = sources._compose_address(
            {"addr:housenumber": "100", "addr:street": "Main St", "addr:city": "Austin"}
        )
        self.assertEqual(addr, "100 Main St")
        self.assertEqual(city, "Austin")

    def test_query_includes_all_tags(self):
        q = sources._build_overpass_query(30.0, -97.0, 5000, 60)
        self.assertIn('["amenity"="fuel"]', q)
        self.assertIn("around:5000,30.0,-97.0", q)
        self.assertIn("out center tags;", q)


def _sample_prospects():
    return [
        sources.Prospect(
            osm_id="node/1", name="Joe's Diner", category="Restaurant",
            score=88, note="patios", address="1 Main St", city="Austin",
            phone="555-1212", website="", lat=30.1, lon=-97.1,
        ),
        sources.Prospect(
            osm_id="node/2", name="QuickFuel", category="Gas station",
            score=95, note="forecourt", address="", city="Austin",
            phone="", website="https://x.example", lat=30.2, lon=-97.2,
        ),
    ]


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        os.unlink(self.path)

    def test_upsert_is_idempotent(self):
        with db.connect(self.path) as conn:
            added, skipped = db.upsert_prospects(conn, _sample_prospects(), "Austin")
            self.assertEqual((added, skipped), (2, 0))
        with db.connect(self.path) as conn:
            added, skipped = db.upsert_prospects(conn, _sample_prospects(), "Austin")
            self.assertEqual((added, skipped), (0, 2))

    def test_query_orders_by_score_desc(self):
        with db.connect(self.path) as conn:
            db.upsert_prospects(conn, _sample_prospects(), "Austin")
            rows = db.query_leads(conn)
            self.assertEqual(rows[0]["name"], "QuickFuel")  # score 95 first
            self.assertEqual(len(rows), 2)

    def test_min_score_filter(self):
        with db.connect(self.path) as conn:
            db.upsert_prospects(conn, _sample_prospects(), "Austin")
            rows = db.query_leads(conn, min_score=90)
            self.assertEqual([r["name"] for r in rows], ["QuickFuel"])

    def test_update_status_and_notes(self):
        with db.connect(self.path) as conn:
            db.upsert_prospects(conn, _sample_prospects(), "Austin")
            lead = db.query_leads(conn)[0]
            self.assertTrue(
                db.update_lead(conn, lead["id"], status="contacted", notes="called")
            )
            refreshed = db.get_lead(conn, lead["id"])
            self.assertEqual(refreshed["status"], "contacted")
            self.assertEqual(refreshed["notes"], "called")

    def test_update_missing_lead_returns_false(self):
        with db.connect(self.path) as conn:
            self.assertFalse(db.update_lead(conn, 999, status="won"))

    def test_status_counts(self):
        with db.connect(self.path) as conn:
            db.upsert_prospects(conn, _sample_prospects(), "Austin")
            counts = db.status_counts(conn)
            self.assertEqual(counts.get("new"), 2)

    def test_export_csv(self):
        out = self.path + ".csv"
        with db.connect(self.path) as conn:
            db.upsert_prospects(conn, _sample_prospects(), "Austin")
            rows = db.query_leads(conn)
            n = export.write_csv(rows, out)
        self.assertEqual(n, 2)
        with open(out, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("QuickFuel", content)
        self.assertIn("name", content.splitlines()[0])
        os.unlink(out)


if __name__ == "__main__":
    unittest.main()
