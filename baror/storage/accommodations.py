"""Accommodations (הקלות ופטורים). Stdlib only; never imports streamlit.

WHO MAY DO WHAT (owner decision of 2026-11-03; enforced HERE, per call, from the database, never from the caller):
* add      a cadet for THEMSELVES (source 'cadet'); an instructor for an active cadet of their own platoon (source
           'instructor', exactly as in ``baror.storage.cohort``; since schema v9 an admin has no accommodation rights);
           the chief for any active cadet (source 'chief').
* approve  APPROVAL STEP (owner request of 2026-11-03): a record whose source is 'cadet' starts 'pending'; records of an
           instructor, admin or the chief start 'approved' (the chief's are auto-approved). Only the CHIEF decides a
           pending record: ``approve_request`` makes it 'approved', ``reject_request`` makes it 'rejected' (the row is
           KEPT and still shown, labelled; it is not deleted). decided_at / decided_by are stored; both decisions are
           audited (``accommodation.approve`` / ``accommodation.reject``). Only an 'approved' record is in effect (see
           ``baror.domain.accommodation``). A record is decided once: a decided or missing one is 'not visible'.
           ``pending_requests`` lists the pending records for the chief. Deletion requests (below) are a separate thing.
* change   NOBODY. There is no update function for the content of a record (only the approval decision and the
           deletion-request flags ever change). A cadet can never edit or delete a record, not even their own.
* delete   an instructor may only REQUEST deletion (record of a cadet in the own platoon); the chief APPROVES
           (the row is deleted) or REJECTS (the request is cleared), or deletes directly with ``approve_delete`` (works
           with or without a pending request). Nobody else; an operator can through ``baror.storage.ops``.
* read     the data subject with ``list_own``; staff through the cohort/chief reads, which call ``fetch_public`` on the
           SAME connection as their own authorised statement. ``created_by`` is never returned.

Error discipline is that of ``baror.storage.cohort``: an actor who may not use the function at all (unknown, inactive,
wrong role, a cadet naming somebody else) gets ``AuthError``; anything the actor may not see (not granted, no such
record, no such cadet, staff or deactivated target) gets the SAME ``NotFoundError`` so ids cannot be probed. A refusal
by an existing account writes a 'denied' audit row; every success writes an ok row (action codes below).
Authorisation and the statement run in ONE ``BEGIN IMMEDIATE`` transaction (no check-then-act gap).
"""
from __future__ import annotations

import datetime as dt
import zoneinfo
from typing import Any, Callable

from baror.domain import accommodation as rules
from baror.errors import AuthError, NotFoundError, ValidationError
from baror.storage import audit, db

__all__ = ["add", "list_own", "request_delete", "approve_delete", "reject_delete", "pending_deletions", "fetch_public",
           "approve_request", "reject_request", "pending_requests"]

_PUBLIC = ("id", "kind", "scope", "start_date", "end_date", "note", "source", "approval", "created_at")
_SELECT_PUBLIC = ("SELECT id, user_id, kind, scope, start_date, end_date, note, source, approval, created_at, "
                  "delete_requested_at IS NOT NULL AS delete_requested FROM accommodations")
_STAFF = "('instructor')"       # schema v9: an admin has no accommodation rights
# True when :actor is an active instructor whose platoon is the platoon of the cadet :cadet (same predicate as cohort.py)
_GRANTED_FOR_CADET = ("EXISTS (SELECT 1 FROM users t JOIN users a ON a.id=:actor JOIN instructor_platoons ip "
                      f"ON ip.instructor_id=a.id AND ip.platoon_id=t.platoon_id WHERE t.id=:cadet AND t.role='cadet' "
                      f"AND t.active=1 AND a.active=1 AND a.role IN {_STAFF})")
_ISRAEL = zoneinfo.ZoneInfo("Asia/Jerusalem")


def _id(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2**63 else None


def _public(row) -> dict[str, Any]:
    d = {k: row[k] for k in _PUBLIC}
    d["delete_requested"] = bool(row["delete_requested"])
    return d


def fetch_public(c, user_ids) -> dict[int, list[dict[str, Any]]]:
    """Accommodations of the given users, newest first, as the public dicts (no created_by). For the staff reads: the
    CALLER has already authorised these ids on connection ``c``. Plain ids only; the placeholders are '?' literals."""
    ids = [i for i in (_id(u) for u in user_ids) if i is not None]
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    for r in c.execute(f"{_SELECT_PUBLIC} WHERE user_id IN ({marks}) ORDER BY id DESC", ids):
        out[r["user_id"]].append(_public(r))
    return out


def list_own(db_path, user_id: int) -> list[dict[str, Any]]:
    """The signed-in user's own records, newest first. Scoped to ``user_id`` in the statement; not audited (it is the
    person's own data)."""
    uid = _id(user_id)
    if uid is None:
        return []
    with db.reader(db_path) as c:
        return fetch_public(c, [uid])[uid]


# ------------------------------------------------------------------------------------------------------ plumbing
def _actor(c, actor):
    return c.execute("SELECT id, role, active FROM users WHERE id=?", (actor,)).fetchone() if actor else None


def _run(db_path, actor_id, action, cadet_id, now, body: Callable):
    """One transaction: ``body(c, actor_row, cadet)`` returns a result, or None when the target is not visible
    (-> NotFoundError). It raises AuthError itself when the actor's role may not use the function at all."""
    actor, cadet = _id(actor_id), _id(cadet_id)
    denied: Exception | None = None
    out = None
    with db.transaction(db_path) as c:
        a = _actor(c, actor)
        try:
            if a is None or not a["active"]:
                raise AuthError("NOT_AUTHORISED", detail="no active account")
            out = body(c, a, cadet)
            if out is None:
                denied = NotFoundError("COHORT_NOT_FOUND", detail=f"{action} not visible")
        except AuthError as exc:
            denied = exc
        if denied is not None and a is not None:
            audit.record(c, actor_id=actor, action=action, target_user_id=cadet, outcome="denied", now=now)
    if denied is not None:
        raise denied
    return out


def _today(now: dt.datetime | None, today: dt.date | None) -> dt.date:
    if today is not None:
        return today
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(_ISRAEL).date()


# --------------------------------------------------------------------------------------------------------- add
def add(db_path, actor_id, cadet_id, *, kind, scope, start_date, end_date, note=None,
        now: dt.datetime | None = None, today: dt.date | None = None) -> int:
    """Record an accommodation for ``cadet_id`` (the actor's own id for a cadet). Returns the new record id. A cadet's
    own record is stored 'pending' (not in effect until the chief approves it); staff records are 'approved'.
    ValidationError for bad values (after authorisation, so a refusal never depends on the input)."""
    def body(c, a, cadet):
        if a["role"] == "cadet":
            if cadet != a["id"]:
                raise AuthError("NOT_AUTHORISED", detail="a cadet may record only their own accommodations")
            source = "cadet"
        elif a["role"] == "instructor":
            if cadet is None or not c.execute(f"SELECT {_GRANTED_FOR_CADET}", {"actor": a["id"], "cadet": cadet}).fetchone()[0]:
                return None
            source = a["role"]
        elif a["role"] == "chief":
            if cadet is None or c.execute("SELECT 1 FROM users WHERE id=? AND role='cadet' AND active=1", (cadet,)).fetchone() is None:
                return None
            source = "chief"
        else:
            raise AuthError("NOT_AUTHORISED", detail="role may not record accommodations")
        rec = rules.validate(kind, scope, start_date, end_date, note, _today(now, today))
        if c.execute("SELECT count(*) FROM accommodations WHERE user_id=?", (cadet,)).fetchone()[0] >= rules.MAX_PER_CADET:
            raise ValidationError("ACCOMMODATION_LIMIT", field="kind", detail="too many records for one cadet")
        approval = "pending" if source == "cadet" else "approved"       # only the chief can approve a cadet's own entry
        new_id = int(c.execute(
            "INSERT INTO accommodations(user_id,kind,scope,start_date,end_date,note,source,approval,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cadet, rec["kind"], rec["scope"], rec["start_date"], rec["end_date"], rec["note"], source, approval, a["id"],
             audit.ts(now))).lastrowid)
        audit.record(c, actor_id=a["id"], action="accommodation.add", target_user_id=cadet, now=now)
        return new_id
    return _run(db_path, actor_id, "accommodation.add", cadet_id, now, body)


# ----------------------------------------------------------------------------------------------------- deletion
def _record_owner(c, accommodation_id):
    return c.execute("SELECT a.id AS rid, a.user_id AS cadet, a.delete_requested_at AS requested, a.approval AS approval "
                     "FROM accommodations a JOIN users u ON u.id=a.user_id WHERE a.id=? AND u.role='cadet'",
                     (accommodation_id,)).fetchone()


def request_delete(db_path, actor_id, accommodation_id, *, now: dt.datetime | None = None) -> None:
    """Instructor: ask for the deletion of a record of a cadet in the own platoon. Idempotent (a second request
    keeps the first one's time and requester). Nothing is deleted: the chief decides."""
    rid = _id(accommodation_id)

    def body(c, a, _cadet):
        if a["role"] != "instructor":
            raise AuthError("NOT_AUTHORISED", detail="only an instructor may request a deletion")
        rec = _record_owner(c, rid) if rid else None
        if rec is None or not c.execute(f"SELECT {_GRANTED_FOR_CADET}", {"actor": a["id"], "cadet": rec["cadet"]}).fetchone()[0]:
            return None
        c.execute("UPDATE accommodations SET delete_requested_at=?, delete_requested_by=? "
                  "WHERE id=? AND delete_requested_at IS NULL", (audit.ts(now), a["id"], rid))
        audit.record(c, actor_id=a["id"], action="accommodation.delete_request", target_user_id=rec["cadet"], now=now)
        return True
    _run(db_path, actor_id, "accommodation.delete_request", None, now, body)


def approve_delete(db_path, actor_id, accommodation_id, *, now: dt.datetime | None = None) -> None:
    """Chief only: delete the record (with or without a pending request). The deletion is audited."""
    rid = _id(accommodation_id)

    def body(c, a, _cadet):
        if a["role"] != "chief":
            raise AuthError("NOT_AUTHORISED", detail="only the chief may delete an accommodation")
        rec = _record_owner(c, rid) if rid else None
        if rec is None:
            return None
        c.execute("DELETE FROM accommodations WHERE id=?", (rid,))
        audit.record(c, actor_id=a["id"], action="accommodation.delete_approve", target_user_id=rec["cadet"], now=now)
        return True
    _run(db_path, actor_id, "accommodation.delete_approve", None, now, body)


def reject_delete(db_path, actor_id, accommodation_id, *, now: dt.datetime | None = None) -> None:
    """Chief only: clear a pending deletion request (the record stays). A record with no pending request is 'not
    visible' here, like a missing one."""
    rid = _id(accommodation_id)

    def body(c, a, _cadet):
        if a["role"] != "chief":
            raise AuthError("NOT_AUTHORISED", detail="only the chief may decide a deletion request")
        rec = _record_owner(c, rid) if rid else None
        if rec is None or rec["requested"] is None:
            return None
        c.execute("UPDATE accommodations SET delete_requested_at=NULL, delete_requested_by=NULL WHERE id=?", (rid,))
        audit.record(c, actor_id=a["id"], action="accommodation.delete_reject", target_user_id=rec["cadet"], now=now)
        return True
    _run(db_path, actor_id, "accommodation.delete_reject", None, now, body)


def pending_deletions(db_path, actor_id, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Chief only: the records with a deletion request, oldest request first. Audited as ONE row."""
    def body(c, a, _cadet):
        if a["role"] != "chief":
            raise AuthError("NOT_AUTHORISED", detail="only the chief sees pending deletions")
        rows = c.execute(
            "SELECT a.id, a.user_id AS cadet_id, u.name AS cadet_name, p.name AS platoon, a.kind, a.scope, a.start_date, "
            "a.end_date, a.note, a.source, a.delete_requested_at AS requested_at, r.name AS requested_by_name "
            "FROM accommodations a JOIN users u ON u.id=a.user_id LEFT JOIN platoons p ON p.id=u.platoon_id "
            "LEFT JOIN users r ON r.id=a.delete_requested_by "
            "WHERE a.delete_requested_at IS NOT NULL AND u.role='cadet' ORDER BY a.delete_requested_at, a.id").fetchall()
        audit.record(c, actor_id=a["id"], action="accommodation.pending", now=now)
        return [dict(r) for r in rows] or [None]       # [None]: an empty answer is still a success, not 'not visible'
    out = _run(db_path, actor_id, "accommodation.pending", None, now, body)
    return [] if out == [None] else out


# ------------------------------------------------------------------------------------------------------ approval
def _decide(db_path, actor_id, accommodation_id, *, outcome: str, action: str, now: dt.datetime | None) -> None:
    rid = _id(accommodation_id)

    def body(c, a, _cadet):
        if a["role"] != "chief":
            raise AuthError("NOT_AUTHORISED", detail="only the chief may decide an accommodation")
        rec = _record_owner(c, rid) if rid else None
        if rec is None or rec["approval"] != "pending":
            return None                                   # missing, a staff account's, or already decided: 'not visible'
        c.execute("UPDATE accommodations SET approval=?, decided_at=?, decided_by=? WHERE id=? AND approval='pending'",
                  (outcome, audit.ts(now), a["id"], rid))
        audit.record(c, actor_id=a["id"], action=action, target_user_id=rec["cadet"], now=now)
        return True
    _run(db_path, actor_id, action, None, now, body)


def approve_request(db_path, actor_id, accommodation_id, *, now: dt.datetime | None = None) -> None:
    """Chief only: approve a pending record (it is in effect from now on). decided_at / decided_by are stored and the
    decision is audited. A record that is not pending is 'not visible' (NotFoundError), like a missing one."""
    _decide(db_path, actor_id, accommodation_id, outcome="approved", action="accommodation.approve", now=now)


def reject_request(db_path, actor_id, accommodation_id, *, now: dt.datetime | None = None) -> None:
    """Chief only: reject a pending record. The row is KEPT with approval='rejected' (the cadet and the staff still see
    it, labelled) and it is never in effect. Audited."""
    _decide(db_path, actor_id, accommodation_id, outcome="rejected", action="accommodation.reject", now=now)


def pending_requests(db_path, actor_id, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Chief only: the records waiting for approval (a cadet's own entries), oldest first. Audited as ONE row."""
    def body(c, a, _cadet):
        if a["role"] != "chief":
            raise AuthError("NOT_AUTHORISED", detail="only the chief sees pending accommodations")
        rows = c.execute(
            "SELECT a.id, a.user_id AS cadet_id, u.name AS cadet_name, p.name AS platoon, a.kind, a.scope, a.start_date, "
            "a.end_date, a.note, a.source, a.created_at "
            "FROM accommodations a JOIN users u ON u.id=a.user_id LEFT JOIN platoons p ON p.id=u.platoon_id "
            "WHERE a.approval='pending' AND u.role='cadet' ORDER BY a.created_at, a.id").fetchall()
        audit.record(c, actor_id=a["id"], action="accommodation.pending_requests", now=now)
        return [dict(r) for r in rows] or [None]       # [None]: an empty answer is still a success, not 'not visible'
    out = _run(db_path, actor_id, "accommodation.pending_requests", None, now, body)
    return [] if out == [None] else out
