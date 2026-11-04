"""Open registration (schema v9). Stdlib only; never imports streamlit.

* ``public_platoons``  the platoons offered on the registration form (id + name only; names are not personal data).
* ``capacity_left``    places left in the DATABASE-wide hourly sign-up cap. It counts ``signup_events`` (one row per
                       sign-up, no personal data), which survive the deletion of an account, so deleting accounts cannot
                       be used to reopen the cap.
* ``register_cadet``   the ONLY web path that creates an account. Always a cadet; the role is not a parameter. One
                       ``BEGIN IMMEDIATE`` transaction: re-checks the cap (``SignupBusyError``), the platoon and the
                       e-mail (a duplicate or malformed address -> the same generic ``RegistrationDeniedError``),
                       inserts pseudonym + user + sign-up event, prunes events older than two hours and writes the audit row
                       ``auth.register`` (ids only, never the e-mail).
* ``platoon_from_join_token``  the platoon a valid ``?join=`` link points at, else ``None``. Never raises.

Staff roles are never created here: an admin or a chief grants them later to an existing account (``people.set_role``).
"""
from __future__ import annotations

import datetime as dt

from baror.domain import joinlink
from baror.errors import RegistrationDeniedError, SignupBusyError, ValidationError
from baror.storage import audit, db, repo

DEFAULT_CAP_PER_HOUR = 120
MIN_CAP, MAX_CAP = 1, 1000
WINDOW = dt.timedelta(hours=1)
KEEP = dt.timedelta(hours=2)


def normalize_cap(value) -> int:
    """A cap from configuration or a caller: an int in 1..1000, anything else (None, bool, text, 0, 5000) falls back to
    the default (120). Failing to the default keeps a typo from switching the protection off."""
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_CAP <= value <= MAX_CAP:
        return DEFAULT_CAP_PER_HOUR
    return value


def _used(c, now) -> int:
    since = audit.ts((now or dt.datetime.now(dt.timezone.utc)) - WINDOW)
    return int(c.execute("SELECT count(*) FROM signup_events WHERE ts > ?", (since,)).fetchone()[0])


def public_platoons(db_path) -> list[dict]:
    """``[{"id", "name"}]`` ordered by name: what the registration form offers."""
    with db.reader(db_path) as c:
        return [{"id": r["id"], "name": r["name"]} for r in c.execute("SELECT id,name FROM platoons ORDER BY name, id")]


def capacity_left(db_path, cap, now: dt.datetime | None = None) -> int:
    """Sign-ups still allowed in the last hour: ``cap`` minus the sign-ups of that hour, never below 0."""
    with db.reader(db_path) as c:
        return max(0, normalize_cap(cap) - _used(c, now))


def register_cadet(db_path, *, email: str, name: str, gender: str, is_km: bool, pw_hash: str,
                   platoon_id: int | None = None, cap, now: dt.datetime | None = None) -> int:
    """Create a cadet account and return its id.

    Errors: ``ValidationError`` for a bad name / gender / unknown platoon (field problems that say nothing about the
    e-mail); ``RegistrationDeniedError`` (generic, the reason only in ``detail``) for a malformed or already used
    address; ``SignupBusyError`` when the hourly cap is used up (also if another sign-up took the last place between a
    caller's ``capacity_left`` and this call)."""
    try:
        e = repo.normalize_email(email)
    except ValidationError:
        raise RegistrationDeniedError(detail="malformed e-mail") from None
    clean_name = repo.check_display_name(name)
    if gender not in repo.GENDERS:
        raise ValidationError("INVALID_GENDER", field="gender", detail=f"gender={gender!r}")
    if platoon_id is not None and (isinstance(platoon_id, bool) or not isinstance(platoon_id, int)
                                   or not 0 < platoon_id < 2**63):
        raise ValidationError("PLATOON_REQUIRED", field="platoon", detail="bad platoon id")
    limit = normalize_cap(cap)
    when = now or dt.datetime.now(dt.timezone.utc)
    with db.transaction(db_path) as c:
        c.execute("DELETE FROM signup_events WHERE ts < ?", (audit.ts(when - KEEP),))
        if _used(c, when) >= limit:
            raise SignupBusyError(detail="hourly sign-up cap reached")
        if platoon_id is not None and c.execute("SELECT 1 FROM platoons WHERE id=?", (platoon_id,)).fetchone() is None:
            raise ValidationError("PLATOON_REQUIRED", field="platoon", detail="unknown platoon")
        if c.execute("SELECT 1 FROM users WHERE email=?", (e,)).fetchone() is not None:
            raise RegistrationDeniedError(detail="account exists")
        pseudonym = repo.new_pseudonym()
        c.execute("INSERT INTO pseudonyms(pseudonym) VALUES(?)", (pseudonym,))
        uid = int(c.execute(
            "INSERT INTO users(email,name,gender,is_km,pw_hash,must_change_pw,role,platoon_id,pseudonym) "
            "VALUES(?,?,?,?,?,0,'cadet',?,?)",
            (e, clean_name, gender, int(bool(is_km)), pw_hash, platoon_id, pseudonym)).lastrowid)
        c.execute("INSERT INTO signup_events(ts) VALUES(?)", (audit.ts(when),))
        audit.record(c, actor_id=uid, action="auth.register", target_user_id=uid, platoon_id=platoon_id, now=when)
        return uid


def platoon_from_join_token(db_path, secret, token, now: dt.datetime | None = None) -> int | None:
    """The platoon id of a genuine, unexpired, unrevoked join link; ``None`` for anything else, including no secret
    (feature off), a malformed token or a database problem. Never raises."""
    try:
        parsed = joinlink.parse(token)
        if parsed is None:
            return None
        with db.reader(db_path) as c:
            row = c.execute("SELECT join_epoch FROM platoons WHERE id=?", (parsed[0],)).fetchone()
        if row is None:
            return None
        t0 = now or dt.datetime.now(dt.timezone.utc)
        return joinlink.verify(secret, token, int(row["join_epoch"]), int(t0.timestamp()))
    except Exception:                       # noqa: BLE001  (a pre-selection must never break the registration page)
        return None
