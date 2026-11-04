#!/usr/bin/env python3
"""Operator CLI for accounts, roles, titles and platoons. Stdlib only. No actor check (see baror/storage/ops.py).

    python scripts/admin_users.py --db DB list
    python scripts/admin_users.py --db DB set-role EMAIL cadet|instructor|chief|admin [--title mp|smp|mm|cmdr] [--platoon NAME]
                                              # chief needs --title mp|smp; instructor needs --title mm|cmdr and --platoon
                                              # the ONLY way to create an admin (at most 2 active admins)
    python scripts/admin_users.py --db DB set-title EMAIL mp|smp|mm|cmdr       # chief: mp/smp, instructor: mm/cmdr
    python scripts/admin_users.py --db DB deactivate EMAIL      # revokes ALL sessions; login refused afterwards
    python scripts/admin_users.py --db DB reactivate EMAIL
    python scripts/admin_users.py --db DB release-email EMAIL --as ADMIN_EMAIL   # frees a CADET's address (admin only)
    python scripts/admin_users.py --db DB platoon-add NAME
    python scripts/admin_users.py --db DB assign EMAIL PLATOON_NAME|-         # cadet -> platoon ('-' clears)
    python scripts/admin_users.py --db DB grant EMAIL PLATOON_NAME            # an instructor's ONE platoon (moves them)
    python scripts/admin_users.py --db DB course-end PLATOON_NAME YYYY-MM-DD|-   # starts the 6-month retention clock ('-' clears)

Roles are never self-claimed: cadets register openly, an admin or a chief grants instructor / chief afterwards. Limits
(the same as in the app): at most 2 active admins, 2 chiefs (titles mp and smp, one each), 1 מ"מ (mm) and 5 מ"כים (cmdr)
per platoon; an instructor belongs to exactly one platoon. There is no ``revoke``: an instructor always has a platoon, so
move them with ``grant`` or make them a cadet with ``set-role EMAIL cadet``.

Exit codes: 0 ok | 1 target not found / refused (e.g. last admin, a limit) | 2 usage | 4 database error.
Every change writes an audit_log row (actor NULL = operator; ``release-email`` is attributed to the admin named by --as).
Passwords are never handled here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baror.domain import roles as R  # noqa: E402
from baror.errors import AuthError, BarorError, ConflictError, NotFoundError, StorageError, ValidationError  # noqa: E402
from baror.storage import db, ops, people, repo, retention  # noqa: E402


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


def warnings_for(rows) -> list[str]:
    """Structure problems worth an operator's attention (legacy data may exceed the limits; limits apply to new grants)."""
    out = []
    active = [r for r in rows if r["active"]]
    for role, limit in (("admin", R.MAX_ADMINS), ("chief", R.MAX_CHIEFS)):
        n = sum(1 for r in active if r["role"] == role)
        if n > limit:
            out.append(f"{n} active {role} accounts (limit {limit})")
    per = {}
    for r in active:
        if r["role"] == "instructor":
            per.setdefault(r["platoon"] or "(no platoon)", []).append(r["title"])
    for name, titles in sorted(per.items()):
        mm, cmdr = titles.count("mm"), len(titles) - titles.count("mm")      # an untitled instructor counts as a cmdr
        if mm > 1:
            out.append(f"platoon {name}: {mm} active mm (limit 1)")
        if cmdr > R.MAX_CMDRS_PER_PLATOON:
            out.append(f"platoon {name}: {cmdr} active cmdr/untitled instructors (limit {R.MAX_CMDRS_PER_PLATOON})")
    if "(no platoon)" in per:
        out.append(f"{len(per['(no platoon)'])} active instructor(s) without a platoon")
    untitled = sum(1 for r in active if r["role"] in ("chief", "instructor") and r["title"] is None)
    if untitled:
        out.append(f"{untitled} active chief/instructor account(s) with no title set (use set-title)")
    return out


def run(a) -> int:
    p = a.db or os.environ.get("BAROR_DB")
    if not p:
        print("error: --db or BAROR_DB required", file=sys.stderr); return 2
    try:
        db.init_db(p)
        if a.cmd == "list":
            rows = ops.list_accounts(p)
            for r in rows:
                print(f"{r['id']:<5}{r['role']:<11}{(r['title'] or '-'):<6}{'active' if r['active'] else 'INACTIVE':<9}"
                      f"{(r['platoon'] or '-'):<14}{r['email']}")
            for w in warnings_for(rows):
                print(f"WARNING: {w}", file=sys.stderr)
        elif a.cmd == "set-role":
            ops.set_role(p, _uid(p, a.email), a.role, title=a.title,
                         platoon_id=_pid(p, a.platoon) if a.platoon else None)
        elif a.cmd == "set-title":
            ops.set_title(p, _uid(p, a.email), a.title)
        elif a.cmd == "deactivate":
            ops.deactivate_user(p, _uid(p, a.email))
        elif a.cmd == "reactivate":
            ops.reactivate_user(p, _uid(p, a.email))
        elif a.cmd == "release-email":
            people.release_email(p, _uid(p, a.admin), _uid(p, a.email))
        elif a.cmd == "platoon-add":
            print(ops.create_platoon(p, a.name))
        elif a.cmd == "assign":
            ops.set_user_platoon(p, _uid(p, a.email), None if a.platoon == "-" else _pid(p, a.platoon))
        elif a.cmd == "grant":
            ops.grant_platoon(p, _uid(p, a.email), _pid(p, a.platoon))
        elif a.cmd == "course-end":
            retention.set_course_end(p, _pid(p, a.platoon), None if a.date == "-" else a.date)
            if a.date != "-":
                due = retention.deadline(a.date)
                print(f"retention deadline for this platoon: {due.isoformat()}")
                if due <= dt.date.today():
                    print("WARNING: that deadline has already passed; the next retention run will de-identify this "
                          "platoon's cadets permanently. Check with: scripts/retention.py --dry-run", file=sys.stderr)
        return 0
    except (NotFoundError, ConflictError, ValidationError, AuthError) as exc:
        print(f"refused: {exc.detail or exc.message_key}", file=sys.stderr); return 1
    except (StorageError, BarorError) as exc:
        print(f"database error: {exc.detail or exc.code}", file=sys.stderr); return 4


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sp = sub.add_parser("set-role")
    sp.add_argument("email")
    sp.add_argument("role", choices=R.ROLES)
    sp.add_argument("--title", choices=R.ALL_TITLES)
    sp.add_argument("--platoon", help="platoon name (required for an instructor)")
    sp = sub.add_parser("set-title")
    sp.add_argument("email")
    sp.add_argument("title", choices=R.ALL_TITLES)
    sp = sub.add_parser("release-email")
    sp.add_argument("email")
    sp.add_argument("--as", dest="admin", required=True, metavar="ADMIN_EMAIL",
                    help="the active admin the release is attributed to in the audit log")
    for name, args in (("deactivate", ("email",)), ("reactivate", ("email",)),
                       ("platoon-add", ("name",)), ("assign", ("email", "platoon")), ("grant", ("email", "platoon")),
                       ("course-end", ("platoon", "date"))):
        sp = sub.add_parser(name)
        for x in args:
            sp.add_argument(x)
    try:
        return run(ap.parse_args(argv))
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2


if __name__ == "__main__":
    sys.exit(main())
