#!/usr/bin/env python3
"""Online backup of the SQLite database using sqlite3's backup API (safe while the app is running). Stdlib only.

    python scripts/backup.py --db /data/baror.db --dir /data/backups [--keep 14]
    python scripts/backup.py --restore /data/backups/baror-20261102.db --db /data/baror.db   (app must be stopped)

* Writes ``baror-YYYYMMDD.db`` (UTC date; a second run on the same day writes ``-HHMMSS`` instead of overwriting).
* Written to a temp name, verified with ``PRAGMA integrity_check``, then renamed: a half-written backup never
  carries the final name. Backups are mode 0600 (they contain password hashes and performance data).
* Keeps the newest ``--keep`` backups; only files matching the exact naming pattern are ever deleted.
* Restore keeps the current database as ``<db>.before-restore`` and refuses to restore a backup that fails the check.
  It also switches the automatic e-mailing of reports OFF in the restored file (the chief turns it on again on the
  screen), so a restore can never re-send reports.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

PATTERN = re.compile(r"^baror-\d{8}(-\d{6})?\.db$")


def _check(path: Path) -> list[str]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = [r[0] for r in c.execute("PRAGMA integrity_check").fetchall()]
        fk = c.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        c.close()
    return ([] if rows == ["ok"] else rows) + [f"fk violation {r}" for r in fk]


def backup(db_path: Path, out_dir: Path, keep: int = 14, now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now(dt.timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)   # backups hold password hashes and performance data
    final = out_dir / f"baror-{now:%Y%m%d}.db"
    if final.exists():
        final = out_dir / f"baror-{now:%Y%m%d-%H%M%S}.db"
    tmp = out_dir / (final.name + ".partial")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            # The backup API copies the WAL flag. A backup must be ONE self-contained file: switch the copy to
            # rollback-journal mode so no -wal/-shm sidecars exist (found by tests: they were left behind
            # next to the .partial file). init_db switches it back to WAL after a restore.
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    finally:
        src.close()
    problems = _check(tmp)
    if problems:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed verification: {problems[:3]}")
    os.chmod(tmp, 0o600)
    os.replace(tmp, final)
    mine = sorted((p for p in out_dir.iterdir() if PATTERN.match(p.name)), key=lambda p: p.name)
    for old in mine[:-keep] if keep > 0 else []:
        old.unlink()
    return final


def restore(backup_path: Path, db_path: Path) -> Path | None:
    problems = _check(backup_path)
    if problems:
        raise RuntimeError(f"refusing to restore: backup fails integrity check: {problems[:3]}")
    saved = None
    if db_path.exists():
        saved = db_path.with_name(db_path.name + ".before-restore")
        shutil.copy2(db_path, saved)
    for suffix in ("-wal", "-shm"):  # stale WAL from the old database must not be replayed onto the restored file
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    tmp = db_path.with_name(db_path.name + ".restoring")
    shutil.copyfile(backup_path, tmp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, db_path)
    _switch_report_mail_off(db_path)
    return saved


def _switch_report_mail_off(db_path: Path) -> None:
    """Restored data must not start sending reports by itself. A backup older than schema v8 has no such table: skip."""
    c = sqlite3.connect(db_path)
    try:
        c.execute("UPDATE report_settings SET mail_enabled=0, changed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'), "
                  "changed_by=NULL WHERE mail_enabled=1")
        c.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        c.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--keep", type=int, default=14)
    ap.add_argument("--restore", type=Path)
    a = ap.parse_args(argv)
    try:
        if a.restore:
            saved = restore(a.restore, a.db)
            print(f"restored {a.restore} -> {a.db}; previous database kept at {saved}")
        else:
            if not a.dir:
                ap.error("--dir is required for backup")
            print(f"backup written: {backup(a.db, a.dir, a.keep)}")
        return 0
    except (RuntimeError, sqlite3.Error, OSError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
