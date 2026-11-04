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
from baror.storage import admin, audit, db, people


def _op(db_path, core, *args, **kw):
    with db.transaction(db_path) as c:
        return core(c, None, *args, **kw)


def deactivate_user(db_path, user_id):
    return _op(db_path, admin._do_deactivate, user_id)


def reactivate_user(db_path, user_id):
    return _op(db_path, admin._do_reactivate, user_id)


def _do_set_role(c, actor, user_id, role, *, title=None, platoon_id=None, now=None):
    people._apply_role(c, actor, user_id, role, title=title, platoon_id=platoon_id, allow_admin=True, now=now)
    people._close_requests(c, decided_by=actor, now=now, target=user_id, requester=user_id)
    audit.record(c, actor_id=actor, action="admin.set_role", target_user_id=user_id,
                 platoon_id=platoon_id if role == "instructor" else None, now=now)


def set_role(db_path, user_id, role, *, title=None, platoon_id=None, now=None):
    """Operator: set any role, ``admin`` included (at most MAX_ADMINS active admins; the last admin cannot be demoted).
    Same chief / מ"מ / מ"כ limits as the web path, so an instructor needs ``title`` and ``platoon_id``, a chief a title."""
    return _op(db_path, _do_set_role, user_id, role, title=title, platoon_id=platoon_id, now=now)


def _do_set_title(c, actor, user_id, title, *, now=None):
    people._apply_title(c, user_id, title)
    audit.record(c, actor_id=actor, action="admin.set_title", target_user_id=user_id, now=now)


def set_title(db_path, user_id, title, *, now=None):
    """Operator: set the title (mp/smp for a chief, mm/cmdr for an instructor) with the same limits as a grant."""
    return _op(db_path, _do_set_title, user_id, title, now=now)


def create_platoon(db_path, name):
    return _op(db_path, admin._do_create_platoon, name)


def set_user_platoon(db_path, user_id, platoon_id):
    return _op(db_path, admin._do_set_user_platoon, user_id, platoon_id)


def _do_grant(c, actor, instructor_id, platoon_id, *, now=None):
    people._set_instructor_platoon(c, actor, instructor_id, platoon_id, now)
    audit.record(c, actor_id=actor, action="admin.grant", target_user_id=instructor_id, platoon_id=platoon_id, now=now)


def grant_platoon(db_path, instructor_id, platoon_id, *, now=None):
    """Operator: SET the platoon of an instructor (an instructor has exactly one; the destination's limits apply)."""
    return _op(db_path, _do_grant, instructor_id, platoon_id, now=now)


def _do_revoke(c, actor, instructor_id, platoon_id):
    n = c.execute("DELETE FROM instructor_platoons WHERE instructor_id=? AND platoon_id=?",
                  (instructor_id, platoon_id)).rowcount
    if n:    # a מ"מ without a platoon is no מ"מ: the title goes with the seat (keeps title='mm' <-> is_mm in step)
        c.execute("UPDATE users SET title=NULL WHERE id=? AND title='mm'", (instructor_id,))
    audit.record(c, actor_id=actor, action="admin.revoke", target_user_id=instructor_id, platoon_id=platoon_id)
    return n == 1


def revoke_platoon(db_path, instructor_id, platoon_id):
    """Operator: take the instructor out of the platoon (leaves them without one until ``grant_platoon``)."""
    return _op(db_path, _do_revoke, instructor_id, platoon_id)


def list_accounts(db_path):
    return _op(db_path, _do_list_accounts)


def _do_list_accounts(c, actor):
    return [dict(r) for r in c.execute(
        "SELECT u.id,u.email,u.name,u.role,u.title,u.active,COALESCE(ip.platoon_id,u.platoon_id) AS platoon_id,"
        "p.name AS platoon FROM users u LEFT JOIN instructor_platoons ip ON ip.instructor_id=u.id "
        "LEFT JOIN platoons p ON p.id=COALESCE(ip.platoon_id,u.platoon_id) ORDER BY u.id")]


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
