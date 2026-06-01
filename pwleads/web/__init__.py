"""Flask web app for the pwleads subscription business.

Create the app with :func:`create_app`. It reuses the same SQLite database and
business-logic modules as the CLI -- the web layer is just a thin front end
over ``auth``, ``billing``, ``entitlements`` and ``scan``/``enrich``.
"""

from __future__ import annotations

import os

from flask import Flask

from .. import db


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or os.environ.get("PWLEADS_DB", db.DEFAULT_DB)
    app.config["ADMIN_PASSWORD"] = os.environ.get("PWLEADS_ADMIN_PASSWORD", "admin")
    app.secret_key = os.environ.get("PWLEADS_SECRET", "dev-secret-change-me")

    # Ensure schema exists and plans are seeded on startup.
    with db.connect(app.config["DB_PATH"]) as conn:
        db.seed_plans(conn)

    from .routes import bp

    app.register_blueprint(bp)
    return app
