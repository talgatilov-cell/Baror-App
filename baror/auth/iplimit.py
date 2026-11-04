"""Per-IP abuse counters (run BEFORE scrypt). Stdlib only.

Thresholds are GENEROUS on purpose: a whole platoon may share one public IP (NAT), so a per-IP limit must never be
reachable by honest typos. Per-account delay (service.py) is the tight control; this only stops mass guessing/spam.
  * failed logins per IP:            30 in 10 min   -> cool-down 60 s, doubling per repeat, max 1 h
  * registration attempts per IP:    60 in 1 h      -> same cool-down scheme
  * registration attempts, ALL IPs:  600 in 1 h     -> same cool-down scheme (one shared counter for the whole app, so a
                                                       flood from many addresses cannot exhaust the CPU on scrypt)
  * failures per (IP, account) pair: 8 in 10 min    -> same cool-down scheme
IPv6 addresses are bucketed by /64. Memory is bounded (MAX_KEYS per table, oldest-touched evicted).
Trust: see client_ip(); never trust X-Forwarded-For from an untrusted peer.
"""
from __future__ import annotations

import ipaddress
import threading
import time
from collections import OrderedDict, deque

MAX_KEYS = 50_000
BASE_COOLDOWN_S = 60.0
MAX_COOLDOWN_S = 3600.0
STRIKE_DECAY_S = 86400.0   # a clean day forgives earlier cool-downs, so a shared IP is not punished for ever
LIMITS = {
    "login": (30, 600.0),
    "register": (60, 3600.0),
    "register_app": (600, 3600.0),
    "pair": (8, 600.0),
}
APP_KEY = "app"            # the single key of the app-wide table


def normalize_ip(raw: str) -> str:
    """Canonical bucket key: IPv4 as-is, IPv6 -> its /64 network. Unparseable -> 'invalid' (one shared bucket)."""
    try:
        a = ipaddress.ip_address(str(raw).strip().strip("[]"))
    except ValueError:
        return "invalid"
    if isinstance(a, ipaddress.IPv6Address):
        if a.ipv4_mapped is not None:
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network((int(a) >> 64 << 64, 64)))
    return str(a)


def _parse(raw):
    try:
        return ipaddress.ip_address(str(raw).strip().strip("[]"))
    except ValueError:
        return None


def client_ip(headers, peer, trusted_proxies=()):
    """Right-most untrusted X-Forwarded-For entry; falls back to the TCP peer.

    If peer is not a trusted proxy, XFF is ignored entirely (spoofable). Walk XFF from the right, skipping entries
    that are trusted proxies; the first untrusted one is the client. Garbage entries stop the walk (use that entry's
    left neighbour never: an unparseable hop means we cannot vouch for anything left of it, so return peer-side value).
    """
    trusted = set()
    for t in trusted_proxies:
        a = _parse(t)
        if a is not None:
            trusted.add(a)
    p = _parse(peer)
    if p is None:
        return "invalid"
    if p not in trusted:
        return str(p)
    xff = None
    for k, v in (headers or {}).items():
        if str(k).lower() == "x-forwarded-for":
            xff = v if xff is None else xff + "," + v
    if not xff:
        return str(p)
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for h in reversed(hops):
        a = _parse(h)
        if a is None:
            return str(p)
        if a not in trusted:
            return str(a)
    return str(p)


class _Table:
    def __init__(self, limit, window, clock, max_keys):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self.d = OrderedDict()  # key -> [deque(times), strikes, blocked_until]

    def _get(self, key, create):
        e = self.d.get(key)
        if e is None:
            if not create:
                return None
            e = [deque(), 0, 0.0]
            self.d[key] = e
            while len(self.d) > self.max_keys:
                self.d.popitem(last=False)
        else:
            self.d.move_to_end(key)
        return e

    def retry_after(self, key):
        e = self._get(key, False)
        if e is None:
            return 0.0
        return max(0.0, e[2] - self.clock())

    def record(self, key):
        now = self.clock()
        e = self._get(key, True)
        q = e[0]
        if e[1] and e[2] and now - e[2] > STRIKE_DECAY_S:
            e[1] = 0
        while q and q[0] <= now - self.window:
            q.popleft()
        q.append(now)
        if len(q) > self.limit and e[2] <= now:
            e[1] += 1
            e[2] = now + min(MAX_COOLDOWN_S, BASE_COOLDOWN_S * 2 ** (e[1] - 1))
            q.clear()

    def reset(self, key):
        self.d.pop(key, None)


class IpLimiter:
    def __init__(self, clock=time.monotonic, max_keys=MAX_KEYS, limits=None):
        self._lock = threading.Lock()
        lim = dict(LIMITS)
        lim.update(limits or {})
        self._t = {k: _Table(lim[k][0], lim[k][1], clock, max_keys) for k in lim}

    @staticmethod
    def _pair(ip, account):
        return normalize_ip(ip) + "|" + str(account).strip().lower()

    def check_login(self, ip, account=None):
        """Seconds to wait (0.0 = allowed). Call before hashing."""
        with self._lock:
            r = self._t["login"].retry_after(normalize_ip(ip))
            if account is not None:
                r = max(r, self._t["pair"].retry_after(self._pair(ip, account)))
            return r

    def record_login_failure(self, ip, account=None):
        with self._lock:
            self._t["login"].record(normalize_ip(ip))
            if account is not None:
                self._t["pair"].record(self._pair(ip, account))

    def record_login_success(self, ip, account=None):
        """Clears only the (IP, account) pair, never the whole-IP counter (else one valid login resets a guesser)."""
        with self._lock:
            if account is not None:
                self._t["pair"].reset(self._pair(ip, account))

    def check_register(self, ip):
        with self._lock:
            return self._t["register"].retry_after(normalize_ip(ip))

    def record_register_attempt(self, ip):
        with self._lock:
            self._t["register"].record(normalize_ip(ip))

    def check_register_app(self):
        """Seconds to wait (0.0 = allowed) for the app-wide registration counter. Call before hashing."""
        with self._lock:
            return self._t["register_app"].retry_after(APP_KEY)

    def record_register_app(self):
        with self._lock:
            self._t["register_app"].record(APP_KEY)

    def size(self):
        with self._lock:
            return sum(len(t.d) for t in self._t.values())
