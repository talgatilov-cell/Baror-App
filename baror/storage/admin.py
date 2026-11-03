"""Admin operations, ACTOR-CHECKED. Stdlib only; never imports streamlit.

Every public function takes the acting user's id and, INSIDE the same write transaction as the change, re-reads that
user from the database and requires ``role='admin' AND active=1``. The role is never taken from the caller or the
session. Every change writes an ``audit_log`` row in the same transaction (ids only).

The conn-level ``_do_*`` cores are shared with ``baror.storage.ops`` (operator CLI path, no actor).
Safety rules: the last active admin cannot be deactivated or demoted (lock-out guard); a role change away from
instructor/admin deletes that user's platoon grants; deactivation revokes every session in the same transaction.
"""
from __future__ import annotations

from typing import Any

from baror.errors import AuthError, ConflictError, NotFoundError, ValidationError
from baror.storage import allowlist, audit, db

ROLES = ("cadet", "instructor", "admin", "chief")
STAFF_ROLES = ("instructor", "admin")


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


def _guard_last_admin(c, user_id) -> None:
    row = c.execute("SELECT role,active FROM users WHERE id=?", (user_id,)).fetchone()
    if row and row["role"] == "admin" and row["active"]:
        others = c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1 AND id<>?", (user_id,)).fetchone()[0]
        if others == 0:
            raise ConflictError("LAST_ADMIN", detail="would leave no active admin")


# ------------------------------------------------------------ cores (conn level, no authorisation)
def _do_allow_add(c, actor, email, *, role="cadet", platoon_id=None, source="ui") -> str:
    res = allowlist.add_in_tx(c, email, role=role, platoon_id=platoon_id, added_by=actor, source=source)
    audit.record(c, actor_id=actor, action="admin.allow_add", platoon_id=platoon_id)
    return res


def _do_allow_remove(c, actor, email) -> bool:
    res = allowlist.remove_in_tx(c, email)
    audit.record(c, actor_id=actor, action="admin.allow_remove")
    return res


def _do_deactivate(c, actor, user_id) -> None:
    _target(c, user_id)
    _guard_last_admin(c, user_id)
    c.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
    c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,))
    audit.record(c, actor_id=actor, action="admin.deactivate", target_user_id=user_id)


def _do_reactivate(c, actor, user_id) -> None:
    _target(c, user_id)
    c.execute("UPDATE users SET active=1, failed_logins=0, locked_until=NULL WHERE id=?", (user_id,))
    audit.record(c, actor_id=actor, action="admin.reactivate", target_user_id=user_id)


def _do_set_role(c, actor, user_id, role) -> None:
    if role not in ROLES:
        raise ValidationError("INVALID_INPUT", field="role", detail=f"role {role!r}")
    _target(c, user_id)
    if role != "admin":
        _guard_last_admin(c, user_id)
    c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    if role not in STAFF_ROLES:   # cadet or chief: no platoon grants (a chief sees everything through baror.storage.chief)
        c.execute("DELETE FROM instructor_platoons WHERE instructor_id=?", (user_id,))
    if role != "cadet":           # staff and chiefs are not platoon members
        c.execute("UPDATE users SET platoon_id=NULL WHERE id=?", (user_id,))
    audit.record(c, actor_id=actor, action="admin.set_role", target_user_id=user_id)


def _do_create_platoon(c, actor, name) -> int:
    if not isinstance(name, str) or name != name.strip() or not 1 <= len(name) <= 60:
        raise ValidationError("INVALID_INPUT", field="platoon", detail="platoon name")
    pid = int(c.execute("INSERT INTO platoons(name) VALUES(?)", (name,)).lastrowid)  # UNIQUE -> ConflictError
    audit.record(c, actor_id=actor, action="admin.platoon_create", platoon_id=pid)
    return pid


def _do_set_user_platoon(c, actor, user_id, platoon_id) -> None:
    if _target(c, user_id)["role"] != "cadet":
        raise ValidationError("INVALID_INPUT", detail="only cadets belong to a platoon")
    if platoon_id is not None and (not _ok_id(platoon_id) or
                                   c.execute("SELECT 1 FROM platoons WHERE id=?", (platoon_id,)).fetchone() is None):
        raise NotFoundError(detail=f"platoon {platoon_id!r}")
    c.execute("UPDATE users SET platoon_id=? WHERE id=?", (platoon_id, user_id))
    audit.record(c, actor_id=actor, action="admin.set_platoon", target_user_id=user_id, platoon_id=platoon_id)


def _do_grant(c, actor, instructor_id, platoon_id) -> None:
    if _target(c, instructor_id)["role"] not in STAFF_ROLES:
        raise ValidationError("INVALID_INPUT", detail="grants go to instructors/admins only")
    if not _ok_id(platoon_id) or c.execute("SELECT 1 FROM platoons WHERE id=?", (platoon_id,)).fetchone() is None:
        raise NotFoundError(detail=f"platoon {platoon_id!r}")
    c.execute("INSERT OR IGNORE INTO instructor_platoons(instructor_id,platoon_id,granted_by) VALUES(?,?,?)",
              (instructor_id, platoon_id, actor))
    audit.record(c, actor_id=actor, action="admin.grant", target_user_id=instructor_id, platoon_id=platoon_id)


def _do_revoke(c, actor, instructor_id, platoon_id) -> bool:
    n = c.execute("DELETE FROM instructor_platoons WHERE instructor_id=? AND platoon_id=?",
                  (instructor_id, platoon_id)).rowcount
    audit.record(c, actor_id=actor, action="admin.revoke", target_user_id=instructor_id, platoon_id=platoon_id)
    return n == 1


def _do_list_accounts(c, actor) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(
        "SELECT u.id,u.email,u.name,u.role,u.active,u.platoon_id,p.name AS platoon FROM users u "
        "LEFT JOIN platoons p ON p.id=u.platoon_id ORDER BY u.id")]


def _admin(db_path, actor_id, core, *args, **kw):
    with db.transaction(db_path) as c:
        _require_admin(c, actor_id)
        return core(c, actor_id, *args, **kw)


# ------------------------------------------------------------ public, actor-checked
def add_allowed_email(db_path, actor_id, email, *, role="cadet", platoon_id=None):
    return _admin(db_path, actor_id, _do_allow_add, email, role=role, platoon_id=platoon_id, source="ui")


def remove_allowed_email(db_path, actor_id, email):
    return _admin(db_path, actor_id, _do_allow_remove, email)


def list_allowed_emails(db_path, actor_id, *, status=None):
    with db.transaction(db_path) as c:
        _require_admin(c, actor_id)
        return allowlist.list_in_tx(c, status=status)


def list_accounts(db_path, actor_id):
    return _admin(db_path, actor_id, _do_list_accounts)


def deactivate_user(db_path, actor_id, user_id):
    return _admin(db_path, actor_id, _do_deactivate, user_id)


def reactivate_user(db_path, actor_id, user_id):
    return _admin(db_path, actor_id, _do_reactivate, user_id)


def set_role(db_path, actor_id, user_id, role):
    return _admin(db_path, actor_id, _do_set_role, user_id, role)


def create_platoon(db_path, actor_id, name):
    return _admin(db_path, actor_id, _do_create_platoon, name)


def set_user_platoon(db_path, actor_id, user_id, platoon_id):
    return _admin(db_path, actor_id, _do_set_user_platoon, user_id, platoon_id)


def grant_platoon(db_path, actor_id, instructor_id, platoon_id):
    return _admin(db_path, actor_id, _do_grant, instructor_id, platoon_id)


def revoke_platoon(db_path, actor_id, instructor_id, platoon_id):
    return _admin(db_path, actor_id, _do_revoke, instructor_id, platoon_id)
