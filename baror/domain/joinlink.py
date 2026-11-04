"""Signed join links (schema v9). Pure Python: stdlib ``hmac`` / ``hashlib`` only, no I/O, no streamlit.

A join link only PRE-SELECTS a platoon on the registration form (``?join=<token>``); it grants nothing, and the cadet
can still change the selection. The token is ``<platoon id>.<epoch>.<expiry>.<mac>``:

* ``platoon id``  the platoon the link points at;
* ``epoch``       ``platoons.join_epoch`` at signing time; bumping the column revokes every earlier link of that platoon;
* ``expiry``      UNIX seconds (UTC) after which the link is dead;
* ``mac``         first 32 hex characters (128 bits) of HMAC-SHA256 over ``join|<platoon>|<epoch>|<expiry>`` keyed with
                  the operator secret ``BAROR_JOIN_LINK_SECRET`` (at least 32 characters; below that the feature is off).

``verify`` never raises: any malformed, forged, expired or revoked token gives ``None``. The comparison of the MAC is
constant time (``hmac.compare_digest``). The secret is never part of a message, a repr or a log line.
"""
from __future__ import annotations

import hashlib
import hmac
import re

MIN_SECRET_CHARS = 32
MAX_TOKEN_CHARS = 120
_MAC_CHARS = 32
_TOKEN = re.compile(r"([0-9]{1,18})\.([0-9]{1,18})\.([0-9]{1,18})\.([0-9a-f]{%d})" % _MAC_CHARS)
_MAX_ID = 2**63 - 1


def _key(secret) -> bytes | None:
    if not isinstance(secret, (str, bytes)) or len(secret) < MIN_SECRET_CHARS:    # characters for str, bytes for bytes
        return None
    return secret.encode("utf-8") if isinstance(secret, str) else secret


def _mac(key: bytes, platoon_id: int, epoch: int, exp_ts: int) -> str:
    msg = f"join|{platoon_id}|{epoch}|{exp_ts}".encode("ascii")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:_MAC_CHARS]


def _is_int(v, lo: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= _MAX_ID


def sign(secret, platoon_id: int, epoch: int, exp_ts: int) -> str:
    """The token for one platoon. Raises ``ValueError`` for a secret shorter than 32 characters or a bad number (the
    caller decides who may sign: this function only builds the string)."""
    key = _key(secret)
    if key is None:
        raise ValueError("join link secret too short")
    if not (_is_int(platoon_id, 1) and _is_int(epoch, 0) and _is_int(exp_ts, 0)):
        raise ValueError("platoon id, epoch and expiry must be non-negative integers")
    return f"{platoon_id}.{epoch}.{exp_ts}.{_mac(key, platoon_id, epoch, exp_ts)}"


def parse(token) -> tuple[int, int, int, str] | None:
    """``(platoon_id, epoch, exp_ts, mac)`` of a well-formed token, else ``None``. The MAC is NOT checked here, so the
    result must not be trusted before ``verify`` (or a MAC check) has accepted the same token."""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
        return None
    m = _TOKEN.fullmatch(token)
    if m is None:
        return None
    pid, epoch, exp = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if pid < 1:
        return None
    return pid, epoch, exp, m.group(4)


def verify(secret, token, current_epoch: int, now_ts: int | float) -> int | None:
    """The platoon id when ``token`` is genuine (MAC matches), not expired (``exp > now_ts``) and its epoch equals
    ``current_epoch`` (the platoon's present ``join_epoch``); otherwise ``None``. Never raises."""
    try:
        key = _key(secret)
        parsed = parse(token)
        if key is None or parsed is None or isinstance(current_epoch, bool) or not isinstance(current_epoch, int):
            return None
        pid, epoch, exp, mac = parsed
        if not hmac.compare_digest(mac.encode("ascii"), _mac(key, pid, epoch, exp).encode("ascii")):
            return None
        if epoch != current_epoch or not exp > now_ts:
            return None
        return pid
    except Exception:                       # noqa: BLE001  (a link check must never break the registration page)
        return None
