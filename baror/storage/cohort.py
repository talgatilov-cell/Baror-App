"""READ-ONLY cohort (platoon) view for instructors/commanders. Stdlib only; never imports streamlit.

Authorisation is enforced HERE, per call, from the database -- the caller is never trusted:
* The actor is identified only by ``actor_id``; the role and ``active`` flag are re-read from ``users`` inside the
  transaction on every call (no role cached in a session or passed in).
* Access = actor is ``active`` AND role in (instructor, admin) AND a row exists in ``instructor_platoons`` for the
  platoon in question. An admin has NO implicit access; admin needs explicit grants, which are audited.
* The authorisation predicate is part of the data SELECT itself (not only a pre-check), so a logic slip in the
  pre-check still returns no rows. Pre-check + data + audit row run in ONE BEGIN IMMEDIATE transaction (no TOCTOU).
* Targets must be ``role='cadet' AND active=1``. Staff accounts and deactivated cadets are not readable.
* Errors: actor not allowed to use the cohort view at all (cadet, inactive, unknown, malformed id) -> ``AuthError``.
  Anything the actor may not see (platoon not granted, no such platoon, cadet in another platoon, no such cadet,
  deleted/inactive/staff target) -> the SAME ``NotFoundError``, so ids cannot be probed.
* Data minimisation: id, name, gender, is_km (needed to grade), result date/run/push-ups. NO e-mail, password hash,
  login state, created_at or result row ids. Thresholds/grades are NOT here: domain code grades the raw values.
* No write function exists in this module. The only write statements are INSERTs into ``audit_log`` (every successful
  read, plus a 'denied' row for refused attempts by an existing account). tests/test_cohort.py scans this source.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from baror.errors import AuthError, NotFoundError
from baror.storage import accommodations, audit, db
from baror.storage.summaries import fold_summary

__all__ = ["my_platoons", "list_platoon_members", "cohort_summary", "get_cadet_results"]

_STAFF = "('instructor','admin')"
# True when :actor may read platoon :platoon. Used as a pre-check AND embedded in every data query.
_GRANTED = (f"EXISTS (SELECT 1 FROM users a JOIN instructor_platoons ip ON ip.instructor_id=a.id "
            f"WHERE a.id=:actor AND a.active=1 AND a.role IN {_STAFF} AND ip.platoon_id=:platoon)")


def _id(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2**63 else None


def _denial(c, actor):
    """Return the exception to raise, or None if the actor may use the cohort view at all."""
    a = c.execute("SELECT role,active FROM users WHERE id=?", (actor,)).fetchone() if actor else None
    if a is None or not a["active"] or a["role"] not in ("instructor", "admin"):
        return AuthError("NOT_AUTHORISED", detail="cohort view requires an active instructor/admin")
    return None


def _not_found(what: str) -> NotFoundError:
    return NotFoundError("COHORT_NOT_FOUND", detail=what)


def _run(db_path, actor_id, action, platoon_id, cadet_id, now, body):
    actor, platoon, cadet = _id(actor_id), _id(platoon_id), _id(cadet_id)
    denied: Exception | None = None
    out = None
    with db.transaction(db_path) as c:
        denied = _denial(c, actor)
        if denied is None:
            out = body(c, actor, platoon, cadet)
            if out is None:
                denied = _not_found(f"{action} not visible")
        if denied is not None and actor is not None and \
                c.execute("SELECT 1 FROM users WHERE id=?", (actor,)).fetchone():
            # refused probe by an existing account; commits with the transaction (nothing else was written)
            audit.record(c, actor_id=actor, action=action, target_user_id=cadet, platoon_id=platoon,
                         outcome="denied", now=now)
    if denied is not None:
        raise denied
    return out


def _members(c, actor, platoon):
    return c.execute(
        f"SELECT u.id,u.name,u.gender,u.is_km FROM users u WHERE u.platoon_id=:platoon AND u.role='cadet' "
        f"AND u.active=1 AND {_GRANTED} ORDER BY u.name, u.id", {"actor": actor, "platoon": platoon}).fetchall()


def my_platoons(db_path, actor_id) -> list[dict[str, Any]]:
    """The platoons this actor may read: [{id, name}] by name. Own grants only, so no cadet data and no audit row on
    success; a refused call by an existing account is audited as 'denied' like every other cohort call."""
    def body(c, actor, _platoon, _cadet):
        return [dict(r) for r in c.execute(
            "SELECT p.id, p.name FROM instructor_platoons ip JOIN platoons p ON p.id=ip.platoon_id "
            "WHERE ip.instructor_id=? ORDER BY p.name, p.id", (actor,))]
    return _run(db_path, actor_id, "cohort.platoons", None, None, None, body)


def list_platoon_members(db_path, actor_id, platoon_id, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Active cadets of one platoon the actor is granted: [{id,name,gender,is_km}] by name. Audited as one platoon-level row."""
    def body(c, actor, platoon, _cadet):
        if not c.execute(f"SELECT {_GRANTED}", {"actor": actor, "platoon": platoon}).fetchone()[0]:
            return None
        rows = [dict(r) for r in _members(c, actor, platoon)]
        audit.record(c, actor_id=actor, action="cohort.members", platoon_id=platoon, now=now)
        return rows
    return _run(db_path, actor_id, "cohort.members", platoon_id, None, now, body)


def cohort_summary(db_path, actor_id, platoon_id, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """One entry per active cadet: raw latest/best values, NO grading.

    {id,name,gender,is_km,result_count,last_test_date,
     latest_run:{test_date,run_tenths}|None, latest_pushups:{test_date,pushups}|None,
     best_run_tenths (lowest)|None, best_pushups (highest)|None, accommodations:[...]}
    'Latest' = newest test_date, ties by newest insertion. Audited with one row PER CADET read (so each cadet's
    disclosure is exact); a platoon with no cadets gets one platoon-level row.
    """
    def body(c, actor, platoon, _cadet):
        if not c.execute(f"SELECT {_GRANTED}", {"actor": actor, "platoon": platoon}).fetchone()[0]:
            return None
        rows = c.execute(
            f"SELECT u.id,u.name,u.gender,u.is_km,r.test_date,r.run_tenths,r.pushups FROM users u "
            f"LEFT JOIN results r ON r.pseudonym=u.pseudonym WHERE u.platoon_id=:platoon AND u.role='cadet' AND u.active=1 "
            f"AND {_GRANTED} ORDER BY u.name, u.id, r.test_date DESC, r.id DESC",
            {"actor": actor, "platoon": platoon}).fetchall()
        out = fold_summary(c, rows)
        if out:
            c.executemany("INSERT INTO audit_log(ts,actor_id,action,target_user_id,platoon_id) VALUES(?,?,?,?,?)",
                          [(audit.ts(now), actor, "cohort.summary", s["id"], platoon) for s in out])
        else:
            audit.record(c, actor_id=actor, action="cohort.summary", platoon_id=platoon, now=now)
        return out
    return _run(db_path, actor_id, "cohort.summary", platoon_id, None, now, body)


def get_cadet_results(db_path, actor_id, cadet_id, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """{'cadet': {id,name,gender,is_km}, 'results': [{test_date,run_tenths,pushups}] newest first,
    'accommodations': [...] newest first}.
    Authorised iff the cadet is an ACTIVE CADET whose CURRENT platoon the actor is granted. Audited per call."""
    def body(c, actor, _platoon, cadet):
        t = c.execute(
            f"SELECT t.id,t.name,t.gender,t.is_km,t.platoon_id,t.pseudonym FROM users t WHERE t.id=:cadet AND t.role='cadet' "
            f"AND t.active=1 AND t.platoon_id IS NOT NULL AND EXISTS (SELECT 1 FROM users a JOIN instructor_platoons ip "
            f"ON ip.instructor_id=a.id AND ip.platoon_id=t.platoon_id WHERE a.id=:actor AND a.active=1 "
            f"AND a.role IN {_STAFF})", {"actor": actor, "cadet": cadet}).fetchone()
        if t is None:
            return None
        res = c.execute("SELECT test_date,run_tenths,pushups FROM results WHERE pseudonym=? "
                        "ORDER BY test_date DESC, id DESC", (t["pseudonym"],)).fetchall()
        audit.record(c, actor_id=actor, action="cohort.cadet", target_user_id=t["id"], platoon_id=t["platoon_id"], now=now)
        return {"cadet": {k: t[k] for k in ("id", "name", "gender", "is_km")}, "results": [dict(r) for r in res],
                "accommodations": accommodations.fetch_public(c, [t["id"]])[t["id"]]}
    return _run(db_path, actor_id, "cohort.cadet", None, cadet_id, now, body)
