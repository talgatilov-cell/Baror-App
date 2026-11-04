"""Admin operations on accounts, ACTOR-CHECKED. Stdlib only; never imports streamlit.

Every public function takes the acting user's id and, INSIDE the same write transaction as the change, re-reads that
user from the database and requires ``role='admin' AND active=1``. The role is never taken from the caller or the
session. Every change writes an ``audit_log`` row in the same transaction (ids only).

Schema v9: roles, titles and platoons of people are managed in ``baror.storage.people`` (admin AND chief). What stays
here is deactivation / reactivation (which keep ``instructor_platoons.is_mm`` in step with ``users.active``) and
creating platoons. The conn-level ``_do_*`` cores are shared with ``baror.storage.ops`` (operator CLI path, no actor).
Safety rules: the last active admin cannot be deactivated or demoted (lock-out guard); deactivation revokes every
session in the same transaction and closes the change requests about that account.
"""
from __future__ import annotations

from typing import Any

from baror.errors import AuthError, NotFoundError, ValidationError
from baror.storage import audit, db, people

_guard_last_admin = people._guard_last_admin


def _ok_id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2**63


def _require_admin(c, actor_id) -> None:
    row = c.execute("SELECT 1 FROM users WHERE id=? AND role='admin' AND active=1", (actor_id,)).fetchone() \
        if _ok_id(actor_id) else None
    if row is None:
        raise AuthError("NOT_AUTHORISED", detail="admin required")


def _target(c, user_id) -> Any:
    row = c.execute("SELECT id,role,active FROM users WHERE id=?", (user_id,)).fetchone() if _ok_id(user_id) else None
    if row is None:
        raise NotFoundError(detail=f"user {user_id!r}")
    return row


# ------------------------------------------------------------ cores (conn level, no authorisation)
def _do_deactivate(c, actor, user_id) -> None:
    _target(c, user_id)
    _guard_last_admin(c, user_id)
    c.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
    c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,))
    people._set_active_mm_flag(c, user_id, False)
    people._close_requests(c, decided_by=actor, target=user_id, requester=user_id)   # about them AND made by them
    audit.record(c, actor_id=actor, action="admin.deactivate", target_user_id=user_id)


def _do_reactivate(c, actor, user_id) -> None:
    _target(c, user_id)
    people.reactivate_checks(c, user_id)       # ConflictError: MM_TAKEN / TITLE_TAKEN / CHIEF_LIMIT / CMDR_LIMIT / ADMIN_LIMIT
    c.execute("UPDATE users SET active=1, failed_logins=0, locked_until=NULL WHERE id=?", (user_id,))
    people._set_active_mm_flag(c, user_id, True)
    audit.record(c, actor_id=actor, action="admin.reactivate", target_user_id=user_id)


def _do_create_platoon(c, actor, name) -> int:
    if not isinstance(name, str) or name != name.strip() or not 1 <= len(name) <= 60:
        raise ValidationError("INVALID_INPUT", field="platoon", detail="platoon name")
    pid = int(c.execute("INSERT INTO platoons(name) VALUES(?)", (name,)).lastrowid)  # UNIQUE -> ConflictError
    audit.record(c, actor_id=actor, action="admin.platoon_create", platoon_id=pid)
    return pid


def _do_set_user_platoon(c, actor, user_id, platoon_id) -> None:
    """Operator path: put a CADET into a platoon (or ``None``). Instructors get theirs through ``ops.grant_platoon``."""
    if _target(c, user_id)["role"] != "cadet":
        raise ValidationError("INVALID_INPUT", detail="only cadets belong to a platoon")
    if platoon_id is not None and (not _ok_id(platoon_id) or
                                   c.execute("SELECT 1 FROM platoons WHERE id=?", (platoon_id,)).fetchone() is None):
        raise NotFoundError(detail=f"platoon {platoon_id!r}")
    c.execute("UPDATE users SET platoon_id=? WHERE id=?", (platoon_id, user_id))
    audit.record(c, actor_id=actor, action="admin.set_platoon", target_user_id=user_id, platoon_id=platoon_id)


def _admin(db_path, actor_id, core, *args, **kw):
    with db.transaction(db_path) as c:
        _require_admin(c, actor_id)
        return core(c, actor_id, *args, **kw)


# ------------------------------------------------------------ public, actor-checked
def deactivate_user(db_path, actor_id, user_id):
    return _admin(db_path, actor_id, _do_deactivate, user_id)


def reactivate_user(db_path, actor_id, user_id):
    """Raises ConflictError (MM_TAKEN, TITLE_TAKEN, CHIEF_LIMIT, CMDR_LIMIT) when the account's old seat is taken."""
    return _admin(db_path, actor_id, _do_reactivate, user_id)


def create_platoon(db_path, actor_id, name):
    return _admin(db_path, actor_id, _do_create_platoon, name)
