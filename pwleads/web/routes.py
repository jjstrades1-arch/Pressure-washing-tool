"""Routes for the pwleads web app."""

from __future__ import annotations

import functools
import io

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect,
    render_template, request, session, url_for,
)

from .. import auth, billing, db, entitlements, enrich, export, scan, scoring, sources

bp = Blueprint("main", __name__)


def _db():
    return db.connect(current_app.config["DB_PATH"])


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
        db.add_service_area(
            conn, cid, place.display_name, place.lat, place.lon, sub["plan_radius_km"]
        )
    flash(f"Added service area: {place.display_name}", "ok")
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
        leads = entitlements.visible_leads(conn, cid) if sub else []
    return render_template(
        "dashboard.html",
        contractor=contractor,
        sub=sub,
        areas=areas,
        leads=leads,
        statuses=scoring.STATUSES,
    )


@bp.route("/lead/<int:lead_id>")
@login_required
def lead_detail(lead_id):
    cid = current_contractor_id()
    with _db() as conn:
        lead = entitlements.lead_visible_to(conn, cid, lead_id)
        if lead is None:
            abort(404)
        claim = db.get_claim(conn, lead_id, cid)
    return render_template(
        "lead.html", lead=lead, claim=claim, statuses=scoring.STATUSES
    )


@bp.route("/lead/<int:lead_id>/update", methods=["POST"])
@login_required
def update_claim(lead_id):
    cid = current_contractor_id()
    status = request.form.get("status") or None
    notes = request.form.get("notes")
    if status and status not in scoring.STATUSES:
        abort(400)
    with _db() as conn:
        if entitlements.lead_visible_to(conn, cid, lead_id) is None:
            abort(404)
        db.upsert_claim(conn, lead_id, cid, status=status, notes=notes)
    flash("Lead updated.", "ok")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.route("/export.csv")
@login_required
def export_csv():
    cid = current_contractor_id()
    with _db() as conn:
        leads = entitlements.visible_leads(conn, cid)
    buf = io.StringIO()
    import csv

    cols = ["id", "name", "category", "score", "claim_status", "phone",
            "email", "website", "address", "city", "distance_km", "note"]
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
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE is_active = 1"
        ).fetchone()["n"]
        contractors = conn.execute(
            "SELECT COUNT(*) AS n FROM contractors"
        ).fetchone()["n"]
    return render_template(
        "admin.html", scans=scans, areas=areas, total=total, contractors=contractors
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
