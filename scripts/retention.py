#!/usr/bin/env python3
"""Daily retention job (ADR-0002). Stdlib only. Run from the host's scheduler, e.g. once a day.

    python scripts/retention.py --db /data/baror.db --backup-dir /data/backups [--today YYYY-MM-DD] [--dry-run]

1. De-identifies every cadet whose platoon's course ended more than 6 calendar months ago: their name, e-mail,
   password hash and pseudonym link are deleted; their results stay under the random pseudonym.
2. In the backup directory it deletes
   * every backup older than the retention period (6 months), and
   * when anything was de-identified: every backup that still contains one of the deleted links, or whose schema is
     too old to tell (a backup made before schema v3 holds names and e-mails of everybody),
   then writes one fresh backup so a restore point exists.
Run with --dry-run first after changing a course end date: de-identification cannot be undone.
Prints counts only, never names, e-mails or pseudonyms. Exit codes: 0 ok | 1 error.

NOT reachable by this job (see the runbook): copies outside the data directory (host snapshots, off-host backups,
``<db>.before-restore`` left by a restore, ``users.json.premigration`` from the JSON migration).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup as backup_script  # noqa: E402
from baror.errors import BarorError  # noqa: E402
from baror.storage import retention  # noqa: E402

_DATE_IN_NAME = re.compile(r"^baror-(\d{4})(\d{2})(\d{2})")
_CHUNK = 500


def backup_has_links(path: Path, pseudonyms: list[str]) -> bool:
    """True if the backup still contains any of the deleted links, or if that cannot be ruled out."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if c.execute("PRAGMA user_version").fetchone()[0] < 3:
                return True
            for i in range(0, len(pseudonyms), _CHUNK):
                part = pseudonyms[i:i + _CHUNK]
                marks = ",".join("?" * len(part))
                if c.execute(f"SELECT 1 FROM users WHERE pseudonym IN ({marks}) LIMIT 1", part).fetchone():
                    return True
            return False
        finally:
            c.close()
    except sqlite3.Error:
        return True   # unreadable: cannot prove it is clean, and it could not be restored from anyway


def purge_backups(backup_dir: Path, pseudonyms: list[str], today: dt.date,
                  months: int = retention.RETENTION_MONTHS) -> dict[str, int]:
    cutoff = retention.add_months(today, -months)
    removed = {"age": 0, "link": 0}
    if not backup_dir.is_dir():
        return removed
    for p in sorted(backup_dir.iterdir()):
        if not backup_script.PATTERN.match(p.name):
            continue
        reason = None
        m = _DATE_IN_NAME.match(p.name)
        try:
            made = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except (ValueError, AttributeError):
            made = None
        if made is not None and made < cutoff:
            reason = "age"
        elif pseudonyms and backup_has_links(p, pseudonyms):
            reason = "link"
        if reason:
            p.unlink()
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(p) + suffix).unlink(missing_ok=True)
            removed[reason] += 1
    return removed


def run(db_path: Path, backup_dir: Path | None, today: dt.date | None = None,
        now: dt.datetime | None = None, dry_run: bool = False) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    out = retention.run(db_path, today=today, now=now, dry_run=dry_run)
    if backup_dir is not None and not dry_run:
        # against ALL de-identified pseudonyms, not just this run's: a run that crashed after the database commit
        # must still get its backups cleaned by the next one
        removed = purge_backups(backup_dir, retention.deidentified_pseudonyms(db_path), today)
        out["backups_removed"] = removed
        if out["deidentified"] or removed["link"]:
            backup_script.backup(db_path, backup_dir, now=now)
            out["fresh_backup"] = True
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--backup-dir", type=Path)
    ap.add_argument("--today", type=dt.date.fromisoformat, help="override today's date (tests, dry checks)")
    ap.add_argument("--dry-run", action="store_true", help="only report how many cadets are due; write nothing")
    a = ap.parse_args(argv)
    try:
        print(json.dumps(run(a.db, a.backup_dir, a.today, dry_run=a.dry_run), sort_keys=True))
        return 0
    except (BarorError, sqlite3.Error, OSError) as exc:
        print(f"ERROR: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
