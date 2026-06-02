"""Tests for the subscription business layer (no network)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone  # noqa: E402
from unittest import mock  # noqa: E402

from pwleads import (  # noqa: E402
    auth, billing, db, entitlements, enrich, guarantee, quality, scan, sources,
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
            self.assertEqual(entitlements.candidate_leads(c, cid), [])

    def test_area_and_distance_filtering(self):
        with db.connect(self.path) as c:
            cid = self._setup(c)
            names = {l["name"] for l in entitlements.candidate_leads(c, cid)}
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
            scores = [l["score"] for l in entitlements.candidate_leads(c, cid)]
            self.assertTrue(all(s >= 80 for s in scores))

    def test_reveal_cap_limits_unlocks(self):
        with db.connect(self.path) as c:
            db.seed_plans(c)
            c.execute("UPDATE plans SET monthly_lead_cap = 1 WHERE id = 1")
            cid = auth.register(c, "Mike", "m@example.com", "secret123")
            db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
            billing.get_billing().subscribe(c, cid, 1)  # Starter, cap now 1
            scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder())
            leads = entitlements.candidate_leads(c, cid)
            self.assertEqual(len(leads), 2)  # both browsable
            ids = [l["id"] for l in leads]
            self.assertEqual(entitlements.reveal_lead(c, cid, ids[0]), "ok")
            self.assertEqual(entitlements.reveal_lead(c, cid, ids[0]), "already")
            self.assertEqual(entitlements.reveal_lead(c, cid, ids[1]), "limit")
            self.assertEqual(entitlements.reveal_usage(c, cid), (1, 1))
            self.assertEqual(len(entitlements.revealed_leads(c, cid)), 1)

    def test_exclusivity_hides_from_others(self):
        with db.connect(self.path) as c:
            db.seed_plans(c)
            a = auth.register(c, "A", "a@example.com", "secret123")
            b = auth.register(c, "B", "b@example.com", "secret123")
            for cid in (a, b):
                db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
                billing.get_billing().subscribe(c, cid, 3)  # Metro = exclusive
            scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder())
            c.execute("UPDATE leads SET exclusivity = 'exclusive'")
            qf = c.execute("SELECT id FROM leads WHERE name='QuickFuel'").fetchone()["id"]
            db.upsert_claim(c, qf, a, status="contacted")
            a_names = {l["name"] for l in entitlements.candidate_leads(c, a)}
            b_names = {l["name"] for l in entitlements.candidate_leads(c, b)}
            self.assertIn("QuickFuel", a_names)
            self.assertNotIn("QuickFuel", b_names)

    def test_working_a_shared_lead_locks_it(self):
        with db.connect(self.path) as c:
            db.seed_plans(c)
            a = auth.register(c, "A", "a@example.com", "secret123")
            b = auth.register(c, "B", "b@example.com", "secret123")
            for cid in (a, b):
                db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
                billing.get_billing().subscribe(c, cid, 2)  # Pro = shared
            scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder())
            qf = c.execute("SELECT id FROM leads WHERE name='QuickFuel'").fetchone()["id"]
            db.upsert_claim(c, qf, a, status="contacted")  # A is working it
            self.assertIn("QuickFuel", {l["name"] for l in entitlements.candidate_leads(c, a)})
            self.assertNotIn("QuickFuel", {l["name"] for l in entitlements.candidate_leads(c, b)})

    def test_new_since_login_flag(self):
        with db.connect(self.path) as c:
            cid = self._setup(c)
            c.execute("UPDATE contractors SET prev_login='2000-01-01T00:00:00+00:00' WHERE id=?", (cid,))
            self.assertTrue(all(l["is_new"] for l in entitlements.candidate_leads(c, cid)))
            c.execute("UPDATE contractors SET prev_login='2099-01-01T00:00:00+00:00' WHERE id=?", (cid,))
            self.assertFalse(any(l["is_new"] for l in entitlements.candidate_leads(c, cid)))

    def test_roi_reports_won_value(self):
        with db.connect(self.path) as c:
            cid = self._setup(c)  # Pro plan = $99/mo
            lead_id = entitlements.candidate_leads(c, cid)[0]["id"]
            db.upsert_claim(c, lead_id, cid, status="won", job_value_cents=200000)
            r = entitlements.roi(c, cid)
            self.assertEqual(r["won_cents"], 200000)
            self.assertEqual(r["won_count"], 1)
            self.assertEqual(r["price_cents"], 9900)


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


class QualityTests(unittest.TestCase):
    def test_classify_phone(self):
        self.assertEqual(quality.classify_phone("(253) 852-1998"), "direct")
        self.assertEqual(quality.classify_phone("+1 253-852-1998"), "direct")
        self.assertEqual(quality.classify_phone("1-800-555-1212"), "tollfree")
        self.assertEqual(quality.classify_phone("888.555.0000"), "tollfree")
        self.assertEqual(quality.classify_phone("12"), "unknown")
        self.assertEqual(quality.classify_phone(""), "")

    def test_chain_from_tags(self):
        brand, is_chain = quality.chain_from_tags({"brand": "Chevron", "name": "Chevron"})
        self.assertEqual(brand, "Chevron")
        self.assertEqual(is_chain, 1)
        brand, is_chain = quality.chain_from_tags({"name": "Joe's Diner"})
        self.assertEqual(is_chain, 0)

    def test_outreach_hint(self):
        self.assertIn("visit", quality.outreach_hint(0, "tollfree").lower())
        self.assertIn("manager", quality.outreach_hint(1, "direct").lower())
        self.assertIn("call", quality.outreach_hint(0, "direct").lower())

    def test_scan_persists_quality_signals(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            def finder(place, radius_km, **kw):
                return [
                    sources.Prospect("node/1", "Chevron", "Gas station", 95, "x",
                        "", "Kent", "1-800-555-1212", "", place.lat, place.lon,
                        email="", brand="Chevron", is_chain=1),
                    sources.Prospect("node/2", "Joe Diner", "Restaurant", 88, "x",
                        "", "Kent", "(253) 852-1998", "", place.lat, place.lon,
                        email="", brand="", is_chain=0),
                ]
            with db.connect(tmp.name) as c:
                scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=finder)
                chev = c.execute("SELECT * FROM leads WHERE name='Chevron'").fetchone()
                joe = c.execute("SELECT * FROM leads WHERE name='Joe Diner'").fetchone()
                self.assertEqual(chev["is_chain"], 1)
                self.assertEqual(chev["phone_type"], "tollfree")
                self.assertEqual(joe["is_chain"], 0)
                self.assertEqual(joe["phone_type"], "direct")
        finally:
            os.unlink(tmp.name)


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


class ServiceAreaTests(FixtureMixin):
    def test_add_then_delete(self):
        with db.connect(self.path) as c:
            cid = auth.register(c, "Mike", "m@example.com", "secret123")
            aid = db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
            self.assertEqual(len(db.list_service_areas(c, cid)), 1)
            self.assertTrue(db.delete_service_area(c, aid, cid))
            self.assertEqual(db.list_service_areas(c, cid), [])

    def test_cannot_delete_another_contractors_area(self):
        with db.connect(self.path) as c:
            a = auth.register(c, "A", "a@example.com", "secret123")
            b = auth.register(c, "B", "b@example.com", "secret123")
            aid = db.add_service_area(c, a, "Kent", 47.38, -122.23, 8.0)
            # B tries to delete A's area -> refused, area still there.
            self.assertFalse(db.delete_service_area(c, aid, b))
            self.assertEqual(len(db.list_service_areas(c, a)), 1)


class ConfidenceTests(unittest.TestCase):
    def test_complete_independent_is_high(self):
        now = datetime.now(timezone.utc)
        lead = {
            "phone_type": "direct", "phone": "(253) 555-1000",
            "email": "a@b.com", "address": "1 Main St", "website": "http://x",
            "is_chain": 0, "last_seen": now.isoformat(timespec="seconds"),
            "bad_reports": 0,
        }
        c = quality.confidence(lead, now=now)
        self.assertEqual(c["label"], "High")
        self.assertGreaterEqual(c["score"], 70)

    def test_sparse_chain_reported_is_low(self):
        now = datetime.now(timezone.utc)
        lead = {
            "phone_type": "", "phone": "", "email": "", "address": "",
            "website": "", "is_chain": 1, "last_seen": "2000-01-01T00:00:00+00:00",
            "bad_reports": 2,
        }
        c = quality.confidence(lead, now=now)
        self.assertEqual(c["label"], "Low")
        self.assertLess(c["score"], 45)


class GuaranteeTests(FixtureMixin):
    def _setup(self, plan_id=2):
        """Pro contractor in Kent with two scanned leads. Returns (cid, [ids])."""
        with db.connect(self.path) as c:
            db.seed_plans(c)
            cid = auth.register(c, "Mike", "m@example.com", "secret123")
            db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
            billing.get_billing().subscribe(c, cid, plan_id)
            scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder())
            ids = [l["id"] for l in entitlements.candidate_leads(c, cid)]
        return cid, ids

    def test_refund_returns_the_credit(self):
        cid, ids = self._setup()
        with db.connect(self.path) as c:
            entitlements.reveal_lead(c, cid, ids[0])
            self.assertEqual(entitlements.reveal_usage(c, cid)[0], 1)
            status = guarantee.report_bad_lead(c, cid, ids[0], "dead_phone")
            self.assertEqual(status, "refunded")
            # Slot returned: usage back to 0.
            self.assertEqual(entitlements.reveal_usage(c, cid)[0], 0)
            # Reporting again is rejected.
            self.assertEqual(
                guarantee.report_bad_lead(c, cid, ids[0], "dead_phone"), "already"
            )

    def test_cannot_refund_a_lead_you_worked(self):
        cid, ids = self._setup()
        with db.connect(self.path) as c:
            entitlements.reveal_lead(c, cid, ids[0])
            db.upsert_claim(c, ids[0], cid, status="won", job_value_cents=200000)
            self.assertEqual(
                guarantee.report_bad_lead(c, cid, ids[0], "closed"), "blocked_worked"
            )

    def test_cannot_report_unrevealed_lead(self):
        cid, ids = self._setup()
        with db.connect(self.path) as c:
            self.assertEqual(
                guarantee.report_bad_lead(c, cid, ids[0], "dead_phone"), "invalid"
            )

    def test_invalid_reason_rejected(self):
        cid, ids = self._setup()
        with db.connect(self.path) as c:
            entitlements.reveal_lead(c, cid, ids[0])
            self.assertEqual(
                guarantee.report_bad_lead(c, cid, ids[0], "lost_the_bid"),
                "invalid_reason",
            )

    def test_report_window_enforced(self):
        cid, ids = self._setup()
        with db.connect(self.path) as c:
            entitlements.reveal_lead(c, cid, ids[0])
            # Backdate the unlock well past the window.
            c.execute(
                "UPDATE reveals SET created_at = '2000-01-01T00:00:00+00:00' "
                "WHERE contractor_id = ? AND lead_id = ?", (cid, ids[0]),
            )
            self.assertEqual(
                guarantee.report_bad_lead(c, cid, ids[0], "dead_phone"),
                "blocked_window",
            )

    def test_heavy_reporter_goes_to_review(self):
        cid, ids = self._setup()
        with db.connect(self.path) as c:
            entitlements.reveal_lead(c, cid, ids[0])
            entitlements.reveal_lead(c, cid, ids[1])
            with mock.patch.object(guarantee, "AUTO_REFUND_CAP", 1):
                self.assertEqual(
                    guarantee.report_bad_lead(c, cid, ids[0], "dead_phone"), "refunded"
                )
                # Second one is over the cap -> manual review, no auto-refund.
                self.assertEqual(
                    guarantee.report_bad_lead(c, cid, ids[1], "dead_phone"), "review"
                )
            self.assertEqual(len(db.pending_reports(c)), 1)

    def test_multiple_reporters_deactivate_lead(self):
        with db.connect(self.path) as c:
            db.seed_plans(c)
            a = auth.register(c, "A", "a@example.com", "secret123")
            b = auth.register(c, "B", "b@example.com", "secret123")
            for cid in (a, b):
                db.add_service_area(c, cid, "Kent", 47.38, -122.23, 8.0)
                billing.get_billing().subscribe(c, cid, 2)
            scan.scan_area(c, "Kent", 47.38, -122.23, 8.0, finder=fake_finder())
            lead_id = entitlements.candidate_leads(c, a)[0]["id"]
            entitlements.reveal_lead(c, a, lead_id)
            entitlements.reveal_lead(c, b, lead_id)
            guarantee.report_bad_lead(c, a, lead_id, "closed")
            active = c.execute("SELECT is_active FROM leads WHERE id=?", (lead_id,)).fetchone()
            self.assertEqual(active["is_active"], 1)  # one report: still live
            guarantee.report_bad_lead(c, b, lead_id, "closed")
            active = c.execute("SELECT is_active FROM leads WHERE id=?", (lead_id,)).fetchone()
            self.assertEqual(active["is_active"], 0)  # two distinct reports: pulled


if __name__ == "__main__":
    unittest.main()
