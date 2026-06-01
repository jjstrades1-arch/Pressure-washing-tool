"""Credit-back guarantee: refund a lead *defect*, never a lost job.

The guarantee covers bad DATA (dead number, closed business, duplicate, wrong
info), not bad SELLING. The rules here are what keep it from becoming "free
jobs":

* Only objective defect reasons are accepted.
* A lead the contractor's own pipeline shows they reached (contacted/quoted/
  won) is BLOCKED -- they can't claim "unreachable" about a lead they worked.
* Reports must come within a short window of unlocking.
* Refunds return an unlock *credit*, not cash.
* Heavy reporters trip a per-period cap and go to manual review.
* Repeatedly-reported leads are auto-pulled from circulation.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import db, entitlements

# Objective defect reasons (label shown to the contractor).
REASONS = {
    "dead_phone": "Phone disconnected or wrong number",
    "closed": "Business permanently closed / doesn't exist",
    "duplicate": "Duplicate of a lead I already unlocked",
    "wrong_info": "Wrong category, location, or other data error",
}

# A report this long after unlocking is too late (a job could have happened).
REPORT_WINDOW_DAYS = 7
# Approved refunds per billing period before further reports go to review.
AUTO_REFUND_CAP = 5
# Distinct contractors reporting one lead before it's pulled from circulation.
DEACTIVATE_AT = 2


def report_bad_lead(
    conn: sqlite3.Connection, contractor_id: int, lead_id: int, reason: str
) -> str:
    """Process a bad-lead report. Returns a status string:

    'refunded'       -> rules pass; unlock credit returned
    'review'         -> accepted but over the auto cap; owner will review
    'blocked_worked' -> pipeline shows they reached this lead; not refundable
    'blocked_window' -> reported too long after unlocking
    'already'        -> already reported
    'invalid_reason' -> not an accepted defect reason
    'invalid'        -> not unlocked this period / no subscription
    """
    sub = entitlements.access(conn, contractor_id)
    if sub is None:
        return "invalid"
    period = sub["current_period_start"]

    if not db.has_reveal(conn, contractor_id, lead_id, period):
        return "invalid"  # can only report a lead you actually unlocked
    if db.get_report(conn, contractor_id, lead_id) is not None:
        return "already"
    if reason not in REASONS:
        return "invalid_reason"

    # Self-contradiction guard: you can't call a lead you worked "unreachable".
    claim = db.get_claim(conn, lead_id, contractor_id)
    if claim is not None and claim["status"] in db.WORKING_STATUSES:
        return "blocked_worked"

    # Time window.
    revealed_at = db.reveal_time(conn, contractor_id, lead_id, period)
    if revealed_at:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=REPORT_WINDOW_DAYS)
        ).isoformat(timespec="seconds")
        if revealed_at < cutoff:
            return "blocked_window"

    # Rate limit: frequent reporters go to manual review instead of auto-refund.
    if db.approved_report_count(conn, contractor_id, period) >= AUTO_REFUND_CAP:
        db.add_report(conn, contractor_id, lead_id, reason, "review", period)
        return "review"

    # Approve: return the credit, flag the lead, pull it if others agree.
    db.add_report(conn, contractor_id, lead_id, reason, "approved", period)
    db.refund_reveal(conn, contractor_id, lead_id, period)
    distinct = db.flag_bad_report(conn, lead_id)
    if distinct >= DEACTIVATE_AT:
        db.deactivate_lead(conn, lead_id)
    return "refunded"
