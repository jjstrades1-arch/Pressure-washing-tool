"""Flask web app smoke tests (no live network).

Patches the scan finder so the admin 'scan' uses fake data instead of OSM,
and exercises the full subscriber journey end to end.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pwleads import scan, sources  # noqa: E402
from pwleads.web import create_app  # noqa: E402


def fake_find(place, radius_km, **kw):
    return [
        sources.Prospect("node/1", "QuickFuel", "Gas station", 95, "forecourt",
                         "1 Pump Rd", "Kent", "555-1000", "", place.lat,
                         place.lon, email="info@quickfuel.test"),
        sources.Prospect("node/2", "Joe Diner", "Restaurant", 88, "patio",
                         "12 Main", "Kent", "555-2000", "",
                         place.lat + 0.001, place.lon + 0.001, email=""),
    ]


class WebJourneyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = tmp.name
        app = create_app(self.path)
        # AUTOSCAN off so adding an area never hits the network in tests.
        app.config.update(TESTING=True, ADMIN_PASSWORD="admin", AUTOSCAN=False)
        self.client = app.test_client()

    def tearDown(self):
        os.unlink(self.path)

    def _signup(self):
        return self.client.post("/signup", data={
            "business_name": "Mike Wash", "email": "mike@example.com",
            "password": "secret123",
        }, follow_redirects=True)

    def test_landing_page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"pressure washing", r.data.lower())

    def test_full_journey(self):
        # Sign up -> lands on plans
        r = self._signup()
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Choose a plan", r.data)

        # Before subscribing, dashboard shows the locked state
        r = self.client.get("/dashboard")
        self.assertIn(b"not active", r.data)

        # Subscribe to Pro (id 2)
        r = self.client.post("/subscribe/2", follow_redirects=True)
        self.assertIn(b"Subscription active", r.data)

        # Add a service area (patch geocode so no network)
        with mock.patch(
            "pwleads.web.routes.sources.geocode",
            return_value=sources.Place("Kent, WA", 47.38, -122.23),
        ):
            r = self.client.post("/areas", data={"location": "Kent, WA"},
                                 follow_redirects=True)
        self.assertIn(b"Added service area", r.data)

        # No leads in inventory yet
        r = self.client.get("/dashboard")
        self.assertIn(b"No leads yet", r.data)

        # Admin logs in and scans (patched finder)
        self.client.post("/admin", data={"password": "admin"}, follow_redirects=True)
        with mock.patch.object(scan.sources, "find_prospects", fake_find):
            r = self.client.post("/admin/scan", follow_redirects=True)
        self.assertIn(b"prospects seen", r.data)

        # Dashboard now shows leads
        r = self.client.get("/dashboard")
        self.assertIn(b"QuickFuel", r.data)

        # Open a lead and move it through the pipeline
        lead_id = self._first_lead_id()
        r = self.client.get(f"/lead/{lead_id}")
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/lead/{lead_id}/update",
                             data={"status": "won", "notes": "Closed!"},
                             follow_redirects=True)
        self.assertIn(b"Lead updated", r.data)

        # Export CSV
        r = self.client.get("/export.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["Content-Type"])
        self.assertIn(b"QuickFuel", r.data)

    def _first_lead_id(self):
        from pwleads import db, entitlements
        with db.connect(self.path) as conn:
            cid = conn.execute("SELECT id FROM contractors LIMIT 1").fetchone()["id"]
            return entitlements.candidate_leads(conn, cid)[0]["id"]

    def test_login_required_redirects(self):
        r = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_wrong_login(self):
        self._signup()
        self.client.post("/logout")
        r = self.client.post("/login", data={
            "email": "mike@example.com", "password": "wrong"},
            follow_redirects=True)
        self.assertIn(b"Wrong email or password", r.data)


if __name__ == "__main__":
    unittest.main()
