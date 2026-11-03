"""First-party bot check: signed one-time form token + hidden honeypot + minimum fill time. Stdlib only.

Token = "<session_id-bound>": base64url(nonce|issued_ts) + "." + HMAC-SHA256(secret, session_id|nonce|ts).
Not a CAPTCHA: stops naive scripts only. Rejections return a reason string; callers show ONE generic message.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

MIN_SECONDS = 2.0
MAX_SECONDS = 3600.0
MAX_USED = 100_000


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


class BotCheck:
    def __init__(self, secret: bytes, clock=time.time, min_seconds=MIN_SECONDS, max_seconds=MAX_SECONDS):
        if not isinstance(secret, (bytes, bytearray)) or len(secret) < 16:
            raise ValueError("secret must be >= 16 bytes")
        self._secret, self._clock = bytes(secret), clock
        self.min_seconds, self.max_seconds = min_seconds, max_seconds
        self._used = {}  # nonce -> expiry
        self._lock = threading.Lock()

    def _sig(self, session_id, payload):
        msg = session_id.encode() + b"|" + payload.encode()
        return _b64(hmac.new(self._secret, msg, hashlib.sha256).digest())

    def issue(self, session_id: str) -> str:
        payload = _b64(secrets.token_hex(12).encode() + b"|" + repr(self._clock()).encode())
        return payload + "." + self._sig(session_id, payload)

    def verify(self, token, session_id, honeypot_value="") -> tuple:
        """Returns (ok, reason). reasons: honeypot, malformed, signature, session, too_fast, expired, replay."""
        if honeypot_value:
            return False, "honeypot"
        try:
            payload, sig = str(token).split(".", 1)
            raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            nonce, ts = raw.split(b"|", 1)
            issued = float(ts.decode())
        except Exception:
            return False, "malformed"
        if not hmac.compare_digest(sig.encode(), self._sig(session_id, payload).encode()):
            return False, "signature"  # also covers wrong session / tampered
        now = self._clock()
        elapsed = now - issued
        if elapsed < self.min_seconds:
            return False, "too_fast"
        if elapsed > self.max_seconds:
            return False, "expired"
        with self._lock:
            for k in [k for k, exp in self._used.items() if exp < now]:
                del self._used[k]
            if nonce in self._used:
                return False, "replay"
            if len(self._used) >= MAX_USED:
                return False, "expired"
            self._used[nonce] = issued + self.max_seconds
        return True, "ok"
