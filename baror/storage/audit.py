"""Audit trail (schema v2 ``audit_log``). Stdlib only; never imports streamlit.

* Rows hold ids only (actor, target user, platoon) plus an action code and a timestamp: no e-mail, no name, no
  result values. Names are joined in at read time and only for the disclosure shown to the data subject.
* ``record`` takes the CALLER'S connection so the audit row commits or rolls back together with the operation it
  describes (a read cannot happen without its row, and vice versa).
* FKs are ON DELETE SET NULL: deleting an account never blocks on, or silently re-labels, history. (Plain integer ids
  without an FK would be dangerous: SQLite may reuse the highest rowid after a delete, which would attribute old
  rows to a new person.)
* The module offers no UPDATE/DELETE other than ``purge`` (age-based retention). DB-level append-only enforcement
  (triggers) is NOT implemented: anyone with write access to the file can alter it. UNVERIFIED whether that is enough
  for the owner's compliance needs.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from baror.errors import ValidationError
from baror.storage import db

FMT = "%Y-%m-%dT%H:%M:%SZ"
# Rows older than this are deleted by ``purge``. 365 days = one training year. A number the OWNER must confirm;
# any legal retention requirement is UNVERIFIED and is not legal advice.
RETENTION_DAYS = 365
# Actions that count as "somebody read this cadet's performance data" in the data-subject disclosure.
DATA_READ_ACTIONS = ("cohort.summary", "cohort.cadet", "chief.cadet", "chief.summary")


def ts(now: dt.datetime | None = None) -> str:
    t = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0)
    return t.strftime(FMT)


def record(c, *, actor_id: int | None, action: str, target_user_id: int | None = None,
           platoon_id: int | None = None, outcome: str = "ok", now: dt.datetime | None = None) -> None:
    """Insert one row on the caller's connection. Ids that do not exist are stored as NULL (never an FK error),
    so recording a DENIED probe of a non-existent id cannot itself leak existence through an exception."""
    c.execute(
        "INSERT INTO audit_log(ts,actor_id,action,target_user_id,platoon_id,outcome) VALUES("
        "?, (SELECT id FROM users WHERE id=?), ?, (SELECT id FROM users WHERE id=?),"
        " (SELECT id FROM platoons WHERE id=?), ?)",
        (ts(now), actor_id, action, target_user_id, platoon_id, outcome))


def purge(db_path, *, older_than_days: int = RETENTION_DAYS, now: dt.datetime | None = None) -> int:
    """Delete rows older than ``older_than_days`` (at least 1, to make 'delete everything' impossible by typo).
    Leaves one ``audit.purge`` row (count only) so a purge is itself visible. Returns the number deleted."""
    if isinstance(older_than_days, bool) or not isinstance(older_than_days, int) or older_than_days < 1:
        raise ValidationError("INVALID_INPUT", detail="older_than_days must be an int >= 1")
    cutoff = ts((now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=older_than_days))
    with db.transaction(db_path) as c:
        n = c.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,)).rowcount
        record(c, actor_id=None, action="audit.purge", now=now)
        return n


def reads_of_user(c, user_id: int, *, since_days: int = RETENTION_DAYS,
                  now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Disclosure for the data subject: per (UTC date, reader) how many times their results were read.

    Minimal on purpose: date (no time of day), reader's display name (None if that account no longer exists) and a
    count. No platoon, no action detail, no denied attempts, no admin actions. Only counts reads that succeeded.
    """
    since = ts((now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=since_days))
    marks = ",".join("?" * len(DATA_READ_ACTIONS))
    rows = c.execute(
        f"SELECT substr(l.ts,1,10) AS day, u.name AS reader, count(*) AS reads FROM audit_log l "
        f"LEFT JOIN users u ON u.id=l.actor_id WHERE l.target_user_id=? AND l.outcome='ok' AND l.ts>=? "
        f"AND l.action IN ({marks}) GROUP BY day, l.actor_id ORDER BY day DESC, reader",  # marks are '?' literals only
        (user_id, since, *DATA_READ_ACTIONS)).fetchall()
    return [{"date": r["day"], "reader": r["reader"], "reads": r["reads"]} for r in rows]
