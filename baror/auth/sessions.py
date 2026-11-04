"""Server-side session tokens for the 'stay signed in after reload' feature (brief T3.4). Stdlib only.

The browser holds a random bearer token in a cookie; the database holds only sha256(token). A stolen database
therefore does not yield usable tokens, and a token can be revoked server-side (logout, password change,
account deletion cascades).

Honest limits (see research_notes/user_isolation.md): the token is a bearer credential and the cookie is
script-set, so it is NOT HttpOnly. Anyone who obtains the token (XSS, shared computer, copied cookie jar) IS that
user until it expires or is revoked.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
from typing import Any

from baror.storage import db

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
MAX_ACTIVE_PER_USER = 10
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now(now: dt.datetime | None) -> dt.datetime:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0)


def _iso(t: dt.datetime) -> str:
    return t.strftime(_FMT)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def issue(db_path, user_id: int, days: int = 14, now: dt.datetime | None = None) -> str:
    """Create a session; returns the raw token (the only time it exists server-side)."""
    t = _now(now)
    token = secrets.token_urlsafe(32)  # 32 random bytes -> 43 url-safe chars
    assert TOKEN_RE.fullmatch(token)
    with db.transaction(db_path) as c:
        c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                  (hash_token(token), user_id, _iso(t + dt.timedelta(days=days))))
        # cap: keep the newest MAX_ACTIVE_PER_USER live sessions, delete the rest
        c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN ("
                  " SELECT token_hash FROM sessions WHERE user_id=? AND revoked=0 AND expires_at>? "
                  " ORDER BY expires_at DESC LIMIT ?)", (user_id, user_id, _iso(t), MAX_ACTIVE_PER_USER))
    return token


def resolve(db_path, token: Any, now: dt.datetime | None = None, *, slide_days: int | None = None) -> dict | None:
    """Return the user dict for a valid token, else None. Never raises for malformed tokens.

    Role and ``active`` are read from the users table on EVERY call (never cached in the session), so a demotion or
    a deactivation takes effect on the next request even if ``revoke_all`` was somehow not run.

    ``slide_days``: if set and less than half the lifetime remains, extend expiry to now+slide_days
    (idle expiry: an unused session dies after ``slide_days``; at most one write per half-life).
    """
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        return None
    t = _now(now)
    th = hash_token(token)
    with db.reader(db_path) as c:
        row = c.execute(
            "SELECT s.expires_at, u.id, u.email, u.name, u.gender, u.is_km, u.must_change_pw, u.role, u.title, "
            "COALESCE(ip.platoon_id, u.platoon_id) AS platoon_id "
            "FROM sessions s JOIN users u ON u.id=s.user_id LEFT JOIN instructor_platoons ip ON ip.instructor_id=u.id "
            "WHERE s.token_hash=? AND s.revoked=0 AND s.expires_at>? AND u.active=1",
            (th, _iso(t))).fetchone()
    if row is None:
        return None
    if slide_days:
        remaining = dt.datetime.strptime(row["expires_at"], _FMT).replace(tzinfo=dt.timezone.utc) - t
        if remaining < dt.timedelta(days=slide_days) / 2:
            with db.transaction(db_path) as c:
                c.execute("UPDATE sessions SET expires_at=? WHERE token_hash=? AND revoked=0",
                          (_iso(t + dt.timedelta(days=slide_days)), th))
    return {k: row[k] for k in ("id", "email", "name", "gender", "is_km", "must_change_pw", "role", "title", "platoon_id")}


def revoke(db_path, token: Any) -> None:
    if isinstance(token, str) and TOKEN_RE.fullmatch(token):
        with db.transaction(db_path) as c:
            c.execute("UPDATE sessions SET revoked=1 WHERE token_hash=?", (hash_token(token),))


def revoke_all(db_path, user_id: int) -> None:
    with db.transaction(db_path) as c:
        c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,))


def purge_expired(db_path, now: dt.datetime | None = None) -> int:
    with db.transaction(db_path) as c:
        return c.execute("DELETE FROM sessions WHERE expires_at<=? OR revoked=1", (_iso(_now(now)),)).rowcount
