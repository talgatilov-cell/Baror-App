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

Outgoing mail (only the report script reads these, via ``mail_from_env``; unset BAROR_SMTP_HOST = mail is not configured):
    BAROR_SMTP_HOST       SMTP server host name
    BAROR_SMTP_PORT       port (default 587 for starttls, 465 for ssl)
    BAROR_SMTP_MODE       "starttls" (default) or "ssl"; there is NO plain mode: TLS with certificate verification is required
    BAROR_SMTP_USER       login (required unless the host is this machine)
    BAROR_SMTP_PASSWORD   password (never logged, never part of a repr)
    BAROR_SMTP_FROM       sender address

Invalid values raise ``ConfigError`` with a readable internal detail; nothing here logs or echoes secrets.
"""
from __future__ import annotations

import ipaddress
import os
import re
import zoneinfo
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    mode: str                                   # 'starttls' | 'ssl'
    sender: str
    user: str | None = None
    password: str | None = field(default=None, repr=False)      # never shown in a repr or a log line


_ADDRESS = re.compile(r"^[^\s@<>,;\"']{1,64}@[A-Za-z0-9.\-]{1,255}$")


def valid_address(value: str) -> bool:
    """A single plain address: no spaces, no CR/LF, no list separators or display-name syntax (header-injection safe)."""
    return isinstance(value, str) and len(value) <= 254 and bool(_ADDRESS.fullmatch(value))


def mail_from_env(env: Mapping[str, str] | None = None) -> MailConfig | None:
    """None when BAROR_SMTP_HOST is unset or blank (mail not configured). Otherwise a validated ``MailConfig``;
    ``ConfigError`` (detail names the variable, never its value) when it is incomplete or unsafe."""
    env = os.environ if env is None else env
    host = (env.get("BAROR_SMTP_HOST") or "").strip()
    if not host:
        return None
    if not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host):
        raise ConfigError(detail="BAROR_SMTP_HOST must be a host name or address")
    mode = (env.get("BAROR_SMTP_MODE") or "starttls").strip().lower()
    if mode not in ("starttls", "ssl"):
        raise ConfigError(detail="BAROR_SMTP_MODE must be starttls or ssl (plain SMTP is not supported)")
    try:
        port = int((env.get("BAROR_SMTP_PORT") or "").strip() or (465 if mode == "ssl" else 587))
    except ValueError:
        raise ConfigError(detail="BAROR_SMTP_PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ConfigError(detail="BAROR_SMTP_PORT must be between 1 and 65535")
    sender = (env.get("BAROR_SMTP_FROM") or "").strip()
    if not valid_address(sender):
        raise ConfigError(detail="BAROR_SMTP_FROM must be one plain e-mail address")
    user, password = env.get("BAROR_SMTP_USER") or None, env.get("BAROR_SMTP_PASSWORD") or None
    local = host.lower() in ("localhost", "127.0.0.1", "::1")
    if (user is None or password is None) and not local:
        raise ConfigError(detail="BAROR_SMTP_USER and BAROR_SMTP_PASSWORD are required for a remote mail server")
    if any(ch in (user or "") + (password or "") for ch in "\r\n"):
        raise ConfigError(detail="BAROR_SMTP_USER / BAROR_SMTP_PASSWORD must not contain line breaks")
    return MailConfig(host=host, port=port, mode=mode, sender=sender, user=user, password=password)
