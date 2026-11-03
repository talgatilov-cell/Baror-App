"""Retention by deleting the identity link (ADR-0002). Stdlib only; never imports streamlit.

OPERATOR-ONLY, like ``baror.storage.ops``: the web app must not import this module (tests/test_retention.py
checks it). It is run by ``scripts/retention.py``, which also handles backup files.

What "6 months after the course" means here (assumptions listed in ADR-0002, to be confirmed by the owner):
* the course end is one date per platoon, ``platoons.course_end``;
* the deadline is ``course_end`` plus 6 CALENDAR months (a 31st maps to the last day of a shorter month);
* only accounts with role ``cadet`` are de-identified; staff accounts are not touched;
* a cadet with no platoon, or in a platoon without ``course_end``, is NEVER deleted automatically (counted in the
  report as ``unscheduled`` so the operator sees them).

What the job does for each due cadet, in ONE transaction with secure_delete on:
1. copies gender / is_km / platoon to the cadet's ``pseudonyms`` row (the only attributes the overviews need);
2. deletes the consumed allowlist entry (it holds the e-mail);
3. deletes the ``users`` row, i.e. name, e-mail, password hash and the pseudonym link (sessions cascade; audit rows that
   mention the account become NULL through their ON DELETE SET NULL foreign keys).
The results stay, under the pseudonym. Afterwards ``VACUUM`` and a WAL truncate remove the freed bytes from the file.
"""
from __future__ import annotations

import calendar
import datetime as dt
import sqlite3
from typing import Any

from baror.errors import NotFoundError, StorageError, ValidationError
from baror.storage import audit, db

RETENTION_MONTHS = 6


def add_months(d: dt.date, months: int) -> dt.date:
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def deadline(course_end: str, months: int = RETENTION_MONTHS) -> dt.date:
    return add_months(dt.date.fromisoformat(course_end), months)


def _do_set_course_end(c, actor, platoon_id, course_end) -> None:
    if course_end is not None:
        try:
            ok = dt.date.fromisoformat(course_end).isoformat() == course_end
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise ValidationError("INVALID_DATE", field="course_end", detail="course_end must be YYYY-MM-DD or None")
    n = c.execute("UPDATE platoons SET course_end=? WHERE id=?", (course_end, platoon_id)).rowcount
    if n != 1:
        raise NotFoundError(detail=f"platoon {platoon_id!r}")
    audit.record(c, actor_id=actor, action="retention.set_course_end", platoon_id=platoon_id)


def set_course_end(db_path, platoon_id: int, course_end: str | None) -> None:
    """Operator: start (or clear) the retention clock of a platoon. ``course_end`` is YYYY-MM-DD."""
    with db.transaction(db_path) as c:
        _do_set_course_end(c, None, platoon_id, course_end)


def plan(c, today: dt.date) -> dict[str, list[dict[str, Any]]]:
    """Classify every cadet: due / pending (clock running, not yet due) / unscheduled (no platoon or no course end)."""
    out: dict[str, list[dict[str, Any]]] = {"due": [], "pending": [], "unscheduled": []}
    rows = c.execute("SELECT u.id,u.email,u.gender,u.is_km,u.platoon_id,u.pseudonym,p.course_end FROM users u "
                     "LEFT JOIN platoons p ON p.id=u.platoon_id WHERE u.role='cadet'").fetchall()
    for r in rows:
        if r["course_end"] is None:
            out["unscheduled"].append(dict(r))
        elif deadline(r["course_end"]) <= today:
            out["due"].append(dict(r))
        else:
            out["pending"].append(dict(r))
    return out


def deidentified_pseudonyms(db_path) -> list[str]:
    """Every pseudonym whose link has been deleted so far (not only in the latest run), so that cleaning up after a
    crashed run is idempotent. A pseudonym carries no personal data once the link is gone."""
    with db.reader(db_path) as c:
        return [r[0] for r in c.execute("SELECT pseudonym FROM pseudonyms WHERE deidentified_at IS NOT NULL")]


def _scrub_pending(c) -> bool:
    """True if the last de-identification has not been followed by a successful scrub of the file."""
    last = lambda action: c.execute("SELECT COALESCE(MAX(id),0) FROM audit_log WHERE action=?", (action,)).fetchone()[0]  # noqa: E731
    return last("retention.run") > last("retention.scrubbed")


def run(db_path, *, today: dt.date | None = None, now: dt.datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """De-identify every cadet whose deadline has passed. Idempotent. Returns counts only (never names or e-mails).

    The deletion commits first; scrubbing the file afterwards (VACUUM, WAL truncate) is recorded in the audit log, and a
    scrub that failed or was interrupted is retried by the next run even when nothing is due any more.
    ``dry_run`` only classifies and writes nothing.
    """
    now_utc = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    today = today or now_utc.date()
    stamp = audit.ts(now_utc)
    if dry_run:
        with db.reader(db_path) as c:
            groups = plan(c, today)
        return {"deidentified": 0, "would_deidentify": len(groups["due"]), "pending": len(groups["pending"]),
                "unscheduled": len(groups["unscheduled"]), "scrubbed": False}
    with db.transaction(db_path, secure_delete=True) as c:
        groups = plan(c, today)
        for r in groups["due"]:
            c.execute("UPDATE pseudonyms SET gender=?, is_km=?, platoon_id=?, deidentified_at=? WHERE pseudonym=?",
                      (r["gender"], r["is_km"], r["platoon_id"], stamp, r["pseudonym"]))
            c.execute("DELETE FROM allowed_emails WHERE used_by=? OR email=?", (r["id"], r["email"]))
            c.execute("DELETE FROM users WHERE id=?", (r["id"],))
        if groups["due"]:
            audit.record(c, actor_id=None, action="retention.run", now=now_utc)
        pending = _scrub_pending(c)
    scrubbed = False
    if pending:
        _scrub_file(db_path)                      # raises StorageError if the WAL could not be truncated
        with db.transaction(db_path) as c:
            audit.record(c, actor_id=None, action="retention.scrubbed", now=now_utc)
        scrubbed = True
    return {"deidentified": len(groups["due"]), "pending": len(groups["pending"]),
            "unscheduled": len(groups["unscheduled"]), "scrubbed": scrubbed}


def _scrub_file(db_path) -> None:
    """Remove the freed bytes from the main file and the write-ahead log. Raises if the log cannot be truncated
    (a reader holding an old snapshot keeps the old frames alive); the caller retries on the next run."""
    conn = db.connect(db_path, secure_delete=True)
    try:
        conn.execute("VACUUM")
        busy, _log, _done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise StorageError(detail="write-ahead log could not be truncated (a reader is still open); will retry")
    except sqlite3.Error as exc:
        raise StorageError(detail=f"scrub failed: {type(exc).__name__}") from exc
    finally:
        conn.close()
