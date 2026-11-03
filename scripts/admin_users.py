#!/usr/bin/env python3
"""Operator CLI for accounts, roles, platoons and instructor grants. Stdlib only. No actor check (see baror/storage/ops.py).

    python scripts/admin_users.py --db DB list
    python scripts/admin_users.py --db DB set-role EMAIL cadet|instructor|admin      # first admin is created this way
    python scripts/admin_users.py --db DB deactivate EMAIL      # revokes ALL sessions; login refused afterwards
    python scripts/admin_users.py --db DB reactivate EMAIL
    python scripts/admin_users.py --db DB platoon-add NAME
    python scripts/admin_users.py --db DB assign EMAIL PLATOON_NAME|-         # cadet -> platoon ('-' clears)
    python scripts/admin_users.py --db DB grant EMAIL PLATOON_NAME            # instructor may READ this platoon
    python scripts/admin_users.py --db DB revoke EMAIL PLATOON_NAME

Exit codes: 0 ok | 1 target not found / refused (e.g. last admin) | 2 usage | 4 database error.
Every change writes an audit_log row (actor NULL = operator). Passwords are never handled here.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baror.errors import BarorError, ConflictError, NotFoundError, StorageError, ValidationError  # noqa: E402
from baror.storage import db, ops, repo  # noqa: E402


def _uid(db_path, email):
    rec = repo.get_login_record(db_path, email)
    if rec is None:
        raise NotFoundError(detail="no such account")
    return rec["id"]


def _pid(db_path, name):
    with db.reader(db_path) as c:
        row = c.execute("SELECT id FROM platoons WHERE name=?", (name,)).fetchone()
    if row is None:
        raise NotFoundError(detail="no such platoon")
    return row["id"]


def run(a) -> int:
    p = a.db or os.environ.get("BAROR_DB")
    if not p:
        print("error: --db or BAROR_DB required", file=sys.stderr); return 2
    try:
        db.init_db(p)
        if a.cmd == "list":
            for r in ops.list_accounts(p):
                print(f"{r['id']:<5}{r['role']:<11}{'active' if r['active'] else 'INACTIVE':<9}{(r['platoon'] or '-'):<14}{r['email']}")
        elif a.cmd == "set-role":
            ops.set_role(p, _uid(p, a.email), a.role)
        elif a.cmd == "deactivate":
            ops.deactivate_user(p, _uid(p, a.email))
        elif a.cmd == "reactivate":
            ops.reactivate_user(p, _uid(p, a.email))
        elif a.cmd == "platoon-add":
            print(ops.create_platoon(p, a.name))
        elif a.cmd == "assign":
            ops.set_user_platoon(p, _uid(p, a.email), None if a.platoon == "-" else _pid(p, a.platoon))
        elif a.cmd == "grant":
            ops.grant_platoon(p, _uid(p, a.email), _pid(p, a.platoon))
        elif a.cmd == "revoke":
            ops.revoke_platoon(p, _uid(p, a.email), _pid(p, a.platoon))
        return 0
    except (NotFoundError, ConflictError, ValidationError) as exc:
        print(f"refused: {exc.detail or exc.message_key}", file=sys.stderr); return 1
    except (StorageError, BarorError) as exc:
        print(f"database error: {exc.detail or exc.code}", file=sys.stderr); return 4


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name, args in (("set-role", ("email", "role")), ("deactivate", ("email",)), ("reactivate", ("email",)),
                       ("platoon-add", ("name",)), ("assign", ("email", "platoon")), ("grant", ("email", "platoon")),
                       ("revoke", ("email", "platoon"))):
        sp = sub.add_parser(name)
        for x in args:
            sp.add_argument(x)
    try:
        return run(ap.parse_args(argv))
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2


if __name__ == "__main__":
    sys.exit(main())
