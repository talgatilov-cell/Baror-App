"""OPERATOR-ONLY entry points: the same admin operations WITHOUT an actor check.

Trust model: whoever can run ``scripts/*.py`` against the database file already has full read/write access to the
file, so authenticating them inside the app would add nothing. The web app MUST NOT import this module;
tests/test_admin.py::test_ops_is_not_imported_by_app_code fails if any module under ``baror/`` other than this one
imports it. This is also the only way to create the FIRST admin (bootstrap).
"""
from __future__ import annotations

import datetime as dt

from baror.domain import accommodation as rules
from baror.errors import NotFoundError
from baror.storage import admin, audit, db


def _op(db_path, core, *args, **kw):
    with db.transaction(db_path) as c:
        return core(c, None, *args, **kw)


def deactivate_user(db_path, user_id):
    return _op(db_path, admin._do_deactivate, user_id)


def reactivate_user(db_path, user_id):
    return _op(db_path, admin._do_reactivate, user_id)


def set_role(db_path, user_id, role):
    return _op(db_path, admin._do_set_role, user_id, role)


def create_platoon(db_path, name):
    return _op(db_path, admin._do_create_platoon, name)


def set_user_platoon(db_path, user_id, platoon_id):
    return _op(db_path, admin._do_set_user_platoon, user_id, platoon_id)


def grant_platoon(db_path, instructor_id, platoon_id):
    return _op(db_path, admin._do_grant, instructor_id, platoon_id)


def revoke_platoon(db_path, instructor_id, platoon_id):
    return _op(db_path, admin._do_revoke, instructor_id, platoon_id)


def list_accounts(db_path):
    return _op(db_path, admin._do_list_accounts)


# ------------------------------------------------------------ accommodations (no actor check: the operator owns the file)
def add_accommodation(db_path, cadet_id, *, kind, scope, start_date, end_date, note=None, today=None, now=None):
    """Record an accommodation for a cadet as source 'admin' (the operator acts as the system administrator)."""
    rec = rules.validate(kind, scope, start_date, end_date, note, today or dt.date.today())
    with db.transaction(db_path) as c:
        if c.execute("SELECT 1 FROM users WHERE id=? AND role='cadet'", (cadet_id,)).fetchone() is None:
            raise NotFoundError("COHORT_NOT_FOUND", detail=f"no cadet {cadet_id!r}")
        new_id = int(c.execute(
            "INSERT INTO accommodations(user_id,kind,scope,start_date,end_date,note,source,approval,created_at) "
            "VALUES(?,?,?,?,?,?,'admin','approved',?)",       # the operator's record is in effect at once
            (cadet_id, rec["kind"], rec["scope"], rec["start_date"], rec["end_date"], rec["note"], audit.ts(now))).lastrowid)
        audit.record(c, actor_id=None, action="accommodation.add", target_user_id=cadet_id, now=now)
        return new_id


def delete_accommodation(db_path, accommodation_id, *, now=None) -> bool:
    """Delete one record (the operator bypasses the request/approve flow). True if it existed."""
    with db.transaction(db_path) as c:
        row = c.execute("SELECT user_id FROM accommodations WHERE id=?", (accommodation_id,)).fetchone()
        if row is None:
            return False
        c.execute("DELETE FROM accommodations WHERE id=?", (accommodation_id,))
        audit.record(c, actor_id=None, action="accommodation.delete_approve", target_user_id=row["user_id"], now=now)
        return True


def list_accommodations(db_path):
    """Every record with the cadet's name and platoon (operator view), newest first."""
    with db.reader(db_path) as c:
        return [dict(r) for r in c.execute(
            "SELECT a.id, a.user_id AS cadet_id, u.name AS cadet_name, p.name AS platoon, a.kind, a.scope, a.start_date, "
            "a.end_date, a.note, a.source, a.approval, a.delete_requested_at IS NOT NULL AS delete_requested "
            "FROM accommodations a JOIN users u ON u.id=a.user_id LEFT JOIN platoons p ON p.id=u.platoon_id "
            "ORDER BY a.id DESC")]
