"""People management (schema v9): who holds which role, title and platoon, and the change requests. ACTOR-CHECKED.
Stdlib only; never imports streamlit.

Every public function takes ``(db_path, actor_id, ...)``. Inside ONE ``BEGIN IMMEDIATE`` transaction it re-reads the actor
from ``users`` (``active=1`` and a role allowed for that function; nothing about the actor is ever passed in or cached),
checks the rule, writes, and writes the audit row. Audit rows hold ids and action codes only, never an e-mail or a name.

Errors: an actor who may not call the function at all, or may not do THAT to THAT target -> ``AuthError`` (message key
``NOT_AUTHORISED`` / ``ROLE_NOT_ALLOWED``); anything the actor may not see (a chief asking about an admin, an instructor
asking about a cadet of another platoon, an unknown id) -> ``NotFoundError``. Both leave a ``denied`` audit row when the
account exists (nothing else of the call is kept); a stranger leaves nothing. State conflicts (limits, taken title, the
account changed since it was verified) -> ``ConflictError``; bad input -> ``ValidationError``.

Structure rules (``baror.domain.roles``) are enforced here inside the transaction:
* one company: at most ``MAX_CHIEFS`` active chiefs, titles ``mp``/``smp`` unique among them;
* a platoon has at most one active מ"מ (``mm``; also a DATABASE rule: ``instructor_platoons.is_mm`` + partial unique
  index) and at most ``MAX_CMDRS_PER_PLATOON`` other active instructors. An instructor WITHOUT a title (a legacy account)
  counts toward that limit until a title is set. Replacing a מ"מ is two steps: the old one becomes ``cmdr``, then the new
  one ``mm``. Each instructor belongs to exactly one platoon (``instructor_platoons.instructor_id`` is the primary key);
* invariant kept in step in the same transaction: ``instructor_platoons.is_mm = 1`` iff ``users.title = 'mm'`` and
  ``users.active = 1``. ``users.title`` stays the source of truth for display.

``_apply_role`` / ``_apply_title`` / ``_place_instructor`` are the shared cores; ``baror.storage.ops`` (operator path, no
actor) and ``baror.storage.admin`` reuse them. This module never imports ``admin`` or ``ops``.
"""
from __future__ import annotations

import datetime as dt
import secrets
from typing import Any, Callable

from baror.domain import roles as R
from baror.errors import AuthError, BarorError, ConflictError, NotFoundError, ValidationError
from baror.storage import audit, db, repo

__all__ = ["search_accounts", "set_role", "set_title", "move_instructor", "list_platoons", "move_cadet", "add_cadet",
           "edit_cadet", "delete_cadet", "nominate_cmdr", "pending_changes", "my_changes", "approve_change",
           "reject_change", "release_email", "join_link", "rotate_join_links"]

MAX_PENDING_PER_REQUESTER = 30
MAX_PENDING_NOMINATIONS = 5
MAX_SEARCH = 100
_ADMIN_CHIEF = ("admin", "chief")
_EDITABLE = ("name", "gender", "is_km", "platoon_id")


def _id(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2**63 else None


def _conflict(key: str, **params) -> ConflictError:
    return ConflictError(key, params=params, detail=key)


def _run(db_path, actor_id, action: str, allowed_roles: tuple[str, ...], body: Callable, *, now=None, target=None,
         platoon=None):
    """Authorise the actor and run ``body(c, actor_row)`` in one transaction.

    A refusal (``AuthError`` / ``NotFoundError``, from the role gate or raised by ``body``) undoes whatever ``body`` had
    written, leaves ONE ``denied`` audit row if the actor account exists, and is raised after the commit. Any other
    exception rolls the whole transaction back."""
    actor = _id(actor_id)
    err: BarorError | None = None
    out = None
    with db.transaction(db_path) as c:
        a = c.execute("SELECT id,role,title,active FROM users WHERE id=?", (actor,)).fetchone() if actor else None
        if a is None or not a["active"] or a["role"] not in allowed_roles:
            err = AuthError("NOT_AUTHORISED", detail=f"{action}: role not allowed")
        else:
            c.execute("SAVEPOINT body")
            try:
                out = body(c, a)
            except (AuthError, NotFoundError) as exc:
                c.execute("ROLLBACK TO body")
                err = exc
            c.execute("RELEASE body")
        if err is not None and a is not None:
            audit.record(c, actor_id=actor, action=action, target_user_id=_id(target), platoon_id=_id(platoon),
                         outcome="denied", now=now)
    if err is not None:
        raise err
    return out


def _not_visible(what: str) -> NotFoundError:
    return NotFoundError("COHORT_NOT_FOUND", detail=f"{what} not visible")


def _no_role(detail: str) -> AuthError:
    return AuthError("ROLE_NOT_ALLOWED", detail=detail)


# ------------------------------------------------------------------------------------------ structure helpers
def _platoon_exists(c, platoon_id) -> bool:
    return _id(platoon_id) is not None and c.execute("SELECT 1 FROM platoons WHERE id=?", (platoon_id,)).fetchone() is not None


def _active_mm(c, platoon_id, exclude: int = -1):
    row = c.execute("SELECT ip.instructor_id FROM instructor_platoons ip JOIN users u ON u.id=ip.instructor_id "
                    "WHERE ip.platoon_id=? AND ip.is_mm=1 AND u.active=1 AND u.id<>?", (platoon_id, exclude)).fetchone()
    return row["instructor_id"] if row else None


def _cmdr_count(c, platoon_id, exclude: int = -1) -> int:
    """Active instructors of a platoon who are not its מ"מ; legacy instructors without a title are included."""
    return c.execute("SELECT count(*) FROM instructor_platoons ip JOIN users u ON u.id=ip.instructor_id "
                     "WHERE ip.platoon_id=? AND ip.is_mm=0 AND u.active=1 AND u.role='instructor' AND u.id<>?",
                     (platoon_id, exclude)).fetchone()[0]


def _check_slot(c, user_id: int, platoon_id: int, title: str | None) -> None:
    """Raise MM_TAKEN / CMDR_LIMIT if ``user_id`` cannot take the slot ``title`` in ``platoon_id`` (the user's own
    current row is not counted). ``title`` None or ``cmdr`` is a מ"כ slot (legacy untitled instructors count as מ"כ)."""
    if title == "mm":
        if _active_mm(c, platoon_id, user_id) is not None:
            raise _conflict("MM_TAKEN")
    elif _cmdr_count(c, platoon_id, user_id) >= R.MAX_CMDRS_PER_PLATOON:
        raise _conflict("CMDR_LIMIT", max=R.MAX_CMDRS_PER_PLATOON)


def _place_instructor(c, user_id: int, platoon_id: int, title: str | None, granted_by: int | None, now=None) -> None:
    """(Re)write the one ``instructor_platoons`` row of an instructor; ``is_mm`` follows the title. Limits are the
    caller's business (``_check_slot``)."""
    c.execute("DELETE FROM instructor_platoons WHERE instructor_id=?", (user_id,))
    c.execute("INSERT INTO instructor_platoons(instructor_id,platoon_id,is_mm,granted_by,granted_at) "
              "VALUES(?,?,CASE WHEN ?='mm' AND (SELECT active FROM users WHERE id=?)=1 THEN 1 ELSE 0 END,?,?)",
              (user_id, platoon_id, title, user_id, granted_by, audit.ts(now)))


def _close_requests(c, *, decided_by: int | None, now=None, target: int | None = None, requester: int | None = None,
                    delete_is_approved: bool = False, exclude: int | None = None) -> int:
    """Close the PENDING requests about ``target`` and/or made by ``requester`` (one of them is given). They become
    ``rejected``; with ``delete_is_approved`` a pending ``delete`` is ``approved`` instead (its goal was reached)."""
    where, args = [], []
    if target is not None:
        where.append("target_user_id=?"); args.append(target)
    if requester is not None:
        where.append("requested_by=?"); args.append(requester)
    if not where:
        return 0
    return c.execute(
        "UPDATE cadet_change_requests SET status=CASE WHEN kind='delete' AND ? THEN 'approved' ELSE 'rejected' END, "
        "decided_by=?, decided_at=? WHERE status='pending' AND (" + " OR ".join(where) + ") AND id IS NOT ?",   # `where`: literals
        (1 if delete_is_approved else 0, decided_by, audit.ts(now), *args, exclude)).rowcount


def _guard_last_admin(c, user_id) -> None:
    row = c.execute("SELECT role,active FROM users WHERE id=?", (user_id,)).fetchone()
    if row and row["role"] == "admin" and row["active"]:
        others = c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1 AND id<>?", (user_id,)).fetchone()[0]
        if others == 0:
            raise ConflictError("LAST_ADMIN", detail="would leave no active admin")


def _check_chief_title(c, user_id: int, title) -> None:
    if title not in R.CHIEF_TITLES:
        raise ValidationError("TITLE_REQUIRED", field="title", detail="chief title mp/smp required")
    if c.execute("SELECT 1 FROM users WHERE role='chief' AND active=1 AND title=? AND id<>?", (title, user_id)).fetchone():
        raise _conflict("TITLE_TAKEN")


def _apply_role(c, actor: int | None, user_id: int, role: str, *, title: str | None = None, platoon_id: int | None = None,
                allow_admin: bool = False, now=None) -> None:
    """Make ``user_id`` a ``role`` (title/platoon as the role needs), enforcing every structural limit. Shared by
    ``set_role`` (web), ``ops.set_role`` (operator, ``allow_admin=True``) and ``approve_change``. Does not decide WHO may
    do it and does not close change requests (the callers do)."""
    if role not in R.ROLES or (role == "admin" and not allow_admin):
        raise ValidationError("INVALID_INPUT", field="role", detail=f"role {role!r}")
    t = c.execute("SELECT id,role,active FROM users WHERE id=?", (user_id,)).fetchone()
    if t is None:
        raise NotFoundError(detail=f"user {user_id!r}")
    if role != "admin":
        _guard_last_admin(c, user_id)
    if role == "cadet":
        title = None
    elif role == "instructor":
        if title not in R.INSTRUCTOR_TITLES:
            raise ValidationError("TITLE_REQUIRED", field="title", detail="instructor title mm/cmdr required")
        if not _platoon_exists(c, platoon_id):
            raise ValidationError("PLATOON_REQUIRED", field="platoon", detail="an instructor needs an existing platoon")
        if t["active"]:                       # an inactive account is checked when it is reactivated
            _check_slot(c, user_id, platoon_id, title)
    elif role == "chief":
        if title not in R.CHIEF_TITLES:
            raise ValidationError("TITLE_REQUIRED", field="title", detail="chief title mp/smp required")
        if t["active"]:                       # the seat count comes first: with both seats taken the title is taken too
            if c.execute("SELECT count(*) FROM users WHERE role='chief' AND active=1 AND id<>?",
                         (user_id,)).fetchone()[0] >= R.MAX_CHIEFS:
                raise _conflict("CHIEF_LIMIT", max=R.MAX_CHIEFS)
            _check_chief_title(c, user_id, title)
    else:   # admin (operator path only)
        title = None
        if c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1 AND id<>?",
                     (user_id,)).fetchone()[0] >= R.MAX_ADMINS:
            raise _conflict("ADMIN_LIMIT", max=R.MAX_ADMINS)
    c.execute("DELETE FROM instructor_platoons WHERE instructor_id=?", (user_id,))
    # staff and chiefs are not platoon members; a cadet keeps the platoon it already has
    c.execute("UPDATE users SET role=?, title=?, platoon_id=CASE WHEN ?='cadet' THEN platoon_id ELSE NULL END WHERE id=?",
              (role, title, role, user_id))
    if role == "instructor":
        _place_instructor(c, user_id, platoon_id, title, actor, None)


def _apply_title(c, user_id: int, title: str | None) -> None:
    """Change the title of an active instructor or chief, with the same limits as a grant."""
    t = c.execute("SELECT id,role,active FROM users WHERE id=?", (user_id,)).fetchone()
    if t is None:
        raise NotFoundError(detail=f"user {user_id!r}")
    if t["role"] == "chief":
        _check_chief_title(c, user_id, title)
        c.execute("UPDATE users SET title=? WHERE id=?", (title, user_id))
    elif t["role"] == "instructor":
        if title not in R.INSTRUCTOR_TITLES:
            raise ValidationError("TITLE_REQUIRED", field="title", detail="instructor title mm/cmdr required")
        row = c.execute("SELECT platoon_id,is_mm FROM instructor_platoons WHERE instructor_id=?", (user_id,)).fetchone()
        if row is None:
            raise ValidationError("PLATOON_REQUIRED", field="platoon", detail="instructor has no platoon")
        # A מ"כ seat is only newly taken by a מ"מ stepping down: an untitled or titled מ"כ is already counted, so a
        # platoon that is over the limit (migration) still lets its members be titled.
        if t["active"] and (title == "mm" or row["is_mm"]):
            _check_slot(c, user_id, row["platoon_id"], title)
        c.execute("UPDATE users SET title=? WHERE id=?", (title, user_id))
        c.execute("UPDATE instructor_platoons SET is_mm=? WHERE instructor_id=?",
                  (1 if title == "mm" and t["active"] else 0, user_id))
    else:
        raise ValidationError("INVALID_INPUT", field="title", detail="only instructors and chiefs carry a title")


def _set_instructor_platoon(c, actor: int | None, instructor_id: int, platoon_id: int, now=None) -> bool:
    """Move an instructor to ``platoon_id`` (same limits at the destination). Returns False when nothing changed."""
    t = c.execute("SELECT id,role,title,active FROM users WHERE id=?", (instructor_id,)).fetchone()
    if t is None or t["role"] != "instructor":
        raise ValidationError("INVALID_INPUT", detail="only an instructor can be placed in a platoon")
    if not _platoon_exists(c, platoon_id):
        raise ValidationError("PLATOON_REQUIRED", field="platoon", detail="unknown platoon")
    row = c.execute("SELECT platoon_id FROM instructor_platoons WHERE instructor_id=?", (instructor_id,)).fetchone()
    if row is not None and row["platoon_id"] == platoon_id:
        return False
    if t["active"]:
        _check_slot(c, instructor_id, platoon_id, t["title"])
    _place_instructor(c, instructor_id, platoon_id, t["title"], actor, now)
    _close_requests(c, decided_by=actor, now=now, requester=instructor_id)
    return True


def reactivate_checks(c, user_id: int) -> None:
    """Called by ``admin`` BEFORE an account comes back: refuse a clash (MM_TAKEN / TITLE_TAKEN / CHIEF_LIMIT /
    CMDR_LIMIT / ADMIN_LIMIT). ``admin`` then sets ``active=1`` and calls ``_set_active_mm_flag`` to restore ``is_mm``."""
    t = c.execute("SELECT id,role,title FROM users WHERE id=?", (user_id,)).fetchone()
    if t is None:
        raise NotFoundError(detail=f"user {user_id!r}")
    if t["role"] == "chief":
        if t["title"] is not None:
            _check_chief_title(c, user_id, t["title"])
        if c.execute("SELECT count(*) FROM users WHERE role='chief' AND active=1 AND id<>?",
                     (user_id,)).fetchone()[0] >= R.MAX_CHIEFS:
            raise _conflict("CHIEF_LIMIT", max=R.MAX_CHIEFS)
    elif t["role"] == "admin":
        if c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1 AND id<>?",
                     (user_id,)).fetchone()[0] >= R.MAX_ADMINS:
            raise _conflict("ADMIN_LIMIT", max=R.MAX_ADMINS)
    elif t["role"] == "instructor":
        row = c.execute("SELECT platoon_id FROM instructor_platoons WHERE instructor_id=?", (user_id,)).fetchone()
        if row is not None:
            _check_slot(c, user_id, row["platoon_id"], t["title"])


def _set_active_mm_flag(c, user_id: int, active: bool) -> None:
    """Keep ``is_mm`` equal to ``title='mm' AND active`` for one instructor (after the ``active`` flag changed)."""
    c.execute("UPDATE instructor_platoons SET is_mm=CASE WHEN ? AND (SELECT title FROM users WHERE id=?)='mm' THEN 1 ELSE 0 END "
              "WHERE instructor_id=?", (1 if active else 0, user_id, user_id))


# ------------------------------------------------------------------------------------------------ account list
def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_accounts(db_path, actor_id, *, query: str = "", role: str | None = None, platoon_id: int | None = None,
                    limit: int = 50, offset: int = 0, now=None) -> dict[str, Any]:
    """Admin / chief: {"rows": [{id,email,name,gender,is_km,role,title,platoon_id,platoon,active,created_at}], "total"}.
    A chief never sees admin rows. ``query`` (lower-cased, at most 80 characters, LIKE metacharacters escaped) matches
    e-mail or name; newest registration first. Audited as ``people.search`` (no values)."""
    lim = min(MAX_SEARCH, max(1, limit)) if isinstance(limit, int) and not isinstance(limit, bool) else 50
    off = max(0, offset) if isinstance(offset, int) and not isinstance(offset, bool) else 0

    def body(c, a):
        if not isinstance(query, str) or len(query.strip()) > 80:
            raise ValidationError("INVALID_INPUT", field="query", detail="query")
        if role is not None and role not in R.ROLES:
            raise ValidationError("INVALID_INPUT", field="role", detail="role filter")
        if platoon_id is not None and _id(platoon_id) is None:
            raise ValidationError("INVALID_INPUT", field="platoon", detail="platoon filter")
        q = query.strip().lower()
        where, args = ["(? OR u.role<>'admin')"], [1 if a["role"] == "admin" else 0]
        if q:
            where.append("(lower(u.email) LIKE ? ESCAPE '\\' OR lower(u.name) LIKE ? ESCAPE '\\')")
            args += [_like(q), _like(q)]
        if role is not None:
            where.append("u.role=?"); args.append(role)
        if platoon_id is not None:
            where.append("COALESCE(ip.platoon_id,u.platoon_id)=?"); args.append(platoon_id)
        frm = ("FROM users u LEFT JOIN instructor_platoons ip ON ip.instructor_id=u.id "
               "LEFT JOIN platoons p ON p.id=COALESCE(ip.platoon_id,u.platoon_id) WHERE " + " AND ".join(where))
        total = c.execute("SELECT count(*) " + frm, args).fetchone()[0]          # `where` holds literals and ? only
        rows = [dict(r) for r in c.execute(
            "SELECT u.id,u.email,u.name,u.gender,u.is_km,u.role,u.title,COALESCE(ip.platoon_id,u.platoon_id) AS platoon_id,"
            "p.name AS platoon,u.active,u.created_at " + frm + " ORDER BY u.created_at DESC, u.id DESC LIMIT ? OFFSET ?",
            (*args, lim, off))]
        audit.record(c, actor_id=a["id"], action="people.search", now=now)
        return {"rows": rows, "total": total}
    return _run(db_path, actor_id, "people.search", _ADMIN_CHIEF, body, now=now)


def list_platoons(db_path, actor_id, *, now=None) -> list[dict[str, Any]]:
    """Admin / chief: [{id, name, cadets, mm_user_id, cmdrs}] by name. ``cadets`` = active cadets, ``mm_user_id`` = the
    active מ"מ or None, ``cmdrs`` = active instructors who are not the מ"מ (untitled legacy ones included: they count
    toward the limit). Counts only and run on every screen update, so a success is NOT audited."""
    def body(c, a):
        return [dict(r) for r in c.execute(
            "SELECT p.id, p.name, "
            "(SELECT count(*) FROM users u WHERE u.platoon_id=p.id AND u.role='cadet' AND u.active=1) AS cadets, "
            "(SELECT ip.instructor_id FROM instructor_platoons ip JOIN users u ON u.id=ip.instructor_id "
            " WHERE ip.platoon_id=p.id AND ip.is_mm=1 AND u.active=1) AS mm_user_id, "
            "(SELECT count(*) FROM instructor_platoons ip JOIN users u ON u.id=ip.instructor_id "
            " WHERE ip.platoon_id=p.id AND ip.is_mm=0 AND u.active=1 AND u.role='instructor') AS cmdrs "
            "FROM platoons p ORDER BY p.name, p.id")]
    return _run(db_path, actor_id, "people.platoons", _ADMIN_CHIEF, body, now=now)


# ----------------------------------------------------------------------------------------------- roles and titles
def _expected_ok(t, expected_name, expected_created_at) -> None:
    if not isinstance(expected_name, str) or not isinstance(expected_created_at, str):
        raise ValidationError("INVALID_INPUT", field="expected", detail="the grant needs the verified name and registration time")
    if t["name"] != expected_name or t["created_at"] != expected_created_at:
        raise _conflict("ACCOUNT_CHANGED")


def set_role(db_path, actor_id, user_id, role, *, title=None, platoon_id=None, expected_name=None,
             expected_created_at=None, now=None) -> None:
    """Admin / chief: change the role of an ACTIVE account (see ``baror.domain.roles.may_change``: instructor <-> cadet
    for admin and chief; anything with ``chief`` admin only; nobody grants ``admin``; nobody changes their own role;
    admin rows are not touched). A grant to ``instructor`` / ``chief`` needs ``title`` (+ ``platoon_id`` for an
    instructor) and the identity check ``expected_name`` / ``expected_created_at`` (ACCOUNT_CHANGED when the account no
    longer matches what was verified). Side effects, same transaction: title cleared with the role, cadet platoon cleared,
    pending change requests about the user (and, when they stop being an instructor, made by them) rejected.
    Audit: ``role.to_cadet|to_instructor|to_chief``."""
    def body(c, a):
        if role not in R.ROLES:
            raise ValidationError("INVALID_INPUT", field="role", detail=f"role {role!r}")
        t = c.execute("SELECT id,role,active,name,created_at FROM users WHERE id=?", (_id(user_id),)).fetchone() \
            if _id(user_id) else None
        if t is None or not t["active"] or (t["role"] == "admin" and a["role"] != "admin"):
            raise NotFoundError(detail="account not visible")
        if t["id"] == a["id"]:
            raise _no_role("nobody changes their own role")
        if t["role"] == "admin" or role == "admin" or not R.may_change(a["role"], t["role"], role):
            raise _no_role(f"{a['role']} may not change {t['role']} to {role}")
        if role in ("instructor", "chief"):
            _expected_ok(t, expected_name, expected_created_at)
        _apply_role(c, a["id"], t["id"], role, title=title, platoon_id=platoon_id, now=now)
        _close_requests(c, decided_by=a["id"], now=now, target=t["id"],
                        requester=t["id"] if t["role"] == "instructor" else None)
        audit.record(c, actor_id=a["id"], action=f"role.to_{role}", target_user_id=t["id"],
                     platoon_id=platoon_id if role == "instructor" else None, now=now)
    _run(db_path, actor_id, "role.change", _ADMIN_CHIEF, body, now=now, target=user_id)


def set_title(db_path, actor_id, user_id, title, *, now=None) -> None:
    """Admin: set the title of an active chief or instructor. Chief: of an instructor only. Same limits as a grant
    (TITLE_TAKEN, MM_TAKEN, CMDR_LIMIT); never one's own. Audit ``title.set``."""
    def body(c, a):
        t = c.execute("SELECT id,role,active FROM users WHERE id=?", (_id(user_id),)).fetchone() if _id(user_id) else None
        if t is None or not t["active"] or t["role"] not in ("instructor", "chief"):
            raise NotFoundError(detail="account not visible")
        if t["id"] == a["id"] or (a["role"] == "chief" and t["role"] != "instructor"):
            raise _no_role("title change refused")
        _apply_title(c, t["id"], title)
        audit.record(c, actor_id=a["id"], action="title.set", target_user_id=t["id"], now=now)
    _run(db_path, actor_id, "title.set", _ADMIN_CHIEF, body, now=now, target=user_id)


def move_instructor(db_path, actor_id, instructor_id, platoon_id, *, now=None) -> bool:
    """Admin / chief: put an active instructor into another platoon (a מ"מ stays מ"מ if the destination has none; the
    destination's limits apply). Returns False when the instructor is already there. Pending requests made by the
    instructor are rejected (they referred to the old platoon). Audit ``instructor.move``."""
    def body(c, a):
        t = c.execute("SELECT id,role,active FROM users WHERE id=?", (_id(instructor_id),)).fetchone() \
            if _id(instructor_id) else None
        if t is None or not t["active"] or t["role"] != "instructor":
            raise NotFoundError(detail="account not visible")
        if not _platoon_exists(c, platoon_id):
            raise ValidationError("PLATOON_REQUIRED", field="platoon", detail="unknown platoon")
        moved = _set_instructor_platoon(c, a["id"], t["id"], platoon_id, now)
        if moved:
            audit.record(c, actor_id=a["id"], action="instructor.move", target_user_id=t["id"], platoon_id=platoon_id, now=now)
        return moved
    return _run(db_path, actor_id, "instructor.move", _ADMIN_CHIEF, body, now=now, target=instructor_id, platoon=platoon_id)


# --------------------------------------------------------------------------------------------------- cadets
def _own_platoon(c, a) -> int | None:
    row = c.execute("SELECT platoon_id FROM instructor_platoons WHERE instructor_id=?", (a["id"],)).fetchone()
    return row["platoon_id"] if row else None


def _cadet(c, a, cadet_id, *, any_platoon: bool):
    """The ACTIVE cadet the actor may deal with: any for admin/chief, only the own platoon for an instructor."""
    if _id(cadet_id) is None:
        raise _not_visible("cadet")
    t = c.execute("SELECT id,name,gender,is_km,platoon_id FROM users WHERE id=? AND role='cadet' AND active=1",
                  (cadet_id,)).fetchone()
    if t is None or (not any_platoon and (t["platoon_id"] is None or t["platoon_id"] != _own_platoon(c, a))):
        raise _not_visible("cadet")
    return t


def _request(c, a, kind: str, target: int, platoon_id: int | None, *, now=None, new_name=None, new_gender=None,
             new_is_km=None, new_platoon_id=None) -> int:
    """Create a pending request, or return the id of the identical open one. Limit: MAX_PENDING_PER_REQUESTER."""
    dup = c.execute("SELECT id FROM cadet_change_requests WHERE kind=? AND target_user_id=? AND requested_by=? "
                    "AND status='pending'", (kind, target, a["id"])).fetchone()
    if dup is not None:
        return dup["id"]
    limit, label = (MAX_PENDING_NOMINATIONS, "nominations") if kind == "nominate_cmdr" else (MAX_PENDING_PER_REQUESTER, "requests")
    if c.execute("SELECT count(*) FROM cadet_change_requests WHERE requested_by=? AND status='pending'"
                 + (" AND kind='nominate_cmdr'" if kind == "nominate_cmdr" else ""), (a["id"],)).fetchone()[0] >= limit:
        raise ValidationError("REQUEST_LIMIT", params={"max": limit}, detail=f"too many pending {label}")
    rid = int(c.execute(
        "INSERT INTO cadet_change_requests(kind,requested_by,requested_at,target_user_id,platoon_id,new_name,new_gender,"
        "new_is_km,new_platoon_id) VALUES(?,?,?,?,?,?,?,?,?)",
        (kind, a["id"], audit.ts(now), target, platoon_id, new_name, new_gender, new_is_km, new_platoon_id)).lastrowid)
    audit.record(c, actor_id=a["id"], action="request.create", target_user_id=target, platoon_id=platoon_id, now=now)
    return rid


def _destination(c, a, platoon_id) -> int:
    """The platoon a cadet may be added to by this actor: any existing one for admin/chief, only the own for an instructor."""
    if not _platoon_exists(c, platoon_id) or (a["role"] == "instructor" and platoon_id != _own_platoon(c, a)):
        raise _not_visible("platoon")
    return platoon_id


def move_cadet(db_path, actor_id, cadet_id, platoon_id, *, now=None) -> dict[str, Any]:
    """Admin / chief: put an active cadet into a platoon, or (``platoon_id=None``) take them out of any platoon.
    Pending requests about the cadet are rejected. {"status": "moved"|"unchanged"}. Audit ``cadet.move``."""
    def body(c, a):
        t = _cadet(c, a, cadet_id, any_platoon=True)
        if platoon_id is not None and not _platoon_exists(c, platoon_id):
            raise _not_visible("platoon")
        if t["platoon_id"] == platoon_id:
            return {"status": "unchanged"}
        c.execute("UPDATE users SET platoon_id=? WHERE id=?", (platoon_id, t["id"]))
        _close_requests(c, decided_by=a["id"], now=now, target=t["id"])
        audit.record(c, actor_id=a["id"], action="cadet.move", target_user_id=t["id"], platoon_id=platoon_id, now=now)
        return {"status": "moved"}
    return _run(db_path, actor_id, "cadet.move", _ADMIN_CHIEF, body, now=now, target=cadet_id, platoon=platoon_id)


def add_cadet(db_path, actor_id, email, platoon_id, *, now=None) -> dict[str, Any]:
    """Put an ALREADY REGISTERED, active cadet (found by e-mail) into ``platoon_id``. Admin / chief: any platoon, and a
    cadet of another platoon is moved. Instructor: own platoon only; a cadet of another platoon becomes a request
    (``add_conflict``) for the chief or admin. Unknown / staff / inactive e-mail -> the same NotFoundError
    (CADET_NOT_REGISTERED). {"status": "assigned"|"unchanged"|"moved"|"requested", "request_id": int|None}."""
    def body(c, a):
        e = repo.normalize_email(email)
        pid = _destination(c, a, platoon_id)
        t = c.execute("SELECT id,platoon_id FROM users WHERE email=? AND role='cadet' AND active=1", (e,)).fetchone()
        if t is None:
            raise NotFoundError("CADET_NOT_REGISTERED", detail="no active cadet with that e-mail")
        if t["platoon_id"] == pid:
            return {"status": "unchanged", "request_id": None}
        if t["platoon_id"] is None or a["role"] != "instructor":
            c.execute("UPDATE users SET platoon_id=? WHERE id=?", (pid, t["id"]))
            _close_requests(c, decided_by=a["id"], now=now, target=t["id"])
            audit.record(c, actor_id=a["id"], action="cadet.move", target_user_id=t["id"], platoon_id=pid, now=now)
            return {"status": "assigned" if t["platoon_id"] is None else "moved", "request_id": None}
        return {"status": "requested", "request_id": _request(c, a, "add_conflict", t["id"], pid, now=now)}
    return _run(db_path, actor_id, "cadet.add", ("instructor",) + _ADMIN_CHIEF, body, now=now, platoon=platoon_id)


def edit_cadet(db_path, actor_id, cadet_id, changes: dict, *, now=None) -> dict[str, Any]:
    """Edit name, gender, is_km and/or platoon_id of an active cadet (NOTHING else, never results). Admin / chief apply
    it at once; an instructor (own platoon's cadets only) files a request. Returns {"status": "applied"|"requested",
    "request_id": int|None}. Audit ``cadet.edit`` / ``request.create``."""
    def body(c, a):
        t = _cadet(c, a, cadet_id, any_platoon=a["role"] != "instructor")
        if not isinstance(changes, dict) or not changes or set(changes) - set(_EDITABLE):
            raise ValidationError("INVALID_INPUT", field="changes", detail="changes must name name/gender/is_km/platoon_id")
        v: dict[str, Any] = {}
        if "name" in changes:
            v["name"] = repo.check_display_name(changes["name"])
        if "gender" in changes:
            if changes["gender"] not in repo.GENDERS:
                raise ValidationError("INVALID_GENDER", field="gender", detail="gender")
            v["gender"] = changes["gender"]
        if "is_km" in changes:
            if not isinstance(changes["is_km"], (bool, int)) or changes["is_km"] not in (0, 1):
                raise ValidationError("INVALID_INPUT", field="is_km", detail="is_km")
            v["is_km"] = int(changes["is_km"])
        if "platoon_id" in changes:
            if not _platoon_exists(c, changes["platoon_id"]):
                raise ValidationError("PLATOON_REQUIRED", field="platoon", detail="unknown platoon")
            v["platoon_id"] = changes["platoon_id"]
        if a["role"] == "instructor":
            rid = _request(c, a, "edit", t["id"], t["platoon_id"], now=now, new_name=v.get("name"),
                           new_gender=v.get("gender"), new_is_km=v.get("is_km"), new_platoon_id=v.get("platoon_id"))
            return {"status": "requested", "request_id": rid}
        _write_edit(c, t["id"], v)
        if "platoon_id" in v:
            _close_requests(c, decided_by=a["id"], now=now, target=t["id"])
        audit.record(c, actor_id=a["id"], action="cadet.edit", target_user_id=t["id"],
                     platoon_id=v.get("platoon_id", t["platoon_id"]), now=now)
        return {"status": "applied", "request_id": None}
    return _run(db_path, actor_id, "cadet.edit", ("instructor",) + _ADMIN_CHIEF, body, now=now, target=cadet_id)


def _write_edit(c, cadet: int, v: dict[str, Any]) -> None:
    cols = {"name": "name", "gender": "gender", "is_km": "is_km", "platoon_id": "platoon_id"}      # literals, never input
    sets = [f"{cols[k]}=?" for k in v]
    c.execute("UPDATE users SET " + ", ".join(sets) + " WHERE id=?", (*v.values(), cadet))


def delete_cadet(db_path, actor_id, cadet_id, *, now=None) -> dict[str, Any]:
    """'Delete' a cadet = REMOVE FROM THE PLATOON: platoon_id becomes NULL; the account, sessions, results and
    accommodations stay. Admin / chief do it at once (pending requests about the cadet are closed: ``delete`` ->
    approved, the others -> rejected); an instructor files a request for a cadet of the own platoon. Returns
    {"status": "applied"|"requested"|"unchanged", "request_id": int|None}. Audit ``cadet.remove`` / ``request.create``."""
    def body(c, a):
        t = _cadet(c, a, cadet_id, any_platoon=a["role"] != "instructor")
        if a["role"] == "instructor":
            return {"status": "requested", "request_id": _request(c, a, "delete", t["id"], t["platoon_id"], now=now)}
        if t["platoon_id"] is None:
            return {"status": "unchanged", "request_id": None}
        c.execute("UPDATE users SET platoon_id=NULL WHERE id=?", (t["id"],))
        _close_requests(c, decided_by=a["id"], now=now, target=t["id"], delete_is_approved=True)
        audit.record(c, actor_id=a["id"], action="cadet.remove", target_user_id=t["id"], platoon_id=t["platoon_id"], now=now)
        return {"status": "applied", "request_id": None}
    return _run(db_path, actor_id, "cadet.remove", ("instructor",) + _ADMIN_CHIEF, body, now=now, target=cadet_id)


def nominate_cmdr(db_path, actor_id, email, *, now=None) -> dict[str, Any]:
    """A מ"מ (active instructor with title ``mm``) proposes a registered, active cadet as מ"כ of HIS OWN platoon (read
    from the database). Becomes a ``nominate_cmdr`` request; an admin or chief decides. Unknown / staff / inactive
    e-mail -> NotFoundError (CADET_NOT_REGISTERED). At most 5 open nominations per requester; the identical open one
    returns its id. {"status": "requested", "request_id": int}. Audit ``request.create``."""
    def body(c, a):
        if a["title"] != "mm" or _own_platoon(c, a) is None:
            raise _no_role("only a platoon commander (mm) may nominate")
        e = repo.normalize_email(email)
        t = c.execute("SELECT id FROM users WHERE email=? AND role='cadet' AND active=1", (e,)).fetchone()
        if t is None:
            raise NotFoundError("CADET_NOT_REGISTERED", detail="no active cadet with that e-mail")
        return {"status": "requested", "request_id": _request(c, a, "nominate_cmdr", t["id"], _own_platoon(c, a), now=now)}
    return _run(db_path, actor_id, "request.nominate", ("instructor",), body, now=now)


# ------------------------------------------------------------------------------------------------- requests
_REQ_SELECT = (
    "SELECT r.id, r.kind, r.status, r.requested_at, r.decided_at, r.requested_by, rb.name AS requester_name, "
    "r.target_user_id, t.name AS target_name, r.platoon_id, p.name AS platoon, t.platoon_id AS target_platoon_id, "
    "tp.name AS target_platoon, t.gender AS target_gender, t.is_km AS target_is_km, rb.title AS requester_title, "
    "r.new_name, r.new_gender, r.new_is_km, r.new_platoon_id, np.name AS new_platoon, "
    "CASE WHEN r.kind='nominate_cmdr' AND :detail THEN t.email END AS target_email, "
    "CASE WHEN r.kind='nominate_cmdr' AND :detail THEN t.created_at END AS target_created_at "
    "FROM cadet_change_requests r JOIN users t ON t.id=r.target_user_id LEFT JOIN users rb ON rb.id=r.requested_by "
    "LEFT JOIN platoons p ON p.id=r.platoon_id LEFT JOIN platoons tp ON tp.id=t.platoon_id "
    "LEFT JOIN platoons np ON np.id=r.new_platoon_id ")


def pending_changes(db_path, actor_id, *, limit: int = 100, now=None) -> list[dict[str, Any]]:
    """Admin / chief: the open requests, oldest first: id, kind, status, requested_at, requested_by, requester_name,
    target_user_id, target_name, platoon_id/platoon (where the cadet is to be, or is), target_platoon(_id) (where the
    cadet is now), target_gender / target_is_km (what the cadet is now, for the from -> to line of an edit),
    requester_title (``mm`` / ``cmdr`` / None), new_* (edit), and for ``nominate_cmdr`` only: target_email and target_created_at (what the grant
    dialog shows). A chief never sees a request about an admin. Not audited on success (list refreshed on every screen
    update); a refusal is."""
    lim = min(MAX_SEARCH, max(1, limit)) if isinstance(limit, int) and not isinstance(limit, bool) else 100

    def body(c, a):
        return [dict(r) for r in c.execute(
            _REQ_SELECT + "WHERE r.status='pending' AND (:admin OR t.role<>'admin') ORDER BY r.requested_at, r.id LIMIT :lim",
            {"detail": 1, "admin": 1 if a["role"] == "admin" else 0, "lim": lim})]
    return _run(db_path, actor_id, "request.list", _ADMIN_CHIEF, body, now=now)


def my_changes(db_path, actor_id, *, limit: int = 50, now=None) -> list[dict[str, Any]]:
    """Instructor: the requests this instructor made (every status), newest first, same keys as ``pending_changes``
    without the e-mail and registration time. Not audited on success."""
    lim = min(MAX_SEARCH, max(1, limit)) if isinstance(limit, int) and not isinstance(limit, bool) else 50

    def body(c, a):
        return [dict(r) for r in c.execute(
            _REQ_SELECT + "WHERE r.requested_by=:me ORDER BY r.requested_at DESC, r.id DESC LIMIT :lim",
            {"detail": 0, "me": a["id"], "lim": lim})]
    return _run(db_path, actor_id, "request.mine", ("instructor",), body, now=now)


def _decide(c, rid: int, status: str, actor: int, now) -> None:
    c.execute("UPDATE cadet_change_requests SET status=?, decided_by=?, decided_at=? WHERE id=?",
              (status, actor, audit.ts(now), rid))


def approve_change(db_path, actor_id, request_id, *, expected_name=None, expected_created_at=None, now=None) -> dict[str, Any]:
    """Admin / chief: apply a pending request after re-validating it in the same transaction. A request that is not
    pending (or is about a cadet who is no longer an active cadet) -> NotFoundError; ``reject_change`` still clears it.
    ``nominate_cmdr`` additionally needs ``expected_name`` / ``expected_created_at`` (checked like ``set_role``), the
    nominating מ"מ still being an active מ"מ of that platoon, and passes through the cap (CMDR_LIMIT). Other requests
    pending for the same cadet are closed when the approval changes their platoon or role.
    Audit ``request.approve`` (+ ``role.to_instructor``). Returns {"status": "approved", "kind": kind}."""
    def body(c, a):
        r = c.execute("SELECT * FROM cadet_change_requests WHERE id=? AND status='pending'", (_id(request_id),)).fetchone() \
            if _id(request_id) else None
        if r is None:
            raise _not_visible("request")
        t = c.execute("SELECT id,name,created_at,platoon_id FROM users WHERE id=? AND role='cadet' AND active=1",
                      (r["target_user_id"],)).fetchone()
        if t is None:
            raise _not_visible("request")
        kind = r["kind"]
        if kind == "add_conflict":
            if not _platoon_exists(c, r["platoon_id"]):
                raise _not_visible("platoon")
            c.execute("UPDATE users SET platoon_id=? WHERE id=?", (r["platoon_id"], t["id"]))
        elif kind == "edit":
            v = {k[4:]: r[k] for k in ("new_name", "new_gender", "new_is_km", "new_platoon_id") if r[k] is not None}
            if "platoon_id" in v and not _platoon_exists(c, v["platoon_id"]):
                raise _not_visible("platoon")
            _write_edit(c, t["id"], v)
        elif kind == "delete":
            c.execute("UPDATE users SET platoon_id=NULL WHERE id=?", (t["id"],))
        else:   # nominate_cmdr
            _expected_ok(t, expected_name, expected_created_at)
            nom = c.execute("SELECT u.id FROM users u JOIN instructor_platoons ip ON ip.instructor_id=u.id "
                            "WHERE u.id=? AND u.role='instructor' AND u.title='mm' AND u.active=1 AND ip.platoon_id=?",
                            (r["requested_by"], r["platoon_id"])).fetchone() if r["requested_by"] else None
            if nom is None or not _platoon_exists(c, r["platoon_id"]):
                raise _not_visible("request")
            _apply_role(c, a["id"], t["id"], "instructor", title="cmdr", platoon_id=r["platoon_id"], now=now)
            audit.record(c, actor_id=a["id"], action="role.to_instructor", target_user_id=t["id"],
                         platoon_id=r["platoon_id"], now=now)
        if kind != "edit" or r["new_platoon_id"] is not None:
            _close_requests(c, decided_by=a["id"], now=now, target=t["id"], exclude=r["id"])
        _decide(c, r["id"], "approved", a["id"], now)
        audit.record(c, actor_id=a["id"], action="request.approve", target_user_id=t["id"],
                     platoon_id=r["platoon_id"], now=now)
        return {"status": "approved", "kind": kind}
    return _run(db_path, actor_id, "request.approve", _ADMIN_CHIEF, body, now=now)


def reject_change(db_path, actor_id, request_id, *, now=None) -> dict[str, Any]:
    """Admin / chief: reject a pending request; nothing else changes. A request that is not pending -> NotFoundError.
    Audit ``request.reject``."""
    def body(c, a):
        r = c.execute("SELECT id,target_user_id,platoon_id FROM cadet_change_requests WHERE id=? AND status='pending'",
                      (_id(request_id),)).fetchone() if _id(request_id) else None
        if r is None:
            raise _not_visible("request")
        _decide(c, r["id"], "rejected", a["id"], now)
        audit.record(c, actor_id=a["id"], action="request.reject", target_user_id=r["target_user_id"],
                     platoon_id=r["platoon_id"], now=now)
        return {"status": "rejected"}
    return _run(db_path, actor_id, "request.reject", _ADMIN_CHIEF, body, now=now)


# ------------------------------------------------------------------------------------------------ operator-ish
def release_email(db_path, actor_id, user_id, *, now=None) -> None:
    """Admin only: free the e-mail of a CADET account (typed wrongly, or the person must register again): the address
    becomes ``released-<8 hex>@released.invalid``, the account is deactivated and leaves its platoon, its sessions are
    revoked, pending requests about it are closed. Results, accommodations and the pseudonym stay. Audit ``people.release``."""
    def body(c, a):
        t = c.execute("SELECT id,role,email FROM users WHERE id=?", (_id(user_id),)).fetchone() if _id(user_id) else None
        if t is None or t["email"].endswith("@released.invalid"):
            raise NotFoundError(detail="account not visible")
        if t["role"] != "cadet":
            raise _no_role("only a cadet e-mail can be released")
        while True:
            new = f"released-{secrets.token_hex(4)}@released.invalid"
            if c.execute("SELECT 1 FROM users WHERE email=?", (new,)).fetchone() is None:
                break
        c.execute("UPDATE users SET email=?, active=0, platoon_id=NULL WHERE id=?", (new, t["id"]))
        c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (t["id"],))
        _close_requests(c, decided_by=a["id"], now=now, target=t["id"])
        audit.record(c, actor_id=a["id"], action="people.release", target_user_id=t["id"], now=now)
    _run(db_path, actor_id, "people.release", ("admin",), body, now=now, target=user_id)


# ----------------------------------------------------------------------------------------------- join links
MAX_LINK_DAYS = 30
MIN_SECRET_CHARS = 32


def _link_platoon(c, a, platoon_id) -> int:
    if not _platoon_exists(c, platoon_id) or (a["role"] == "instructor" and platoon_id != _own_platoon(c, a)):
        raise _not_visible("platoon")
    return platoon_id


def join_link(db_path, actor_id, platoon_id, *, secret, days: int = 14, now: dt.datetime | None = None) -> str:
    """The signed token ``<platoon>.<epoch>.<expiry>.<mac>`` for ``?join=`` (it only pre-selects the platoon on the
    registration form and grants nothing). Admin / chief: any platoon; instructor: the own platoon. ``days`` 1..30;
    ``secret`` at least 32 characters (otherwise the feature is off: ValidationError). Audit ``join.create``."""
    def body(c, a):
        pid = _link_platoon(c, a, platoon_id)
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_LINK_DAYS:
            raise ValidationError("INVALID_INPUT", field="days", detail="days 1..30")
        if not isinstance(secret, str) or len(secret) < MIN_SECRET_CHARS:
            raise ValidationError("INVALID_INPUT", field="secret", detail="join links are off (secret too short)")
        from baror.domain import joinlink                 # pure helper of the registration workstream
        epoch = c.execute("SELECT join_epoch FROM platoons WHERE id=?", (pid,)).fetchone()[0]
        t0 = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        token = joinlink.sign(secret, pid, epoch, int((t0 + dt.timedelta(days=days)).timestamp()))
        audit.record(c, actor_id=a["id"], action="join.create", platoon_id=pid, now=now)
        return token
    return _run(db_path, actor_id, "join.create", ("instructor",) + _ADMIN_CHIEF, body, now=now, platoon=platoon_id)


def rotate_join_links(db_path, actor_id, platoon_id, *, now=None) -> int:
    """Revoke every join link of a platoon (bumps ``platoons.join_epoch``); returns the new epoch. Same actors as
    ``join_link``. Audit ``join.rotate``."""
    def body(c, a):
        pid = _link_platoon(c, a, platoon_id)
        c.execute("UPDATE platoons SET join_epoch=join_epoch+1 WHERE id=?", (pid,))
        audit.record(c, actor_id=a["id"], action="join.rotate", platoon_id=pid, now=now)
        return c.execute("SELECT join_epoch FROM platoons WHERE id=?", (pid,)).fetchone()[0]
    return _run(db_path, actor_id, "join.rotate", ("instructor",) + _ADMIN_CHIEF, body, now=now, platoon=platoon_id)
