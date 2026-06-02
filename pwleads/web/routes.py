"""Routes for the pwleads web app."""

from __future__ import annotations

import functools
import io
import threading

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect,
    render_template, request, session, url_for,
)

from .. import (
    auth, billing, db, entitlements, enrich, export, guarantee, outreach,
    quality, scan, scoring, sources,
)

bp = Blueprint("main", __name__)


def _db():
    return db.connect(current_app.config["DB_PATH"])


def _autoscan(db_path: str, label: str, lat: float, lon: float, radius_km: float):
    """Background scan so a contractor's dashboard fills right after they add
    an area. Uses only free OpenStreetMap data (no API keys, no cost).
    Failures are swallowed -- the owner can always re-scan from the admin panel.
    """
    try:
        with db.connect(db_path) as conn:
            scan.scan_area(conn, label, lat, lon, radius_km)
    except Exception:  # noqa: BLE001 - best-effort background work
        pass


def current_contractor_id():
    return session.get("contractor_id")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_contractor_id() is None:
            flash("Please log in first.", "error")
            return redirect(url_for("main.login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("main.admin"))
        return view(*args, **kwargs)

    return wrapped


@bp.app_template_filter("money")
def money(cents):
    return f"${cents / 100:,.0f}"


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
@bp.route("/")
def index():
    with _db() as conn:
        plans = db.list_plans(conn)
    return render_template("index.html", plans=plans)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        with _db() as conn:
            try:
                cid = auth.register(
                    conn,
                    business_name=request.form.get("business_name", ""),
                    email=request.form.get("email", ""),
                    password=request.form.get("password", ""),
                )
            except auth.AuthError as exc:
                flash(str(exc), "error")
                return render_template("signup.html", form=request.form)
            db.touch_login(conn, cid)
        session.clear()
        session["contractor_id"] = cid
        flash("Account created. Pick a plan to start getting leads.", "ok")
        return redirect(url_for("main.plans"))
    return render_template("signup.html", form={})


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        with _db() as conn:
            contractor = auth.authenticate(
                conn,
                request.form.get("email", ""),
                request.form.get("password", ""),
            )
        if contractor is None:
            flash("Wrong email or password.", "error")
            return render_template("login.html")
        with _db() as conn:
            db.touch_login(conn, contractor["id"])
        session.clear()
        session["contractor_id"] = contractor["id"]
        return redirect(url_for("main.dashboard"))
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.index"))


# --------------------------------------------------------------------------- #
# Subscription
# --------------------------------------------------------------------------- #
@bp.route("/plans")
def plans():
    with _db() as conn:
        plan_rows = db.list_plans(conn)
        current = None
        cid = current_contractor_id()
        if cid:
            current = entitlements.access(conn, cid)
    return render_template("plans.html", plans=plan_rows, current=current)


@bp.route("/subscribe/<int:plan_id>", methods=["POST"])
@login_required
def subscribe(plan_id):
    with _db() as conn:
        try:
            billing.get_billing().subscribe(conn, current_contractor_id(), plan_id)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("main.plans"))
    flash("Subscription active! (simulated payment)", "ok")
    return redirect(url_for("main.dashboard"))


# --------------------------------------------------------------------------- #
# Service areas
# --------------------------------------------------------------------------- #
@bp.route("/areas", methods=["POST"])
@login_required
def add_area():
    cid = current_contractor_id()
    location = request.form.get("location", "").strip()
    with _db() as conn:
        sub = entitlements.access(conn, cid)
        if sub is None:
            flash("Subscribe to a plan before adding a service area.", "error")
            return redirect(url_for("main.plans"))
        existing = db.list_service_areas(conn, cid)
        if len(existing) >= sub["max_areas"]:
            flash(
                f"Your {sub['plan_name']} plan allows {sub['max_areas']} "
                "service area(s). Upgrade for more.",
                "error",
            )
            return redirect(url_for("main.dashboard"))
        try:
            place = sources.geocode(location)
        except sources.SourceError as exc:
            flash(f"Could not find that location: {exc}", "error")
            return redirect(url_for("main.dashboard"))
        radius = sub["plan_radius_km"]
        db.add_service_area(
            conn, cid, place.display_name, place.lat, place.lon, radius
        )
    # Auto-fill the dashboard by scanning the new area in the background (free).
    if current_app.config.get("AUTOSCAN", True):
        threading.Thread(
            target=_autoscan,
            args=(current_app.config["DB_PATH"], place.display_name,
                  place.lat, place.lon, radius),
            daemon=True,
        ).start()
        flash(
            f"Added {place.display_name}. Finding leads now — "
            "refresh in a minute.", "ok",
        )
    else:
        flash(f"Added service area: {place.display_name}", "ok")
    return redirect(url_for("main.dashboard"))


@bp.route("/areas/<int:area_id>/delete", methods=["POST"])
@login_required
def delete_area(area_id):
    with _db() as conn:
        ok = db.delete_service_area(conn, area_id, current_contractor_id())
    if ok:
        flash("Service area removed.", "ok")
    else:
        flash("Couldn't remove that area.", "error")
    return redirect(url_for("main.dashboard"))


# --------------------------------------------------------------------------- #
# Dashboard & leads
# --------------------------------------------------------------------------- #
@bp.route("/dashboard")
@login_required
def dashboard():
    cid = current_contractor_id()
    with _db() as conn:
        contractor = db.get_contractor(conn, cid)
        sub = entitlements.access(conn, cid)
        areas = db.list_service_areas(conn, cid)
        leads = entitlements.candidate_leads(conn, cid) if sub else []
        roi = entitlements.roi(conn, cid) if sub else None
        used, cap = entitlements.reveal_usage(conn, cid) if sub else (0, 0)
    new_count = sum(1 for l in leads if l["is_new"])
    return render_template(
        "dashboard.html",
        contractor=contractor,
        sub=sub,
        areas=areas,
        leads=leads,
        roi=roi,
        used=used,
        cap=cap,
        new_count=new_count,
        statuses=scoring.STATUSES,
    )


@bp.route("/lead/<int:lead_id>")
@login_required
def lead_detail(lead_id):
    cid = current_contractor_id()
    with _db() as conn:
        lead = entitlements.get_candidate(conn, cid, lead_id)
        if lead is None:
            abort(404)
        # Viewing the detail "reveals" the lead (counts against the monthly cap).
        reveal_status = entitlements.reveal_lead(conn, cid, lead_id)
        claim = db.get_claim(conn, lead_id, cid)
        contractor = db.get_contractor(conn, cid)
        used, cap = entitlements.reveal_usage(conn, cid)
        report = db.get_report(conn, cid, lead_id)
    revealed = reveal_status in ("ok", "already")
    hint = quality.outreach_hint(lead["is_chain"], lead["phone_type"])
    company = contractor["business_name"]
    script = outreach.call_script(lead["name"], lead["category"], company)
    email = outreach.email_template(lead["name"], lead["category"], company)
    return render_template(
        "lead.html", lead=lead, claim=claim, hint=hint, revealed=revealed,
        used=used, cap=cap, script=script, email=email,
        confidence=quality.confidence(lead), report=report,
        report_reasons=guarantee.REASONS, statuses=scoring.STATUSES,
    )


def _dollars_to_cents(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(round(float(value.replace("$", "").replace(",", "")) * 100))
    except ValueError:
        return None


@bp.route("/lead/<int:lead_id>/update", methods=["POST"])
@login_required
def update_claim(lead_id):
    cid = current_contractor_id()
    status = request.form.get("status") or None
    notes = request.form.get("notes")
    job_value_cents = _dollars_to_cents(request.form.get("job_value"))
    if status and status not in scoring.STATUSES:
        abort(400)
    with _db() as conn:
        if entitlements.get_candidate(conn, cid, lead_id) is None:
            abort(404)
        db.upsert_claim(
            conn, lead_id, cid, status=status, notes=notes,
            job_value_cents=job_value_cents,
        )
    flash("Lead updated.", "ok")
    return redirect(request.referrer or url_for("main.dashboard"))


_REPORT_MESSAGES = {
    "refunded": ("Credited back — your unlock has been returned. Thanks for "
                 "flagging it; we'll improve the data.", "ok"),
    "review": ("Report received. You've reached this month's instant-refund "
               "limit, so our team will review this one.", "ok"),
    "blocked_worked": ("This lead is marked contacted/quoted/won in your "
                       "pipeline, so it isn't eligible for a data refund.", "error"),
    "blocked_window": (f"Bad-lead reports must be made within "
                       f"{guarantee.REPORT_WINDOW_DAYS} days of unlocking.", "error"),
    "already": ("You've already reported this lead.", "error"),
    "invalid_reason": ("Please choose a valid reason.", "error"),
    "invalid": ("You can only report a lead you've unlocked this period.", "error"),
}


@bp.route("/lead/<int:lead_id>/report", methods=["POST"])
@login_required
def report_lead(lead_id):
    reason = request.form.get("reason", "")
    with _db() as conn:
        status = guarantee.report_bad_lead(conn, current_contractor_id(), lead_id, reason)
    msg, category = _REPORT_MESSAGES.get(status, ("Something went wrong.", "error"))
    flash(msg, category)
    return redirect(url_for("main.dashboard"))


@bp.route("/export.csv")
@login_required
def export_csv():
    cid = current_contractor_id()
    with _db() as conn:
        leads = entitlements.revealed_leads(conn, cid)
    buf = io.StringIO()
    import csv

    cols = ["id", "name", "category", "score", "claim_status", "phone",
            "phone_type", "email", "website", "address", "city", "note"]
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        writer.writerow({c: lead.get(c, "") for c in cols})
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=my_leads.csv"},
    )


# --------------------------------------------------------------------------- #
# Admin (owner)
# --------------------------------------------------------------------------- #
@bp.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == current_app.config["ADMIN_PASSWORD"]:
            session["is_admin"] = True
            return redirect(url_for("main.admin_panel"))
        flash("Wrong admin password.", "error")
    if session.get("is_admin"):
        return redirect(url_for("main.admin_panel"))
    return render_template("admin_login.html")


@bp.route("/admin/panel")
@admin_required
def admin_panel():
    with _db() as conn:
        scans = db.recent_scans(conn)
        areas = db.all_service_areas(conn)
        reviews = db.pending_reports(conn)
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE is_active = 1"
        ).fetchone()["n"]
        contractors = conn.execute(
            "SELECT COUNT(*) AS n FROM contractors"
        ).fetchone()["n"]
    return render_template(
        "admin.html", scans=scans, areas=areas, reviews=reviews,
        total=total, contractors=contractors,
    )


@bp.route("/admin/scan", methods=["POST"])
@admin_required
def admin_scan():
    with _db() as conn:
        results = scan.scan_all_areas(conn)
    found = sum(r["found"] for r in results)
    flash(f"Scanned {len(results)} area(s); {found} prospects seen.", "ok")
    return redirect(url_for("main.admin_panel"))


@bp.route("/admin/enrich", methods=["POST"])
@admin_required
def admin_enrich():
    with _db() as conn:
        res = enrich.enrich_pending(conn, limit=40, delay=0.5)
    flash(
        f"Enriched {res['processed']} lead(s); {res['updated']} got new contact info.",
        "ok",
    )
    return redirect(url_for("main.admin_panel"))
