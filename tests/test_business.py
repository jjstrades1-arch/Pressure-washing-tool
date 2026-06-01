"""Tests for the subscription business layer (no network)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pwleads import (  # noqa: E402
    auth, billing, db, entitlements, enrich, scan, sources,
)


def fake_finder(extra=None):
    """Return a finder that yields leads at/near the search center."""
    def finder(place, radius_km, **kw):
        leads = [
            sources.Prospect("node/1", "QuickFuel", "Gas station", 95, "forecourt",
                             "", "Kent", "", "", place.lat, place.lon, email=""),
            sources.Prospect("node/2", "Joe Diner", "Restaurant", 88, "patio",
                             "12 Main", "Kent", "555", "http://x",
                             place.lat + 0.001, place.lon + 0.001, email=""),
            sources.Prospect("node/3", "FarMart", "Supermarket", 88, "lot",
                             "", "Faraway", "", "", place.lat + 1.0,
                             place.lon + 1.0, email=""),
        ]
        return leads + (extra or [])
    return finder


class FixtureMixin(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name

    def tearDown(self):
        os.unlink(self.path)


class AuthTests(FixtureMixin):
    def test_register_and_authenticate(self):
        with db.connect(self.path) as c:
            cid = auth.register(c, "Mike Wash", "mike@example.com", "secret123")
            self.assertTrue(cid)
            self.assertIsNotNone(auth.authenticate(c, "mike@example.com", "secret123"))
            self.assertIsNone(auth.authenticate(c, "mike@example.com", "nope"))
            self.assertIsNone(auth.authenticate(c, "ghost@example.com", "secret123"))

    def test_duplicate_email_rejected(self):
        with db.connect(self.path) as c:
            auth.register(c, "A", "dup@example.com", "secret123")
            with self.assertRaises(auth.AuthError):
                auth.register(c, "B", "dup@example.com", "secret123")

    def test_validation(self):
        with db.connect(self.path) as c:
            with self.assertRaises(auth.AuthError):
                auth.register(c, "", "a@b.com", "secret123")
            with self.assertRaises(auth.AuthError):
                auth.register(c, "X", "bad-email", "secret123")
            with self.assertRaises(auth.AuthError):
                auth.register(c, "X", "a@b.com", "short")


class BillingTests(FixtureMixin):
    def test_subscribe_activates_and_replaces(self):
        with db.connect(self.path) as c:
            db.seed_plans(c)
            cid = auth.register(c, "Mike", "m@example.com", "secret123")
            b = billing.get_billing()
            b.subscribe(c, cid, 1)
            sub = entitlements.access(c, cid)
            self.assertEqual(sub["status"], "active")
            self.assertEqual(sub["plan_id"], 1)
            # Subscribing again switches plan and cancels the old one.
            b.subscribe(c, cid, 2)
            self.assertEqual(entitlements.access(c, cid)["plan_id"], 2)
            actives = c.execute(
                "SELECT COUNT(*) n FROM subscriptions WHERE contractor_id=? AND status='active'",
                (cid,),
            ).fetchone()["n"]
            self.assertEqual(actives, 1)

    def test_cancel_locks_access(self):
        with db.connect(self.path) as c:
            db.seed_plans(c)
            cid = auth.register(c, "Mike", "m@example.com", "secret123")
            sub_id = billing.get_billing().subscribe(c, cid, 1)
            billing.get_billing().cancel(c, sub_id)
            self.assertIsNone(entitlements.access(c, cid))


class EntitlementsTests(FixtureMixin):
    def _setup(self, c, plan_id=2):
        db.seed_plans(c)
        cid = auth.register(c, "Mike", "m@example.com", "secret123")
        db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
        billing.get_billing().subscribe(c, cid, plan_id)
        scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder())
        return cid

    def test_no_subscription_no_leads(self):
        with db.connect(self.path) as c:
            db.seed_plans(c)
            cid = auth.register(c, "Mike", "m@example.com", "secret123")
            db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
            self.assertEqual(entitlements.visible_leads(c, cid), [])

    def test_area_and_distance_filtering(self):
        with db.connect(self.path) as c:
            cid = self._setup(c)
            names = {l["name"] for l in entitlements.visible_leads(c, cid)}
            self.assertIn("QuickFuel", names)
            self.assertIn("Joe Diner", names)
            self.assertNotIn("FarMart", names)  # ~100km away, excluded

    def test_min_score_gate(self):
        with db.connect(self.path) as c:
            # Starter plan requires score >= 80; add a low-score lead.
            extra = [sources.Prospect("node/9", "Tiny Cafe", "Cafe", 50, "x",
                                      "", "Kent", "", "", 47.381, -122.231, email="")]
            db.seed_plans(c)
            cid = auth.register(c, "Mike", "m@example.com", "secret123")
            db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
            billing.get_billing().subscribe(c, cid, 1)  # Starter, min 80
            scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder(extra))
            scores = [l["score"] for l in entitlements.visible_leads(c, cid)]
            self.assertTrue(all(s >= 80 for s in scores))

    def test_cap_limits_results(self):
        with db.connect(self.path) as c:
            db.seed_plans(c)
            # Shrink Starter cap to 1 for the test.
            c.execute("UPDATE plans SET monthly_lead_cap = 1 WHERE id = 1")
            cid = auth.register(c, "Mike", "m@example.com", "secret123")
            db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
            billing.get_billing().subscribe(c, cid, 1)
            scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder())
            leads = entitlements.visible_leads(c, cid)
            self.assertEqual(len(leads), 1)
            self.assertEqual(leads[0]["name"], "QuickFuel")  # highest score

    def test_exclusivity_hides_from_others(self):
        with db.connect(self.path) as c:
            db.seed_plans(c)
            a = auth.register(c, "A", "a@example.com", "secret123")
            b = auth.register(c, "B", "b@example.com", "secret123")
            for cid in (a, b):
                db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
                billing.get_billing().subscribe(c, cid, 3)  # Metro = exclusive
            scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder())
            # Mark all leads exclusive, then A claims QuickFuel.
            c.execute("UPDATE leads SET exclusivity = 'exclusive'")
            qf = c.execute("SELECT id FROM leads WHERE name='QuickFuel'").fetchone()["id"]
            db.upsert_claim(c, qf, a, status="contacted")
            a_names = {l["name"] for l in entitlements.visible_leads(c, a)}
            b_names = {l["name"] for l in entitlements.visible_leads(c, b)}
            self.assertIn("QuickFuel", a_names)
            self.assertNotIn("QuickFuel", b_names)


class ScanFreshnessTests(FixtureMixin):
    def test_dedupe_and_deactivate(self):
        with db.connect(self.path) as c:
            r1 = scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder())
            self.assertEqual(r1["added"], 3)
            self.assertEqual(r1["refreshed"], 0)
            # Re-scan with QuickFuel gone -> it should be deactivated.
            def shrunk(place, radius_km, **kw):
                return [sources.Prospect("node/2", "Joe Diner", "Restaurant", 88,
                        "patio", "12 Main", "Kent", "555", "http://x",
                        place.lat + 0.001, place.lon + 0.001, email="")]
            r2 = scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=shrunk)
            self.assertEqual(r2["added"], 0)
            self.assertEqual(r2["refreshed"], 1)
            qf = c.execute("SELECT is_active FROM leads WHERE name='QuickFuel'").fetchone()
            self.assertEqual(qf["is_active"], 0)


class EnrichParsingTests(unittest.TestCase):
    def test_extract_email(self):
        self.assertEqual(
            enrich.extract_email("reach info@biz.com now"), "info@biz.com"
        )
        self.assertEqual(enrich.extract_email("logo@2x.png stuff"), "")
        self.assertEqual(enrich.extract_email("name@example.com"), "")

    def test_extract_phone(self):
        self.assertEqual(
            enrich.extract_phone("call (253) 852-1998 today"), "(253) 852-1998"
        )
        self.assertEqual(enrich.extract_phone("+1 253-852-1998"), "+1 253-852-1998")
        self.assertEqual(enrich.extract_phone("no number here 12"), "")


class EnrichPipelineTests(FixtureMixin):
    def test_enrich_fills_missing_fields(self):
        with db.connect(self.path) as c:
            # A lead with website but no phone/email/address.
            p = sources.Prospect("node/1", "QuickFuel", "Gas station", 95, "x",
                                  "", "", "", "http://quickfuel.test", 47.38,
                                  -122.23, email="")
            db.upsert_prospects(c, [p], "Kent")
            lead = db.query_leads(c)[0]
            html = "Call (253) 852-1998 or email sales@quickfuel.test"
            changes = enrich.enrich_lead(
                c, lead, fetch=lambda u: html,
                reverse=lambda a, b: ("99 Pump Rd", "Kent"),
            )
            self.assertEqual(changes.get("phone"), "(253) 852-1998")
            self.assertEqual(changes.get("email"), "sales@quickfuel.test")
            self.assertEqual(changes.get("address"), "99 Pump Rd")
            refreshed = db.query_leads(c)[0]
            self.assertEqual(refreshed["phone"], "(253) 852-1998")
            self.assertIsNotNone(refreshed["enriched_at"])

    def test_pending_excludes_complete_and_enriched(self):
        with db.connect(self.path) as c:
            p = sources.Prospect("node/1", "Done", "Gas station", 95, "x",
                                 "1 St", "Kent", "555", "http://x", 47.38,
                                 -122.23, email="a@b.com")
            db.upsert_prospects(c, [p], "Kent")
            # Has phone, email, address -> nothing to enrich.
            self.assertEqual(enrich.pending_leads(c, 10), [])


if __name__ == "__main__":
    unittest.main()
