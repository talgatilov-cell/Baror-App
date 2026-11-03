#!/usr/bin/env python3
"""One-time, idempotent migration: legacy users.json -> SQLite.  Stdlib only.

    python scripts/migrate_json_to_sqlite.py --json /data/users.json --db /data/baror.db [--rejects-dir /data]

Behaviour (IMPLEMENTATION_BRIEF.md T1.4):
* The original file is never modified or deleted. A byte-identical copy ``users.json.premigration`` is written
  next to it (mode 0400) the first time. Rollback = keep using the original.
* Emails are trimmed + lowercased. Two legacy keys that collapse to the same email are NOT merged: the first
  (file order) is imported, the others go to the rejects file.
* gender: 'זכר'->M, 'נקבה'->F, anything else is rejected (never defaulted).
* Plaintext passwords are hashed with scrypt; every migrated account gets must_change_pw=1.
* run_seconds -> run_tenths (x10). Out-of-range / malformed records are rejected with a reason.
* Re-running imports nothing new: existing users are matched by email, existing identical results are skipped.
* Exit code 0 only if nothing was rejected; 2 if anything was rejected (owner must review the rejects file);
  1 for unreadable input or fatal errors.
* The rejects file contains PII and, for rejected users, the legacy plaintext password is REPLACED by "<redacted>".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baror.auth.passwords import hash_password  # noqa: E402
from baror.errors import BarorError  # noqa: E402
from baror.storage import db, repo  # noqa: E402

GENDER_MAP = {"זכר": "M", "נקבה": "F"}


def _reject(rejects: list, kind: str, key, reason: str, payload=None):
    rejects.append({"kind": kind, "legacy_key": key, "reason": reason, "payload": payload})


def _safe_user_payload(u):
    if isinstance(u, dict):
        out = {k: v for k, v in u.items() if k != "password"}
        out["password"] = "<redacted>"
        out.pop("history", None)
        return out
    return u


def _convert_record(rec):
    """Returns (test_date, run_tenths, pushups) or raises ValueError(reason)."""
    if not isinstance(rec, dict):
        raise ValueError("record is not an object")
    d = rec.get("date")
    try:
        parsed = dt.date.fromisoformat(d)
    except (TypeError, ValueError):
        raise ValueError(f"bad date {d!r}") from None
    if parsed.isoformat() != d:
        raise ValueError(f"non-canonical date {d!r}")
    secs, push = rec.get("run_seconds"), rec.get("pushups")
    if isinstance(secs, bool) or not isinstance(secs, int):
        raise ValueError(f"run_seconds not an integer: {secs!r}")
    if not 1 <= secs <= 7200:
        raise ValueError(f"run_seconds out of range 1..7200: {secs}")
    if push is not None and (isinstance(push, bool) or not isinstance(push, int) or not 0 <= push <= 300):
        raise ValueError(f"pushups invalid: {push!r}")
    return d, secs * 10, push


def migrate(json_path: Path, db_path: Path, rejects_dir: Path) -> dict:
    stats = dict(users_read=0, users_imported=0, users_existing=0, users_rejected=0,
                 records_read=0, records_imported=0, records_existing=0, records_rejected=0,
                 zero_pushup_records=0, over_100_minute_or_99_99_suspects=0)
    rejects: list = []

    try:
        raw = json_path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FATAL: cannot read {json_path}: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(data, dict):
        print("FATAL: top-level JSON is not an object", file=sys.stderr)
        raise SystemExit(1)

    backup = json_path.with_name(json_path.name + ".premigration")
    if not backup.exists():
        shutil.copyfile(json_path, backup)
        os.chmod(backup, stat.S_IRUSR)

    db.init_db(db_path)
    seen_emails: dict[str, str] = {}

    for key, u in data.items():
        stats["users_read"] += 1
        if not isinstance(u, dict):
            stats["users_rejected"] += 1; _reject(rejects, "user", key, "user entry is not an object", _safe_user_payload(u)); continue
        history = u.get("history", [])
        if not isinstance(history, list):
            history = []
            _reject(rejects, "history", key, "history is not a list; ignored")
        try:
            email = repo.normalize_email(key)
        except BarorError:
            stats["users_rejected"] += 1; _reject(rejects, "user", key, "invalid email", _safe_user_payload(u)); continue
        if email in seen_emails:
            stats["users_rejected"] += 1
            _reject(rejects, "user", key, f"collides with legacy key {seen_emails[email]!r} after normalisation (not merged)",
                    _safe_user_payload(u))
            continue
        seen_emails[email] = key
        gender = GENDER_MAP.get(u.get("gender"))
        name, pw = u.get("name"), u.get("password")
        if gender is None:
            reason = f"unknown gender {u.get('gender')!r}"
        elif not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            reason = "invalid name"
        elif not isinstance(pw, str) or not pw:
            reason = "missing password"
        else:
            reason = None
        if reason:
            stats["users_rejected"] += 1; _reject(rejects, "user", key, reason, _safe_user_payload(u)); continue

        existing = repo.get_login_record(db_path, email)
        if existing:
            user_id = existing["id"]; stats["users_existing"] += 1
        else:
            # truncate over-long legacy passwords is NOT done; hash_password bounds length, so reject loudly instead
            try:
                pw_hash = hash_password(pw)
            except ValueError:
                stats["users_rejected"] += 1; _reject(rejects, "user", key, "password length outside 1..128", _safe_user_payload(u)); continue
            user_id = repo.create_user(db_path, email=email, name=name, gender=gender, is_km=bool(u.get("is_km", False)),
                                       pw_hash=pw_hash, must_change_pw=True)
            stats["users_imported"] += 1

        for rec in history:
            stats["records_read"] += 1
            try:
                d, tenths, push = _convert_record(rec)
            except ValueError as exc:
                stats["records_rejected"] += 1; _reject(rejects, "record", key, str(exc), rec); continue
            if push == 0:
                stats["zero_pushup_records"] += 1  # legacy UI saved 0 silently; ambiguous, so reported not dropped
            if tenths > 6000 and not u.get("is_km", False):
                stats["over_100_minute_or_99_99_suspects"] += 1
            if repo.find_duplicate(db_path, user_id, test_date=d, run_tenths=tenths, pushups=push):
                stats["records_existing"] += 1; continue
            repo.add_result(db_path, user_id, test_date=d, run_tenths=tenths, pushups=push)
            stats["records_imported"] += 1

    problems = db.integrity_check(db_path)
    if problems:
        print(f"FATAL: integrity check failed after migration: {problems[:3]}", file=sys.stderr)
        raise SystemExit(1)

    if rejects:
        rejects_dir.mkdir(parents=True, exist_ok=True)
        out = rejects_dir / "migration_rejects.json"
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # never world-readable, not even briefly
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rejects, ensure_ascii=False, indent=2))
        os.chmod(out, stat.S_IRUSR | stat.S_IWUSR)   # also tightens a file left by an earlier run
        stats["rejects_file"] = str(out)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--rejects-dir", type=Path, default=None)
    a = ap.parse_args(argv)
    stats = migrate(a.json, a.db, a.rejects_dir or a.db.parent)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 2 if (stats["users_rejected"] or stats["records_rejected"]) else 0


if __name__ == "__main__":
    sys.exit(main())
