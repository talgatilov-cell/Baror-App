"""Environment configuration, validated once at startup. Stdlib only; never imports streamlit.

    DB_PATH               SQLite file (default /data/baror.db); its directory must exist and be writable
    SESSION_DAYS          lifetime of the "stay signed in" cookie, 1..90 (default 14)
    TZ_NAME               IANA zone used for "today" (default Asia/Jerusalem)
    TRUSTED_PROXIES       comma-separated IP addresses of the reverse proxies whose X-Forwarded-For header may be
                          believed (default: none, so the header is ignored). Exact addresses only: no "*", no ranges.
    BAROR_BOT_MIN_SECONDS minimum seconds between opening a form and submitting it (default 2, range 0..60)
    FORWARDED_ALLOW_IPS   read only to REFUSE the unsafe value "*" (the client-sent X-Forwarded-For would win)
    BAROR_COHORT_VIEWS    "1" shows the instructor and chief screens (default off until the owner approves the
                          notice that cadets are given about them)

Invalid values raise ``ConfigError`` with a readable internal detail; nothing here logs or echoes secrets.
"""
from __future__ import annotations

import ipaddress
import os
import zoneinfo
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from baror.errors import ConfigError

DEFAULT_DB_PATH = "/data/baror.db"
ENV_KEYS = ("DB_PATH", "SESSION_DAYS", "TZ_NAME", "BAROR_COHORT_VIEWS", "TRUSTED_PROXIES", "BAROR_BOT_MIN_SECONDS",
            "FORWARDED_ALLOW_IPS")


def env_key(env: Mapping[str, str] | None = None) -> tuple:
    """Hashable snapshot of the variables this module reads: a cache key, so a changed environment (tests) is never
    served a configuration built from another one."""
    env = os.environ if env is None else env
    return tuple((k, env.get(k)) for k in ENV_KEYS)


@dataclass(frozen=True)
class Config:
    db_path: Path
    session_days: int = 14
    tz_name: str = "Asia/Jerusalem"
    cohort_views: bool = False
    trusted_proxies: tuple = ()
    bot_min_seconds: float = 2.0

    @property
    def tz(self) -> zoneinfo.ZoneInfo:
        return zoneinfo.ZoneInfo(self.tz_name)


def _flag(raw: str | None, name: str) -> bool:
    if raw is None or raw.strip() == "":
        return False
    if raw.strip() in ("0", "1"):
        return raw.strip() == "1"
    raise ConfigError(detail=f"{name} must be 0 or 1")


def _proxies(raw: str | None) -> tuple:
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(str(ipaddress.ip_address(part.strip("[]"))))
        except ValueError:
            raise ConfigError(detail="TRUSTED_PROXIES must be a comma-separated list of exact IP addresses "
                                     "(no '*', no ranges, no host names)") from None
    return tuple(out)


def from_env(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    try:
        days = int(env.get("SESSION_DAYS", "14"))
    except ValueError:
        raise ConfigError(detail="SESSION_DAYS must be an integer") from None
    if not 1 <= days <= 90:
        raise ConfigError(detail="SESSION_DAYS must be between 1 and 90")
    tz_name = env.get("TZ_NAME", "Asia/Jerusalem")
    try:
        zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        raise ConfigError(detail="TZ_NAME is not a known time zone (is the tzdata package installed?)") from None
    if "*" in (env.get("FORWARDED_ALLOW_IPS") or ""):
        raise ConfigError(detail="FORWARDED_ALLOW_IPS must never contain '*': any client could then claim any address "
                                 "through X-Forwarded-For. List the proxy addresses in TRUSTED_PROXIES instead.")
    try:
        bot_min = float(env.get("BAROR_BOT_MIN_SECONDS", "2"))
    except ValueError:
        raise ConfigError(detail="BAROR_BOT_MIN_SECONDS must be a number") from None
    if not 0 <= bot_min <= 60:
        raise ConfigError(detail="BAROR_BOT_MIN_SECONDS must be between 0 and 60")
    db_path = Path(env.get("DB_PATH") or DEFAULT_DB_PATH)
    return Config(db_path=db_path, session_days=days, tz_name=tz_name,
                  cohort_views=_flag(env.get("BAROR_COHORT_VIEWS"), "BAROR_COHORT_VIEWS"),
                  trusted_proxies=_proxies(env.get("TRUSTED_PROXIES")), bot_min_seconds=bot_min)
