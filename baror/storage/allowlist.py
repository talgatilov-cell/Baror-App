"""Registration allowlist (schema v2 ``allowed_emails``). Stdlib only; never imports streamlit.

TRUSTED-CALLER module: functions take a db_path and perform NO actor authorisation. The web app must reach them only
through ``baror.storage.admin`` (admin-checked) or ``repo.register_with_allowlist`` (the registration flow);
``scripts/allowlist.py`` is the operator path. Semantics (each covered by tests/test_allowlist.py):

* Entries are normalised e-mails (``repo.normalize_email``) with an optional intended role (cadet/instructor; NEVER
  admin, see research_notes/allowlist_and_cohort.md section 2.3) and an optional platoon (cadets only).
* An entry is CONSUMED, not deleted, by registration (``used_by``/``used_at``), so there is a record of what happened.
* Removing an entry never touches an existing account. To cut access use ``admin.deactivate_user``.
* ``add`` is idempotent: re-adding an identical pending entry changes nothing; an e-mail that already has an account
  is reported as REGISTERED and not added; an entry consumed by an account that was since deleted is re-armed.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from baror.errors import ValidationError
from baror.storage import db, repo

ADDED, UPDATED, UNCHANGED, REGISTERED = "added", "updated", "unchanged", "already_registered"
ROLES = ("cadet", "instructor")
MAX_CSV_BYTES = 1_000_000
MAX_CSV_ROWS = 5000


def add_in_tx(c, email: str, *, role: str = "cadet", platoon_id: int | None = None,
              added_by: int | None = None, source: str = "cli", now_ts: str | None = None) -> str:
    e = repo.normalize_email(email)
    if role not in ROLES:
        raise ValidationError("INVALID_INPUT", field="role", detail=f"role {role!r} not allowed on the allowlist")
    if platoon_id is not None:
        if role != "cadet":
            raise ValidationError("INVALID_INPUT", field="platoon", detail="only cadet entries carry a platoon")
        if c.execute("SELECT 1 FROM platoons WHERE id=?", (platoon_id,)).fetchone() is None:
            raise ValidationError("INVALID_INPUT", field="platoon", detail=f"unknown platoon {platoon_id}")
    if c.execute("SELECT 1 FROM users WHERE email=?", (e,)).fetchone():
        return REGISTERED
    row = c.execute("SELECT role,platoon_id,used_at FROM allowed_emails WHERE email=?", (e,)).fetchone()
    if row is None:
        c.execute("INSERT INTO allowed_emails(email,role,platoon_id,added_by,source) VALUES(?,?,?,?,?)",
                  (e, role, platoon_id, added_by, source))
        return ADDED
    if row["used_at"] is not None:  # consumed by an account that no longer exists (else REGISTERED above): re-arm
        c.execute("UPDATE allowed_emails SET role=?,platoon_id=?,added_by=?,source=?,added_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'),"
                  "used_by=NULL,used_at=NULL WHERE email=?", (role, platoon_id, added_by, source, e))
        return ADDED
    if (row["role"], row["platoon_id"]) == (role, platoon_id):
        return UNCHANGED
    c.execute("UPDATE allowed_emails SET role=?,platoon_id=? WHERE email=?", (role, platoon_id, e))
    return UPDATED


def remove_in_tx(c, email: str) -> bool:
    """True if a row was deleted. An existing account is NOT touched."""
    return c.execute("DELETE FROM allowed_emails WHERE email=?", (repo.normalize_email(email),)).rowcount == 1


def list_in_tx(c, *, status: str | None = None) -> list[dict[str, Any]]:
    rows = c.execute(
        "SELECT a.email, a.role, a.platoon_id, p.name AS platoon, a.source, a.added_at, a.used_at, "
        "CASE WHEN a.used_at IS NULL THEN 'pending' ELSE 'used' END AS status "
        "FROM allowed_emails a LEFT JOIN platoons p ON p.id=a.platoon_id ORDER BY a.email").fetchall()
    out = [dict(r) for r in rows]
    return [r for r in out if status is None or r["status"] == status]


def add_entry(db_path, email, **kw) -> str:
    with db.transaction(db_path) as c:
        return add_in_tx(c, email, **kw)


def remove_entry(db_path, email) -> bool:
    with db.transaction(db_path) as c:
        return remove_in_tx(c, email)


def list_entries(db_path, *, status: str | None = None) -> list[dict[str, Any]]:
    with db.reader(db_path) as c:
        return list_in_tx(c, status=status)


# ---------------------------------------------------------------- CSV
def parse_csv(text: str, *, delimiter: str = ",") -> tuple[list[dict[str, Any]], list[tuple[int, str, str]]]:
    """Parse and VALIDATE import text. Returns (rows, rejects); a reject is (line_number, reason_code, safe_excerpt).

    Header (case-insensitive) must contain ``email``; optional ``role`` (default cadet) and ``platoon`` (a platoon
    NAME, resolved later). Duplicate e-mails inside the file: the first wins, later ones are rejected. Excerpts are
    ``repr``-escaped and truncated so control characters / terminal escape sequences in a hostile file cannot reach
    the admin's terminal. Raises ValidationError if the text is not usable at all (no header, too big, too long).
    """
    if len(text.encode("utf-8", "surrogatepass")) > MAX_CSV_BYTES:
        raise ValidationError("INVALID_INPUT", detail="csv too large")
    if text.startswith("﻿"):  # Excel's "CSV UTF-8" adds a BOM
        text = text[1:]
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    rows: list[dict[str, Any]] = []
    rejects: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    header: list[str] | None = None
    try:
        for rec in reader:
            line = reader.line_num
            if header is None:
                if not any(cell.strip() for cell in rec):
                    continue
                header = [h.strip().lower() for h in rec]
                if "email" not in header or len(set(header)) != len(header) or set(header) - {"email", "role", "platoon"}:
                    raise ValidationError("INVALID_INPUT", detail="csv header must be email[,role][,platoon]")
                continue
            if not any(cell.strip() for cell in rec):
                continue
            if len(rows) + len(rejects) >= MAX_CSV_ROWS:
                raise ValidationError("INVALID_INPUT", detail=f"more than {MAX_CSV_ROWS} rows")
            excerpt = repr(rec[0].strip()[:40]) if rec else "''"
            if len(rec) != len(header):
                rejects.append((line, "column_count", excerpt)); continue
            d = dict(zip(header, (cell.strip() for cell in rec)))
            try:
                email = repo.normalize_email(d["email"])
            except ValidationError:
                rejects.append((line, "invalid_email", excerpt)); continue
            role = (d.get("role") or "cadet").lower()
            platoon = d.get("platoon") or None
            if role not in ROLES:
                rejects.append((line, "invalid_role", excerpt)); continue
            if platoon is not None and (role != "cadet" or len(platoon) > 60):
                rejects.append((line, "invalid_platoon", excerpt)); continue
            if email in seen:
                rejects.append((line, "duplicate_in_file", excerpt)); continue
            seen.add(email)
            rows.append({"line": line, "email": email, "role": role, "platoon": platoon})
    except csv.Error as exc:
        raise ValidationError("INVALID_INPUT", detail=f"csv syntax: {exc}") from exc
    if header is None:
        raise ValidationError("INVALID_INPUT", detail="csv has no header")
    return rows, rejects


def import_rows(db_path, rows: list[dict[str, Any]], *, create_platoons: bool = False, dry_run: bool = False,
                source: str = "import", added_by: int | None = None) -> dict[str, Any]:
    """Apply parsed rows in ONE transaction (all valid rows land together or none do). Unknown platoon names are
    rejected unless ``create_platoons``. Returns {"counts": {...}, "rejects": [(line, reason)]}.
    ``dry_run`` rolls the transaction back after computing the same report."""
    counts = {ADDED: 0, UPDATED: 0, UNCHANGED: 0, REGISTERED: 0}
    rejects: list[tuple[int, str]] = []

    class _DryRun(Exception):
        pass

    try:
        with db.transaction(db_path) as c:
            for r in rows:
                pid = None
                if r["platoon"] is not None:
                    got = c.execute("SELECT id FROM platoons WHERE name=?", (r["platoon"],)).fetchone()
                    if got is None and create_platoons:
                        pid = c.execute("INSERT INTO platoons(name) VALUES(?)", (r["platoon"],)).lastrowid
                    elif got is None:
                        rejects.append((r["line"], "unknown_platoon")); continue
                    else:
                        pid = got["id"]
                counts[add_in_tx(c, r["email"], role=r["role"], platoon_id=pid, added_by=added_by, source=source)] += 1
            if dry_run:
                raise _DryRun()
    except _DryRun:
        pass
    return {"counts": counts, "rejects": rejects}
