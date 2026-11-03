"""Environment configuration, validated once at startup. Stdlib only; never imports streamlit.

    DB_PATH               SQLite file (default /data/baror.db); its directory must exist and be writable
    SESSION_DAYS          lifetime of the "stay signed in" cookie, 1..90 (default 14)
    TZ_NAME               IANA zone used for "today" (default Asia/Jerusalem)
    BAROR_COHORT_VIEWS    "1" shows the instructor and chief screens (default off until the owner approves the
                          notice that cadets are given about them)

Invalid values raise ``ConfigError`` with a readable internal detail; nothing here logs or echoes secrets.
"""
from __future__ import annotations

import os
import zoneinfo
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from baror.errors import ConfigError

DEFAULT_DB_PATH = "/data/baror.db"
ENV_KEYS = ("DB_PATH", "SESSION_DAYS", "TZ_NAME", "BAROR_COHORT_VIEWS")


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

    @property
    def tz(self) -> zoneinfo.ZoneInfo:
        return zoneinfo.ZoneInfo(self.tz_name)


def _flag(raw: str | None, name: str) -> bool:
    if raw is None or raw.strip() == "":
        return False
    if raw.strip() in ("0", "1"):
        return raw.strip() == "1"
    raise ConfigError(detail=f"{name} must be 0 or 1")


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
    db_path = Path(env.get("DB_PATH") or DEFAULT_DB_PATH)
    return Config(db_path=db_path, session_days=days, tz_name=tz_name,
                  cohort_views=_flag(env.get("BAROR_COHORT_VIEWS"), "BAROR_COHORT_VIEWS"))
