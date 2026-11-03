"""scrypt password hashing, stdlib only. Corrected version of the IMPLEMENTATION_BRIEF.md T3.2 snippet.

Changes vs the brief (each covered by tests/test_passwords.py):
* verify_password never raises for None / non-str / garbage stored values (the brief's version raised AttributeError).
* Cost parameters in the stored string are NOT trusted: only parameter sets in ``_ACCEPTED`` are verified, so a
  tampered row (e.g. p=64, ~2 s per attempt) cannot be used to burn CPU. To raise the cost later, add the new set
  to ``_ACCEPTED`` and make it ``_CURRENT``; ``needs_rehash`` tells the login code to upgrade on next success.
* Length is bounded (NIST: allow long passwords, but not unbounded work or memory).
"""
from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import hmac
import secrets
import unicodedata

from baror.errors import ValidationError

_CURRENT = (2**15, 8, 3)              # N, r, p  (OWASP-listed equivalent to N=2^17,r=8,p=1 with less memory)
_ACCEPTED = {_CURRENT}
_MAXMEM = 128 * 1024 * 1024           # default maxmem (32 MiB) rejects N >= 2^15
_DKLEN = 32
_SALT_LEN = 16
MAX_PASSWORD_CHARS = 128
MIN_PASSWORD_CHARS = 8                # owner decision 2026-11-02; applies when a password is CHOSEN, never when verifying


def check_policy(pw) -> None:
    """Policy for a newly chosen password (registration, password change). Raises ValidationError.

    Not applied by ``hash_password`` or ``verify_password``: the JSON migration must hash legacy passwords of any
    length, and existing short passwords must keep verifying until their owner is forced to change them.
    """
    if not isinstance(pw, str) or not MIN_PASSWORD_CHARS <= len(pw) <= MAX_PASSWORD_CHARS:
        raise ValidationError("INVALID_PASSWORD", field="password",
                              detail=f"password must be {MIN_PASSWORD_CHARS}..{MAX_PASSWORD_CHARS} characters")


def _prep(pw: str) -> bytes:
    return unicodedata.normalize("NFKC", pw).encode("utf-8")


def hash_password(pw: str) -> str:
    if not isinstance(pw, str) or not 1 <= len(pw) <= MAX_PASSWORD_CHARS:
        raise ValueError("password length out of range")
    n, r, p = _CURRENT
    salt = secrets.token_bytes(_SALT_LEN)
    dk = hashlib.scrypt(_prep(pw), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=_DKLEN)
    return "$".join(["scrypt", str(n), str(r), str(p),
                     base64.b64encode(salt).decode("ascii"), base64.b64encode(dk).decode("ascii")])


def _parse(stored):
    if not isinstance(stored, str):
        return None
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    try:
        params = (int(parts[1]), int(parts[2]), int(parts[3]))
        salt = base64.b64decode(parts[4], validate=True)
        dk = base64.b64decode(parts[5], validate=True)
    except (ValueError, binascii.Error):
        return None
    if params not in _ACCEPTED or len(salt) != _SALT_LEN or len(dk) != _DKLEN:
        return None
    return params, salt, dk


def verify_password(pw, stored) -> bool:
    """Constant-time comparison. Returns False (never raises) for any malformed input."""
    if not isinstance(pw, str) or len(pw) > MAX_PASSWORD_CHARS:
        return False
    parsed = _parse(stored)
    if parsed is None:
        return False
    (n, r, p), salt, dk = parsed
    try:
        calc = hashlib.scrypt(_prep(pw), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=_DKLEN)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(calc, dk)


def needs_rehash(stored: str) -> bool:
    parsed = _parse(stored)
    return parsed is None or parsed[0] != _CURRENT


@functools.lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("dummy-password-for-timing-equalisation")  # lazy: no import-time work


def verify_against_dummy(pw) -> bool:
    """Call when the e-mail does not exist so unknown-user and wrong-password cost about the same."""
    verify_password(pw, _dummy_hash())
    return False
