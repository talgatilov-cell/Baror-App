#!/usr/bin/env python3
"""Create or upgrade the database at DB_PATH (idempotent). Stdlib plus the baror package; never imports streamlit.

    python scripts/init_db.py

The container runs this BEFORE the web server starts, so the database file exists (and the schema is current) before
the first health check and before the first visitor, and an unusable data directory or a corrupt file stops the
container with one readable line instead of showing up later as an error page. Never overwrites an existing database;
a file that is not a database is left byte-for-byte as it was.

Exit codes: 0 ready | 1 configuration or storage problem (message on stderr, no traceback).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baror import config  # noqa: E402
from baror.errors import BarorError  # noqa: E402
from baror.storage import db  # noqa: E402


def main() -> int:
    try:
        cfg = config.from_env()
        version = db.init_db(cfg.db_path)
    except BarorError as exc:
        print(f"ERROR {exc.code}: {exc.detail or exc.message_key}", file=sys.stderr)
        return 1
    print(f"database ready: schema v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
