"""Contractor accounts: registration, password hashing, authentication.

Password hashing uses ``werkzeug.security`` (which ships with Flask) so we
add no extra dependency. The database only ever stores the salted hash.
"""

from __future__ import annotations

import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

from . import db


class AuthError(ValueError):
    """Raised for bad sign-up / login input (duplicate email, wrong password)."""


def register(
    conn: sqlite3.Connection,
    business_name: str,
    email: str,
    password: str,
    home_lat: float | None = None,
    home_lon: float | None = None,
) -> int:
    """Create a contractor account. Returns the new contractor id."""
    business_name = (business_name or "").strip()
    email = (email or "").strip().lower()
    if not business_name:
        raise AuthError("Business name is required.")
    if "@" not in email or "." not in email:
        raise AuthError("A valid email is required.")
    if len(password or "") < 6:
        raise AuthError("Password must be at least 6 characters.")
    if db.get_contractor_by_email(conn, email) is not None:
        raise AuthError("An account with that email already exists.")
    return db.create_contractor(
        conn,
        business_name=business_name,
        email=email,
        password_hash=generate_password_hash(password),
        home_lat=home_lat,
        home_lon=home_lon,
    )


def authenticate(
    conn: sqlite3.Connection, email: str, password: str
) -> sqlite3.Row | None:
    """Return the contractor row if credentials are valid, else None."""
    contractor = db.get_contractor_by_email(conn, (email or "").strip().lower())
    if contractor is None:
        return None
    if not check_password_hash(contractor["password_hash"], password or ""):
        return None
    return contractor
