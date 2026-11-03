#!/usr/bin/env python3
"""Operator CLI for accommodations (הקלות ופטורים). Stdlib only. Operator tool: whoever can run it can already read the DB file.

    python scripts/accommodations.py --db /data/baror.db list
    python scripts/accommodations.py --db /data/baror.db add dana@example.com --kind relief|exemption \\
        --scope run|pushups|both --from 2026-11-01 --to 2027-01-31 [--note "short plain text"]
    python scripts/accommodations.py --db /data/baror.db delete 12

``--db`` may be replaced by the BAROR_DB environment variable. A record added here has the source 'admin'. ``delete``
bypasses the request/approve flow of the app (the operator owns the file); it is audited. The note is plain text: letters,
digits, spaces and . , - ( ) ' (up to 100 characters), the same rule the app applies.

Exit codes: 0 success | 1 nothing found (unknown cadet or record) | 2 usage error (argparse) | 3 invalid value | 4 database error
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baror.domain import accommodation as rules  # noqa: E402
from baror.errors import BarorError, NotFoundError, StorageError, ValidationError  # noqa: E402
from baror.storage import db, ops, repo  # noqa: E402

EXIT_OK, EXIT_NOT_FOUND, EXIT_USAGE, EXIT_INVALID, EXIT_DB = 0, 1, 2, 3, 4


def run(args) -> int:
    db_path = args.db or os.environ.get("BAROR_DB")
    if not db_path:
        print("error: --db or BAROR_DB required", file=sys.stderr)
        return EXIT_USAGE
    try:
        db.init_db(db_path)
        if args.cmd == "list":
            for r in ops.list_accommodations(db_path):
                pending = " (deletion requested)" if r["delete_requested"] else ""
                print(f"{r['id']:<5}{r['cadet_name']:<24}{(r['platoon'] or '-'):<12}{r['kind']:<11}{r['scope']:<9}"
                      f"{r['start_date']} .. {r['end_date']}  {r['source']}{pending}  {r['note'] or ''}")
            return EXIT_OK
        if args.cmd == "add":
            try:
                email = repo.normalize_email(args.email)
            except ValidationError:
                print("error: not an e-mail address", file=sys.stderr)
                return EXIT_INVALID
            with db.reader(db_path) as c:
                row = c.execute("SELECT id FROM users WHERE email=? AND role='cadet'", (email,)).fetchone()
            if row is None:
                print("no cadet with that e-mail address", file=sys.stderr)
                return EXIT_NOT_FOUND
            new_id = ops.add_accommodation(db_path, row["id"], kind=args.kind, scope=args.scope, start_date=args.start,
                                           end_date=args.end, note=args.note)
            print(f"added {new_id}")
            return EXIT_OK
        if args.cmd == "delete":
            if ops.delete_accommodation(db_path, args.id):
                print("deleted")
                return EXIT_OK
            print("no such record", file=sys.stderr)
            return EXIT_NOT_FOUND
    except ValidationError as exc:
        print(f"invalid value ({exc.message_key}): {exc.field or ''}", file=sys.stderr)
        return EXIT_INVALID
    except NotFoundError:
        print("no such cadet or record", file=sys.stderr)
        return EXIT_NOT_FOUND
    except (StorageError, BarorError) as exc:
        print(f"database error: {type(exc).__name__}", file=sys.stderr)
        return EXIT_DB
    return EXIT_USAGE


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Accommodations (operator tool)")
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("email")
    a.add_argument("--kind", required=True, choices=rules.KINDS)
    a.add_argument("--scope", required=True, choices=rules.SCOPES)
    a.add_argument("--from", dest="start", required=True, metavar="YYYY-MM-DD")
    a.add_argument("--to", dest="end", required=True, metavar="YYYY-MM-DD")
    a.add_argument("--note")
    d = sub.add_parser("delete")
    d.add_argument("id", type=int)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
