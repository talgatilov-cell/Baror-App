"""User-scoped repository. Stdlib only; never imports streamlit.

Rules enforced here (tested in tests/test_repo.py):
* There is NO function that returns another user's results or lists all users.
* Every statement that touches ``results`` is scoped to the caller's pseudonym; update/delete carry BOTH ``id = ?`` and
  the pseudonym so a guessed result id cannot reach another user's row (no IDOR).
* SQL is always parameterized. Identifiers are never built from input.
* Emails are normalised (strip + lower) in ONE place (``normalize_email``); the schema CHECK is the backstop.
* Callers receive plain dicts; ``pw_hash`` is only returned by ``get_login_record``.
* Schema v2: web registration goes through ``register_with_allowlist`` ONLY. ``create_user`` is the trusted/internal
  path (JSON migration, operator tooling, tests) and does not consult the allowlist.
* Schema v3 (ADR-0002): results hang off a random pseudonym. Callers still pass ``user_id``; every results statement
  resolves it to that same user's pseudonym with ``PSEUDONYM_OF_USER``, so isolation is exactly as before.
* Cohort (instructor) reads live in ``baror.storage.cohort``; this module still has no function that returns another
  user's results.
"""
from __future__ import annotations

import datetime as _dt
import secrets
import unicodedata
from typing import Any

from baror.errors import ConflictError, NotFoundError, RegistrationDeniedError, ValidationError
from baror.storage import accommodations, audit, db

GENDERS = ("M", "F")
# The pseudonym of the user whose id is the (single) bound parameter. Never built from input.
PSEUDONYM_OF_USER = "(SELECT pseudonym FROM users WHERE id = ?)"


def new_pseudonym() -> str:
    return secrets.token_hex(16)   # 128 random bits, 32 hex chars (the schema CHECK expects exactly that)


def normalize_email(email: str) -> str:
    if not isinstance(email, str):
        raise ValidationError("INVALID_EMAIL", field="email", detail="email is not a string")
    e = email.strip().lower()
    if len(e) > 254 or "@" not in e[1:] or any(c.isspace() for c in e):
        raise ValidationError("INVALID_EMAIL", field="email", detail="bad email shape")
    return e


def _check_date(value: str) -> str:
    # The schema also checks this; validating here gives a field-level error instead of a generic CHECK failure.
    try:
        d = _dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError("INVALID_DATE", field="test_date", detail=f"bad date {value!r}") from None
    if d.isoformat() != value:
        raise ValidationError("INVALID_DATE", field="test_date", detail=f"non-canonical date {value!r}")
    return value


def _check_measurements(run_tenths: int | None, pushups: int | None) -> None:
    for name, v, lo, hi in (("run_tenths", run_tenths, 1, 72000), ("pushups", pushups, 0, 300)):
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValidationError("INVALID_RESULT", field=name, detail=f"{name}={v!r} outside {lo}..{hi}")
    if run_tenths is None and pushups is None:
        raise ValidationError("EMPTY_RESULT", detail="at least one measurement is required")


# ---------------------------------------------------------------- users
def _check_profile(name: str, gender: str) -> None:
    if gender not in GENDERS:
        raise ValidationError("INVALID_GENDER", field="gender", detail=f"gender={gender!r}")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValidationError("INVALID_NAME", field="name", detail="name length")


NAME_SEPARATORS = " -.'\u2019\u05f3\u05f4"     # space, hyphen, dot, apostrophes, geresh, gershayim


def _name_letter(ch: str) -> bool:
    """A Hebrew or Latin letter (accented Latin included). Digits, other scripts and symbols are not."""
    if not ch.isalpha():
        return False
    unicode_name = unicodedata.name(ch, "")
    return unicode_name.startswith(("HEBREW LETTER", "LATIN "))


def check_display_name(name) -> str:
    """The name a person types (registration, profile): Hebrew and Latin letters plus space, hyphen, dot, apostrophe,
    geresh and gershayim, 1..80 characters, at least one letter. No digits, no markup characters (< > [ ] * : & ...), no
    line breaks, control or direction-override characters. Returns the trimmed name; raises ValidationError("INVALID_NAME").
    The trusted paths (``create_user``: migration, operator tooling) keep the length check only, so a legacy record is
    never refused here; the screens show every name as plain text anyway."""
    if not isinstance(name, str):
        raise ValidationError("INVALID_NAME", field="name", detail="name is not text")
    n = name.strip()
    if not 1 <= len(n) <= 80 or not any(_name_letter(ch) for ch in n) or \
            any(not (_name_letter(ch) or ch in NAME_SEPARATORS) for ch in n):
        raise ValidationError("INVALID_NAME", field="name", detail="name: Hebrew/Latin letters and . - ' space only")
    return n


def create_user(db_path, *, email: str, name: str, gender: str, is_km: bool, pw_hash: str,
                must_change_pw: bool = False, role: str = "cadet", platoon_id: int | None = None) -> int:
    """TRUSTED path: INSERT without consulting the allowlist (JSON migration, operator tooling, tests).
    Web registration must use ``register_with_allowlist``. Uniqueness is enforced by the database, so two concurrent
    registrations of the same e-mail cannot both succeed (closes BR-03c). Raises ConflictError if the e-mail exists."""
    e = normalize_email(email)
    _check_profile(name, gender)
    with db.transaction(db_path) as c:
        # sqlite3.IntegrityError (UNIQUE) is translated to ConflictError by db.transaction
        pseudonym = new_pseudonym()
        c.execute("INSERT INTO pseudonyms(pseudonym) VALUES(?)", (pseudonym,))
        cur = c.execute(
            "INSERT INTO users(email,name,gender,is_km,pw_hash,must_change_pw,role,platoon_id,pseudonym) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (e, name.strip(), gender, int(bool(is_km)), pw_hash, int(bool(must_change_pw)), role, platoon_id, pseudonym))
        return int(cur.lastrowid)


def register_with_allowlist(db_path, *, email: str, name: str, gender: str, is_km: bool, pw_hash: str,
                            must_change_pw: bool = False, now=None) -> int:
    """Self-registration: allowed only for an allowlisted, not-yet-used e-mail. Returns the new user id.

    ONE write transaction (BEGIN IMMEDIATE) does check-listed, check-no-account, INSERT user (role and platoon taken
    from the allowlist entry) and consume the entry. Concurrent attempts for the same e-mail are serialised by the
    write lock; exactly one wins, the others see the entry already consumed. UNIQUE(users.email) is the backstop.

    Raises ``RegistrationDeniedError`` -- the SAME error and message for: e-mail not listed, entry already used,
    account already exists, malformed e-mail. The reason is only in ``detail`` (server log). Name/gender problems
    raise their own field errors BEFORE the allowlist is consulted (they do not depend on list membership).
    Timing: every failure path performs the same single lookup under the same lock; the caller (service.register)
    hashes the password first on every path, so the scrypt cost dominates and is identical.
    """
    _check_profile(name, gender)
    name = check_display_name(name)            # before the allowlist is consulted: independent of list membership
    try:
        e = normalize_email(email)
    except ValidationError:
        raise RegistrationDeniedError(detail="invalid_email") from None
    reason = None
    try:
        with db.transaction(db_path) as c:
            ent = c.execute("SELECT role,platoon_id,used_at FROM allowed_emails WHERE email=?", (e,)).fetchone()
            if ent is None:
                reason = "not_listed"
            elif ent["used_at"] is not None:
                reason = "entry_already_used"
            elif c.execute("SELECT 1 FROM users WHERE email=?", (e,)).fetchone():
                reason = "account_exists"
            if reason is None:
                pseudonym = new_pseudonym()
                c.execute("INSERT INTO pseudonyms(pseudonym) VALUES(?)", (pseudonym,))
                uid = int(c.execute(
                    "INSERT INTO users(email,name,gender,is_km,pw_hash,must_change_pw,role,platoon_id,pseudonym) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (e, name.strip(), gender, int(bool(is_km)), pw_hash, int(bool(must_change_pw)),
                     ent["role"], ent["platoon_id"], pseudonym)).lastrowid)
                won = c.execute("UPDATE allowed_emails SET used_by=?, used_at=? WHERE email=? AND used_at IS NULL",
                                (uid, audit.ts(now), e)).rowcount
                if won != 1:  # cannot happen under the write lock; fail closed (rolls the INSERT back)
                    raise RegistrationDeniedError(detail="lost_race")
                audit.record(c, actor_id=uid, action="auth.register", target_user_id=uid, now=now)
                return uid
    except ConflictError:
        raise RegistrationDeniedError(detail="account_exists") from None
    raise RegistrationDeniedError(detail=reason)


def get_login_record(db_path, email: str) -> dict[str, Any] | None:
    """Only function that exposes pw_hash. Returns None for unknown or malformed e-mail."""
    try:
        e = normalize_email(email)
    except ValidationError:
        return None
    with db.reader(db_path) as c:
        row = c.execute("SELECT id,pw_hash,must_change_pw,failed_logins,locked_until,active FROM users WHERE email=?",
                        (e,)).fetchone()
    return dict(row) if row else None


def get_user(db_path, user_id: int) -> dict[str, Any] | None:
    with db.reader(db_path) as c:
        row = c.execute("SELECT id,email,name,gender,is_km,must_change_pw,role,created_at,platoon_id,active FROM users WHERE id=?",
                        (user_id,)).fetchone()
    return dict(row) if row else None


def update_profile(db_path, user_id: int, *, name: str | None = None, gender: str | None = None,
                   is_km: bool | None = None) -> None:
    sets, args = [], []
    if name is not None:
        sets.append("name=?"); args.append(check_display_name(name))
    if gender is not None:
        if gender not in GENDERS:
            raise ValidationError("INVALID_GENDER", field="gender", detail=f"gender={gender!r}")
        sets.append("gender=?"); args.append(gender)
    if is_km is not None:
        sets.append("is_km=?"); args.append(int(bool(is_km)))
    if not sets:
        return
    with db.transaction(db_path) as c:
        cur = c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*args, user_id))  # column names are literals above
        if cur.rowcount == 0:
            raise NotFoundError(detail=f"user {user_id}")


def set_password_hash(db_path, user_id: int, pw_hash: str, *, must_change_pw: bool = False,
                      revoke_sessions: bool = True) -> None:
    with db.transaction(db_path) as c:
        cur = c.execute("UPDATE users SET pw_hash=?, must_change_pw=?, failed_logins=0, locked_until=NULL WHERE id=?",
                        (pw_hash, int(must_change_pw), user_id))
        if cur.rowcount == 0:
            raise NotFoundError(detail=f"user {user_id}")
        if revoke_sessions:  # a password change invalidates every existing session token of that user
            c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,))  # (not for transparent rehash)


def delete_user(db_path, user_id: int) -> None:
    """ERASURE at the user's request: removes the account, its sessions, its accommodations AND its results.

    Deleting the ``pseudonyms`` row cascades to ``users`` (and from there to sessions) and to ``results``. Deleting only
    the ``users`` row would be the retention path (see baror.storage.retention): results would stay, de-identified.
    """
    with db.transaction(db_path) as c:
        cur = c.execute("DELETE FROM pseudonyms WHERE pseudonym = " + PSEUDONYM_OF_USER, (user_id,))
        if cur.rowcount == 0:
            raise NotFoundError(detail=f"user {user_id}")


# -------------------------------------------------------------- results
def add_result(db_path, user_id: int, *, test_date: str, run_tenths: int | None = None,
               pushups: int | None = None) -> int:
    _check_date(test_date)
    _check_measurements(run_tenths, pushups)
    with db.transaction(db_path) as c:
        cur = c.execute("INSERT INTO results(pseudonym,test_date,run_tenths,pushups) "
                        "SELECT pseudonym,?,?,? FROM users WHERE id=?", (test_date, run_tenths, pushups, user_id))
        if cur.rowcount == 0:   # no such user: same refusal the old foreign key gave
            raise ValidationError("INVALID_RESULT", field="user", detail=f"no user {user_id!r}")
        return int(cur.lastrowid)


def list_results(db_path, user_id: int) -> list[dict[str, Any]]:
    """Newest first; id as tie-breaker so ordering is deterministic."""
    with db.reader(db_path) as c:
        rows = c.execute("SELECT id,test_date,run_tenths,pushups,created_at FROM results WHERE pseudonym = "
                         + PSEUDONYM_OF_USER + " ORDER BY test_date DESC, id DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def update_result(db_path, user_id: int, result_id: int, *, test_date: str, run_tenths: int | None,
                  pushups: int | None) -> None:
    _check_date(test_date)
    _check_measurements(run_tenths, pushups)
    with db.transaction(db_path) as c:
        cur = c.execute("UPDATE results SET test_date=?, run_tenths=?, pushups=? WHERE id=? AND pseudonym = " + PSEUDONYM_OF_USER,
                        (test_date, run_tenths, pushups, result_id, user_id))
        if cur.rowcount == 0:  # same answer for "no such id" and "someone else's id"
            raise NotFoundError(detail=f"result {result_id}")


def delete_result(db_path, user_id: int, result_id: int) -> None:
    with db.transaction(db_path) as c:
        cur = c.execute("DELETE FROM results WHERE id=? AND pseudonym = " + PSEUDONYM_OF_USER, (result_id, user_id))
        if cur.rowcount == 0:
            raise NotFoundError(detail=f"result {result_id}")


def find_duplicate(db_path, user_id: int, *, test_date: str, run_tenths: int | None, pushups: int | None) -> bool:
    with db.reader(db_path) as c:
        row = c.execute("SELECT 1 FROM results WHERE pseudonym = " + PSEUDONYM_OF_USER + " AND test_date=? AND run_tenths IS ? "
                        "AND pushups IS ? LIMIT 1", (user_id, test_date, run_tenths, pushups)).fetchone()
    return row is not None


def export_user_data(db_path, user_id: int) -> dict[str, Any]:
    """Everything stored about one user, for the 'export my data' button (rendered as a PDF by baror.export_pdf).
    Never includes pw_hash, ``created_by`` of an accommodation, or anything about another user except the display name of
    whoever read the results."""
    user = get_user(db_path, user_id)
    if user is None:
        raise NotFoundError(detail=f"user {user_id}")
    with db.reader(db_path) as c:
        # Who read this person's results (cohort view). Date + reader name + count only; see audit.reads_of_user.
        accessed_by = audit.reads_of_user(c, user_id)
    return {"user": user, "results": list_results(db_path, user_id), "accommodations": accommodations.list_own(db_path, user_id),
            "accessed_by": accessed_by}
