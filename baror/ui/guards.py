"""Streamlit side of the abuse controls: the process-wide limiter and bot check, and the client address.

* ``limiter()`` / ``botcheck()`` are ``st.cache_resource`` singletons: they hold counters keyed by address and
  one-time form tokens, never user data. State is per process (documented limit: a restart forgets it).
* The client address is resolved by ``baror.auth.iplimit.client_ip``: the TCP peer, and X-Forwarded-For only when
  the peer is one of the configured TRUSTED_PROXIES (right-most untrusted hop). With no proxy configured the header
  is ignored completely.
* Limits are GENEROUS on purpose (owner rule): a whole platoon may share one public address.
"""
from __future__ import annotations

import ipaddress
import logging
import secrets
import time

import streamlit as st

from baror.auth.botcheck import BotCheck
from baror.auth.iplimit import IpLimiter, client_ip as resolve_client_ip
from baror.config import Config

# (attempts, window seconds). Proposal for the owner to confirm; the research defaults were 30 / 20 / 8.
#   login failures per address:   60 in 10 min   (a platoon of 40 typing at once)
#   registration attempts:        100 in 1 h      (each costs one scrypt; the allowlist is the real gate)
#   failures per (address, account): 8 in 10 min  (the per-account backoff is the tight control)
LIMITS = {"login": (60, 600.0), "register": (100, 3600.0), "pair": (8, 600.0)}

log = logging.getLogger("baror.guards")
_warned_proxy = False

_now = time.time                  # indirection so tests can move the clock of the bot check
SID_KEY = "_bot_sid"              # leading underscore: removed at sign-out


@st.cache_resource
def limiter() -> IpLimiter:
    return IpLimiter(limits=LIMITS)


@st.cache_resource
def _botcheck(min_seconds: float) -> BotCheck:
    return BotCheck(secrets.token_bytes(32), clock=lambda: _now(), min_seconds=min_seconds)


def botcheck(cfg: Config) -> BotCheck:
    return _botcheck(cfg.bot_min_seconds)


def client_ip(cfg: Config) -> str:
    ctx = st.context
    peer = getattr(ctx, "ip_address", None) or "127.0.0.1"      # Streamlit reports loopback as None
    try:
        headers = dict(ctx.headers)
    except Exception:                                            # noqa: BLE001  (no headers: ignore, never fail open)
        headers = {}
    _warn_if_behind_an_untrusted_proxy(peer, headers, cfg)
    return resolve_client_ip(headers, peer, cfg.trusted_proxies)


def _warn_if_behind_an_untrusted_proxy(peer: str, headers: dict, cfg: Config) -> None:
    """Operator hint, once per process: a request that arrives from a non-loopback address AND carries
    X-Forwarded-For while no proxy is trusted usually means a reverse proxy without TRUSTED_PROXIES. Then every cadet
    shares the proxy's address and a single outsider can exhaust the shared limit for everybody. (A client that sends
    the header itself triggers the same warning, which is harmless.) Nothing is logged about the request itself."""
    global _warned_proxy
    if _warned_proxy or cfg.trusted_proxies or not any(str(k).lower() == "x-forwarded-for" for k in headers):
        return
    try:
        if ipaddress.ip_address(str(peer).strip("[]")).is_loopback:
            return
    except ValueError:
        return
    _warned_proxy = True
    log.warning("proxy_untrusted: requests carry X-Forwarded-For but TRUSTED_PROXIES is empty; all clients behind the "
                "proxy share one address for rate limiting. Set TRUSTED_PROXIES to the proxy address.")


def _sid() -> str:
    return st.session_state.setdefault(SID_KEY, secrets.token_hex(16))


def issue_token(cfg: Config, form: str) -> str:
    """The form's one-time token (kept for this browser session, created when the form is first shown)."""
    key = f"_bot_{form}"
    if key not in st.session_state:
        st.session_state[key] = botcheck(cfg).issue(_sid())
    return st.session_state[key]


def check_and_renew(cfg: Config, form: str, honeypot_value) -> bool:
    """Verify the form's token and honeypot; ALWAYS issue a fresh token (a token is single use). True = human enough."""
    token = st.session_state.get(f"_bot_{form}")
    ok, _reason = botcheck(cfg).verify(token, _sid(), honeypot_value or "")
    st.session_state[f"_bot_{form}"] = botcheck(cfg).issue(_sid())
    return ok
