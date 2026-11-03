#!/usr/bin/env python3
"""Daily report job. Stdlib only. Run once a day from the host's scheduler (cron or a systemd timer).

    python scripts/generate_reports.py --db /data/baror.db [--date YYYY-MM-DD] [--since YYYY-MM-DD] [--dry-run]

What it does, for the date it is run (today in Israel time, or --date):
1. Makes the reports that are due that day (baror.domain.reports): the WEEKLY report on Thursdays (Friday..Thursday), the
   MONTHLY report on the last day of the month, or on the Thursday before when that day is a Friday or Saturday (both on
   the same day when a Thursday is the last day of the month). On any other day nothing is due.
2. Stores each in the database (table ``reports``, in the ordinary backup) and lists it on the chief's screen. A report
   that is already stored is NOT made again, so running the job twice in a day is harmless.
3. E-mails the reports to every active chief account, but ONLY while the chief's switch "automatic sending" is ON (it is
   OFF by default; the chief changes it on the screen and the job reads it from the database on every run) and only the
   reports generated after the switch was turned on, never the older ones. Unsent or failed ones are retried (14 days).
   Needs BAROR_SMTP_* (see docs/runbook.md); without them the reports are stored and the job says "mail not configured".

``--since DATE`` catches up after missed days: it goes through every date from DATE to --date (default today) and makes
whatever was due on each (a weekly report made late still covers its own Friday..Thursday, and shows the data as it is
NOW). ``--dry-run`` writes and sends nothing and prints what is due and how many mails are waiting.
Output: one JSON line with counts only (never names or addresses). Exit codes: 0 ok, also when nothing is due, or when
mail is not configured | 1 error (database, schema older than v8) | 2 reports stored, switch ON, but the mail settings are invalid.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baror import config, mailer  # noqa: E402
from baror.domain import reports as schedule  # noqa: E402
from baror.domain.grading import grade_stored_value  # noqa: E402
from baror.errors import BarorError, ConfigError  # noqa: E402
from baror.export_report_pdf import build_report_pdf  # noqa: E402
from baror.logging_setup import configure_logging, log_event  # noqa: E402
from baror.storage import db, reports  # noqa: E402

_MAX_CATCH_UP_DAYS = 400


def _dates(since: dt.date | None, today: dt.date):
    first = since or today
    if first > today or (today - first).days > _MAX_CATCH_UP_DAYS:
        raise ValueError("--since must be on or before the date and at most 400 days back")
    return [first + dt.timedelta(days=i) for i in range((today - first).days + 1)]


def run(db_path, today: dt.date, since: dt.date | None = None, *, env=None, now: dt.datetime | None = None,
        dry_run: bool = False) -> dict:
    if db.user_version(db_path) < 8:
        raise ValueError("the database schema is older than v8: start the app (or run scripts/init_db.py) first")
    out: dict = {"created": 0, "existing": 0, "due": []}
    for day in _dates(since, today):
        if dry_run:
            with db.reader(db_path) as c:
                for kind, start, end in schedule.reports_due(day):
                    exists = c.execute("SELECT 1 FROM reports WHERE period_type=? AND period_start=?",
                                       (kind, start.isoformat())).fetchone() is not None
                    out["due"].append({"kind": kind, "start": start.isoformat(), "end": end.isoformat(), "exists": exists})
                    out["existing" if exists else "created"] += 1
            continue
        for r in reports.generate_due(db_path, day, grade=grade_stored_value, now=now,
                                      render=lambda data, d=day: build_report_pdf(data, generated=d)):
            out["due"].append({"kind": r["kind"], "start": r["start"].isoformat(), "end": r["end"].isoformat(),
                               "exists": r["id"] is None})
            out["existing" if r["id"] is None else "created"] += 1
    invalid = False
    try:
        mail = config.mail_from_env(env)
    except ConfigError:
        mail, invalid = None, True
    out["mail"] = {**mailer.deliver_pending(db_path, mail, now=now, dry_run=dry_run), "invalid": invalid}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--date", type=dt.date.fromisoformat, help="run as if this were today (testing, backfill)")
    ap.add_argument("--since", type=dt.date.fromisoformat, help="also make what was due on every day from here to the date")
    ap.add_argument("--dry-run", action="store_true", help="write and send nothing; print what is due")
    a = ap.parse_args(argv)
    configure_logging(stream=sys.stderr)          # stdout carries only the JSON result line
    tz = zoneinfo.ZoneInfo(os.environ.get("TZ_NAME") or "Asia/Jerusalem")
    today = a.date or dt.datetime.now(tz).date()
    try:
        out = run(a.db, today, a.since, dry_run=a.dry_run)
    except (BarorError, sqlite3.Error, OSError, ValueError) as exc:
        print(f"ERROR: {type(exc).__name__}" + (f": {exc}" if isinstance(exc, ValueError) else ""), file=sys.stderr)
        return 1
    print(json.dumps(out, sort_keys=True))
    if out["mail"]["invalid"] and out["mail"]["enabled"]:
        log_event("report_mail_config_invalid")
        print("ERROR: mail settings are invalid; reports were stored", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
