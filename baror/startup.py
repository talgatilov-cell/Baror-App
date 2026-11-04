"""Start-up self-checks (task T8.3). Standard library plus ``baror.storage.db``; never imports streamlit or
``baror.config`` (the config object is duck-typed: it needs ``.db_path`` and ``.tz_name``).

``run_startup_checks(cfg)`` raises, with an operator-facing English ``detail`` (it goes to the log / the error page's
incident id, never to cadets, who see the Hebrew text of the error class):

* ``ConfigError``       deployment problems: time-zone data missing, data directory missing or not writable by this
                        uid (a REAL write test; ``os.access`` lies on read-only mounts and full disks), disk almost full;
* ``CorruptedDataError`` the database file exists but is not a database (never modified: see ``db.init_db``);
* ``StorageError``      integrity check failed, schema newer than the app, or not at the latest version.

Problems that can be listed together are reported together in one ``ConfigError``.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zoneinfo
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from baror.errors import ConfigError, StorageError
from baror.storage import db

log = logging.getLogger("baror.startup")
MIN_FREE_BYTES = 10 * 1024 * 1024
MAINTENANCE_FLAG = "MAINTENANCE"


@dataclass(frozen=True)
class Problem:
    code: str          # machine-readable, for logs
    what: str          # operator-facing English
    fix: str


def check_timezone(tz_name: str) -> list[Problem]:
    try:
        zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return [Problem("TZ_MISSING", f"time zone {tz_name!r} cannot be loaded",
                        "install the tzdata package (it is in requirements.lock) or fix TZ_NAME")]
    return []


def check_data_dir(data_dir: str | os.PathLike, min_free: int = MIN_FREE_BYTES) -> list[Problem]:
    d = Path(data_dir)
    uid = os.geteuid() if hasattr(os, "geteuid") else "?"
    if not d.is_dir():
        return [Problem("DATA_DIR_MISSING", f"data directory {d} does not exist",
                        f"mount a volume at {d} (compose: volumes: - baror_data:{d})")]
    try:   # a real write: os.access() lies on read-only mounts, full disks and some network file systems
        fd, name = tempfile.mkstemp(prefix=".startup-check-", dir=d)
        try:
            os.write(fd, b"ok")
            os.fsync(fd)
        finally:
            os.close(fd)
            os.unlink(name)
    except OSError as e:
        return [Problem("DATA_DIR_NOT_WRITABLE", f"{d} is not writable by uid {uid}: {e.strerror or type(e).__name__}",
                        "use a named volume (it inherits the image's ownership) or chown the host path to the app uid")]
    try:
        free = shutil.disk_usage(d).free
    except OSError:
        return []
    if free < min_free:
        return [Problem("DISK_LOW", f"only {free // 1024} KiB free in {d}", "free disk space or enlarge the volume")]
    return []


def collect_problems(cfg: Any) -> list[Problem]:
    """Everything that can be checked without touching the database file."""
    problems = check_timezone(cfg.tz_name)
    problems += check_data_dir(Path(cfg.db_path).parent)
    return problems


def run_startup_checks(cfg: Any, *, init: bool = True) -> dict[str, Any]:
    """Raise a readable error if the app cannot run safely; otherwise return ``{"schema": <version>}``.

    ``init=True`` (default) creates a missing database or upgrades an older one with ``db.init_db`` after the
    directory and integrity checks, which replaces the separate ``init_db`` call in ``app.py``. A file that is not a database is
    reported as ``CorruptedDataError`` and left byte-for-byte untouched. ``init=False`` only inspects: a missing file is
    fine (first run), an older schema is an error.
    """
    problems = collect_problems(cfg)
    if problems:
        raise ConfigError(detail="; ".join(f"{p.code}: {p.what} -> {p.fix}" for p in problems))
    path = Path(cfg.db_path)
    if path.exists():
        # Look BEFORE touching: migrating (writing to) a damaged file could make it worse. A file that is not a
        # database raises CorruptedDataError here and stays byte-for-byte as it was.
        bad = db.integrity_check(path)
        if bad:
            raise StorageError(detail=f"integrity check failed ({len(bad)} problem(s)), first: {bad[0][:80]}")
    elif not init:
        return {"schema": None}
    if init:
        db.init_db(path)
    version = db.user_version(path)
    if version != db.LATEST_VERSION:
        raise StorageError(detail=f"database schema is v{version}, this app needs v{db.LATEST_VERSION}")
    log.info("registration_state %s", registration_state(cfg))
    return {"schema": version}


def registration_state(cfg: Any) -> dict[str, Any]:
    """Operator summary of the registration settings, safe to log: booleans and the cap, never the join-link secret or
    the public address. Missing attributes (a duck-typed config in tests) read as the defaults."""
    return {"open": bool(getattr(cfg, "registration_open", True)),
            "signup_cap_per_hour": getattr(cfg, "signup_cap_per_hour", None),
            "join_links": getattr(cfg, "join_link_secret", None) is not None,
            "public_url_set": getattr(cfg, "public_url", None) is not None}


def maintenance_active(db_path: str | os.PathLike, env: dict | None = None) -> bool:
    """True if ``MAINTENANCE=1`` is set or the flag file ``<data dir>/MAINTENANCE`` exists (toggle without a restart:
    ``docker compose exec baror touch /data/MAINTENANCE``). The caller raises ``MaintenanceError``."""
    env = os.environ if env is None else env
    if str(env.get("MAINTENANCE", "")).strip().lower() in ("1", "true", "yes"):
        return True
    return (Path(db_path).parent / MAINTENANCE_FLAG).exists()
