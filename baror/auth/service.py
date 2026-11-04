"""Login orchestration: the single place that decides whether credentials are accepted. Stdlib only.

Properties (tested in tests/test_auth_service.py):
* One generic AuthError for unknown e-mail, wrong password and malformed e-mail (no account enumeration by message).
* Unknown e-mail still pays one scrypt (``verify_against_dummy``) so timing does not reveal existence.
* Per-account backoff (policy below). A correct password during the backoff window is ALSO refused, otherwise the
  backoff would not slow a guesser down. Trade-off, stated plainly: a classmate who knows a victim's e-mail can keep
  that account in backoff (max ``MAX_DELAY_S`` at a time). Mitigations: small delay cap, per-address and app-wide
  registration limits, audit log of failures. Owner may choose a different policy (see research_notes/user_isolation.md).
* Legacy/low-cost hashes are upgraded on successful login (``needs_rehash``).
* Deactivated accounts (``users.active=0``) get the SAME generic AuthError as a wrong password, AFTER a real scrypt
  verification of the supplied password (same cost as a normal failure), and without touching the failure counter.
  So "deactivated" is indistinguishable from "wrong password" by message and by timing.
* ``register`` (open registration, always a cadet) hashes the password on EVERY path before anything that depends on the
  e-mail is consulted, and returns one generic RegistrationDeniedError for a malformed or already used e-mail (see
  storage/signup.py).
"""
from __future__ import annotations

import datetime as dt
import math
import threading
from collections import OrderedDict

from baror.auth import passwords
from baror.errors import AuthError, RateLimitedError, RegistrationClosedError, SignupBusyError, ValidationError
from baror.storage import db, repo, signup

FREE_FAILURES = 4          # first 4 wrong passwords cost nothing extra
BASE_DELAY_S = 2           # 5th failure -> 2 s, then doubling
MAX_DELAY_S = 300          # cap: 5 minutes
_FMT = "%Y-%m-%dT%H:%M:%SZ"


GHOST_MAX_KEYS = 10_000


class _UnknownEmailBackoff:
    """The same backoff schedule for e-mails that have NO account, kept in memory (bounded, thread-safe).

    Without it only existing accounts ever answer "wait N seconds", which tells a guesser which e-mails exist.
    State is per process and lost on restart; that is acceptable because it only has to look the same, not protect.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._d: OrderedDict[str, list] = OrderedDict()   # key -> [failures, locked_until | None]

    @staticmethod
    def key(email) -> str:
        return str(email).strip().lower()[:254]

    def wait(self, email, now) -> int:
        with self._lock:
            e = self._d.get(self.key(email))
            if not e or e[1] is None:
                return 0
            return max(0, math.ceil((e[1] - now).total_seconds()))

    def fail(self, email, now) -> None:
        k = self.key(email)
        with self._lock:
            e = self._d.setdefault(k, [0, None])
            self._d.move_to_end(k)
            e[0] += 1
            d = delay_for(e[0])
            e[1] = now + dt.timedelta(seconds=d) if d else None
            while len(self._d) > GHOST_MAX_KEYS:
                self._d.popitem(last=False)


_ghosts = _UnknownEmailBackoff()


def reset_unknown_email_backoff() -> None:
    """Forget the in-memory state for e-mails without an account (tests, and a manual operator reset)."""
    global _ghosts
    _ghosts = _UnknownEmailBackoff()


def delay_for(failures: int) -> int:
    if failures <= FREE_FAILURES:
        return 0
    return min(MAX_DELAY_S, BASE_DELAY_S * 2 ** (failures - FREE_FAILURES - 1))


def _now(now):
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0)


def _locked_seconds(rec, now) -> int:
    if not rec["locked_until"]:
        return 0
    until = dt.datetime.strptime(rec["locked_until"], _FMT).replace(tzinfo=dt.timezone.utc)
    return max(0, math.ceil((until - now).total_seconds()))


def _record_failure(db_path, user_id, now) -> None:
    with db.transaction(db_path) as c:  # BEGIN IMMEDIATE: concurrent failures cannot lose increments
        n = c.execute("SELECT failed_logins FROM users WHERE id=?", (user_id,)).fetchone()
        if n is None:
            return
        failures = n[0] + 1
        d = delay_for(failures)
        until = (now + dt.timedelta(seconds=d)).strftime(_FMT) if d else None
        c.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", (failures, until, user_id))


def _record_success(db_path, user_id) -> None:
    with db.transaction(db_path) as c:
        c.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", (user_id,))


def login(db_path, email, password, now: dt.datetime | None = None) -> dict:
    """Returns the user dict on success. Raises AuthError (generic) or RateLimitedError."""
    t = _now(now)
    rec = repo.get_login_record(db_path, email)
    if rec is None:
        wait = _ghosts.wait(email, t)   # same lock-out behaviour as a real account: no existence oracle
        if wait > 0:
            raise RateLimitedError(params={"seconds": wait})
        passwords.verify_against_dummy(password)
        _ghosts.fail(email, t)
        raise AuthError()
    wait = _locked_seconds(rec, t)
    if wait > 0:
        raise RateLimitedError(params={"seconds": wait})
    if not passwords.verify_password(password, rec["pw_hash"]):
        _record_failure(db_path, rec["id"], t)
        raise AuthError()
    if not rec["active"]:  # right password, disabled account: generic error, no counter change, no session
        raise AuthError()
    _record_success(db_path, rec["id"])
    if passwords.needs_rehash(rec["pw_hash"]):
        repo.set_password_hash(db_path, rec["id"], passwords.hash_password(password),
                               must_change_pw=bool(rec["must_change_pw"]), revoke_sessions=False)
    user = repo.get_user(db_path, rec["id"])
    return user


def change_password(db_path, user_id, current_password, new_password, now: dt.datetime | None = None) -> None:
    """Replace the password of a signed-in user and revoke ALL their sessions (the caller issues a new one).

    The current password is verified through ``login`` -- same generic error, same per-account backoff -- so a stolen
    session cookie cannot be used to guess the password here without limit. The new password follows the policy
    (8..128 characters) and must differ from the current one. ``must_change_pw`` is cleared.
    """
    passwords.check_policy(new_password)
    if new_password == current_password:
        raise ValidationError("PASSWORD_UNCHANGED", field="password", detail="new password equals the current one")
    user = repo.get_user(db_path, user_id) if isinstance(user_id, int) and not isinstance(user_id, bool) else None
    if user is None or not user["active"]:
        passwords.verify_against_dummy(current_password)
        raise AuthError()
    login(db_path, user["email"], current_password, now)
    repo.set_password_hash(db_path, user["id"], passwords.hash_password(new_password), must_change_pw=False,
                           revoke_sessions=True)


def register(db_path, *, email, name, gender, is_km, password, platoon_id: int | None = None,
             cap: int = signup.DEFAULT_CAP_PER_HOUR, registration_open: bool = True,
             now: dt.datetime | None = None) -> dict:
    """Open self-registration: EVERY new account is a cadet (``platoon_id`` is optional). Returns the new user dict; no
    session is issued here.

    Order (nothing before the hash depends on the e-mail, so every outcome costs one scrypt):
    1. the kill switch (``registration_open=False`` -> RegistrationClosedError; cheap, before any work);
    2. the password policy (ValidationError, a field problem independent of the e-mail);
    3. the hourly sign-up cap (SignupBusyError; independent of the e-mail);
    4. scrypt, always;
    5. ``signup.register_cadet``: one generic RegistrationDeniedError for a malformed or already used e-mail, field
       ValidationError for name / gender / unknown platoon, SignupBusyError if the cap filled up meanwhile.
    """
    if not registration_open:
        raise RegistrationClosedError(detail="registration is closed (BAROR_REGISTRATION_OPEN=0)")
    passwords.check_policy(password)                  # 8..128 characters
    if signup.capacity_left(db_path, cap, now) <= 0:
        raise SignupBusyError(detail="hourly sign-up cap reached")
    pw_hash = passwords.hash_password(password)       # always, before anything that depends on the e-mail
    uid = signup.register_cadet(db_path, email=email, name=name, gender=gender, is_km=is_km, pw_hash=pw_hash,
                                platoon_id=platoon_id, cap=cap, now=now)
    return repo.get_user(db_path, uid)
