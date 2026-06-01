"""Subscription billing.

v1 ships a ``SimulatedBilling`` provider: subscribing activates the plan
immediately with no real charge, so the whole signup -> subscribe -> access
flow can be built and demoed today. The ``Billing`` interface is the seam
where a real ``StripeBilling`` implementation slots in later without touching
the rest of the app -- callers only ever depend on ``subscribe``/``cancel``.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from . import db

BILLING_PERIOD_DAYS = 30


class Billing:
    """Interface every billing provider implements."""

    name = "base"

    def subscribe(
        self, conn: sqlite3.Connection, contractor_id: int, plan_id: int
    ) -> int:
        raise NotImplementedError

    def cancel(self, conn: sqlite3.Connection, subscription_id: int) -> bool:
        raise NotImplementedError


class SimulatedBilling(Billing):
    """Pretend payment provider. Activates subscriptions instantly, no charge."""

    name = "simulated"

    def subscribe(
        self, conn: sqlite3.Connection, contractor_id: int, plan_id: int
    ) -> int:
        if db.get_plan(conn, plan_id) is None:
            raise ValueError(f"no such plan: {plan_id}")
        # Cancel any existing active subscription so a contractor has one plan.
        current = db.get_active_subscription(conn, contractor_id)
        if current is not None:
            db.set_subscription_status(conn, current["id"], "cancelled")
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=BILLING_PERIOD_DAYS)
        return db.create_subscription(
            conn,
            contractor_id=contractor_id,
            plan_id=plan_id,
            provider=self.name,
            provider_ref=f"sim_{uuid.uuid4().hex[:16]}",
            period_start=now.isoformat(timespec="seconds"),
            period_end=end.isoformat(timespec="seconds"),
            status="active",
        )

    def cancel(self, conn: sqlite3.Connection, subscription_id: int) -> bool:
        return db.set_subscription_status(conn, subscription_id, "cancelled")


# Default provider for v1. Swap here (or via config) when Stripe is ready.
def get_billing() -> Billing:
    return SimulatedBilling()
