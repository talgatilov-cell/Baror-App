#!/usr/bin/env python3
"""Admin CLI for the registration allowlist. Stdlib only. Operator tool: whoever can run it can already read the DB file.

    python scripts/allowlist.py --db /data/baror.db add dana@example.com [--role cadet|instructor] [--platoon NAME]
    python scripts/allowlist.py --db /data/baror.db remove dana@example.com
    python scripts/allowlist.py --db /data/baror.db list [--status pending|used]
    python scripts/allowlist.py --db /data/baror.db import people.csv [--dry-run] [--strict] [--create-platoons] [--delimiter ';']

``--db`` may be replaced by the BAROR_DB environment variable. CSV: UTF-8 (BOM ok), header ``email[,role][,platoon]``,
``platoon`` is a platoon NAME. Idempotent: running the same command/file twice changes nothing the second time.
Removing an entry NEVER deletes an existing account (use scripts/admin_users.py deactivate). Passwords are never read,
stored or printed by this tool; rejected rows show line number, a reason code and an escaped 40-char excerpt only.

Exit codes: 0 success (incl. unchanged / already_registered: re-runs stay 0) | 1 a row was rejected or remove found nothing
            2 usage error (argparse) | 3 input file unreadable or invalid | 4 database error
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baror.errors import BarorError, StorageError, ValidationError  # noqa: E402
from baror.storage import allowlist, db  # noqa: E402

EXIT_OK, EXIT_PARTIAL, EXIT_USAGE, EXIT_INPUT, EXIT_DB = 0, 1, 2, 3, 4


def _platoon_id(db_path, name):
    if name is None:
        return None
    with db.reader(db_path) as c:
        row = c.execute("SELECT id FROM platoons WHERE name=?", (name,)).fetchone()
    if row is None:
        raise ValidationError("INVALID_INPUT", detail=f"unknown platoon {name!r}")
    return row["id"]


def run(args) -> int:
    db_path = args.db or os.environ.get("BAROR_DB")
    if not db_path:
        print("error: --db or BAROR_DB required", file=sys.stderr)
        return EXIT_USAGE
    try:
        db.init_db(db_path)  # idempotent; applies pending migrations
        if args.cmd == "add":
            res = allowlist.add_entry(db_path, args.email, role=args.role, platoon_id=_platoon_id(db_path, args.platoon))
            print(res)
            return EXIT_OK  # 'already_registered' is information, not a failure (keeps re-runs idempotent)
        if args.cmd == "remove":
            removed = allowlist.remove_entry(db_path, args.email)
            print("removed (an existing account, if any, is untouched)" if removed else "not on the list")
            return EXIT_OK if removed else EXIT_PARTIAL
        if args.cmd == "list":
            rows = allowlist.list_entries(db_path, status=args.status)
            for r in rows:
                print(f"{r['status']:<8}{r['role']:<11}{(r['platoon'] or '-'):<16}{r['email']}")
            print(f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}", file=sys.stderr)
            return EXIT_OK
        return _import(args, db_path)
    except ValidationError as exc:
        print(f"error: {exc.detail or exc.message_key}", file=sys.stderr)
        return EXIT_INPUT
    except (StorageError, BarorError) as exc:
        print(f"database error: {exc.detail or exc.code}", file=sys.stderr)
        return EXIT_DB


def _import(args, db_path) -> int:
    try:
        text = Path(args.file).read_bytes().decode("utf-8")  # strict: a wrong encoding is a hard stop, not mojibake
        rows, parse_rejects = allowlist.parse_csv(text, delimiter=args.delimiter)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {args.file}: {type(exc).__name__}", file=sys.stderr)
        return EXIT_INPUT
    except ValidationError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return EXIT_INPUT
    rejects = list(parse_rejects)
    excerpt = {r["line"]: r["email"] for r in rows}
    if args.strict:  # all-or-nothing: rehearse first, apply only if nothing would be rejected
        probe = allowlist.import_rows(db_path, rows, create_platoons=args.create_platoons, dry_run=True)
        if rejects or probe["rejects"]:
            for ln, why, ex in sorted(rejects + [(ln, why, repr(excerpt[ln][:40])) for ln, why in probe["rejects"]]):
                print(f"reject line {ln}: {why} {ex}", file=sys.stderr)
            print("strict: nothing applied", file=sys.stderr)
            return EXIT_PARTIAL
    result = allowlist.import_rows(db_path, rows, create_platoons=args.create_platoons, dry_run=args.dry_run)
    rejects += [(ln, why, repr(excerpt[ln][:40])) for ln, why in result["rejects"]]
    for ln, why, ex in sorted(rejects):
        print(f"reject line {ln}: {why} {ex}", file=sys.stderr)
    cnt = result["counts"]
    print(("DRY RUN - nothing written. " if args.dry_run else "") +
          f"added={cnt['added']} updated={cnt['updated']} unchanged={cnt['unchanged']} "
          f"already_registered={cnt['already_registered']} rejected={len(rejects)}")
    return EXIT_PARTIAL if rejects else EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("email"); a.add_argument("--role", choices=allowlist.ROLES, default="cadet")
    a.add_argument("--platoon")
    r = sub.add_parser("remove"); r.add_argument("email")
    ls = sub.add_parser("list"); ls.add_argument("--status", choices=("pending", "used"))
    im = sub.add_parser("import"); im.add_argument("file", type=Path)
    im.add_argument("--dry-run", action="store_true"); im.add_argument("--strict", action="store_true")
    im.add_argument("--create-platoons", action="store_true"); im.add_argument("--delimiter", default=",")
    try:
        return run(ap.parse_args(argv))
    except SystemExit as e:  # argparse usage error already printed
        return int(e.code) if isinstance(e.code, int) else EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
