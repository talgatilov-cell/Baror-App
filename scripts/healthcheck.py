#!/usr/bin/env python3
"""Container HEALTHCHECK (task T8.3). Standard library only; imports nothing from ``baror``.

    python scripts/healthcheck.py          exit 0 = healthy, 1 = unhealthy; ONE line on stdout

Checks, in order, and reports the first failure:
1. Streamlit answers ``http://127.0.0.1:$HEALTH_PORT/_stcore/health`` (default 8501) with 200 and the body ``ok``.
   Proxy environment variables are ignored (a loopback probe must never leave the container).
2. The database at ``$DB_PATH`` (default /data/baror.db) can be opened read-only (``mode=ro``: a missing file is not
   created) and ``PRAGMA quick_check`` says ``ok``. A busy timeout of 2 s makes a locked database unhealthy only if it
   stays locked.

The output never contains a traceback, a path, or database content: only a fixed phrase and, for unexpected errors,
the exception CLASS name.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

HTTP_TIMEOUT_S = 3.0
BUSY_TIMEOUT_MS = 2000
DEFAULT_DB_PATH = "/data/baror.db"


def check_streamlit(port: int | str | None = None, timeout: float = HTTP_TIMEOUT_S) -> str | None:
    port = port or os.environ.get("HEALTH_PORT", "8501")
    try:
        port = int(port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # no proxies for loopback
        with opener.open(f"http://127.0.0.1:{port}/_stcore/health", timeout=timeout) as r:
            body = r.read(64).decode("ascii", "replace").strip()
            if r.status != 200 or body != "ok":
                return f"streamlit health check answered {r.status}"
    except Exception as e:  # noqa: BLE001 - any failure to get a clean "ok" means unhealthy
        return f"streamlit unreachable ({type(e).__name__})"
    return None


def check_db(db_path: str | os.PathLike | None = None, busy_ms: int = BUSY_TIMEOUT_MS) -> str | None:
    db_path = db_path or os.environ.get("DB_PATH", DEFAULT_DB_PATH)
    if not os.path.isfile(db_path):
        return "database file missing"
    conn = None
    try:
        # as_uri() percent-encodes the path: a '?', '#' or '%' in DB_PATH must not add options or select another file
        uri = Path(os.path.abspath(db_path)).as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=busy_ms / 1000)
        conn.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
        rows = conn.execute("PRAGMA quick_check").fetchall()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.OperationalError as e:
        return "database locked" if "locked" in str(e).lower() or "busy" in str(e).lower() else "database cannot be read"
    except sqlite3.DatabaseError:
        return "database is corrupt"
    except Exception as e:  # noqa: BLE001
        return f"database check failed ({type(e).__name__})"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    if [r[0] for r in rows] != ["ok"]:
        return "database quick_check failed"
    if not version:
        return "database not initialised"        # an empty file passes quick_check but is not the app's database
    return None


def main(port: int | str | None = None, db_path: str | os.PathLike | None = None) -> int:
    try:
        reason = check_streamlit(port) or check_db(db_path)
    except Exception as e:  # noqa: BLE001 - a health check must never print a traceback
        reason = f"internal error ({type(e).__name__})"
    print(f"unhealthy: {reason}" if reason else "healthy")
    return 1 if reason else 0


if __name__ == "__main__":
    sys.exit(main())
