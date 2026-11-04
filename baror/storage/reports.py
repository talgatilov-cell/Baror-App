"""The aggregated period reports: computing them, storing them, and the e-mail delivery bookkeeping. Stdlib only; never
imports streamlit. This is the TRUSTED OPERATOR path (run by ``scripts/generate_reports.py``, like ``retention``): it has no
acting user. What the chief may read afterwards goes through ``baror.storage.chief`` (``list_reports`` / ``get_report``),
which is chief-only and audited per call.

THE SMALL-GROUP RULE (``MIN_GROUP`` = 5, same as the chief dashboard) AND COMPLEMENTARY DISCLOSURE
Every number in a report belongs to a GROUP of cadets. A group of fewer than 5 gets no number at all (not even 0): the
value is ``None`` or ``{'suppressed': True}``.
* Platoon with fewer than 5 active cadets: the whole platoon is hidden (no counts, no levels, nothing).
* "Tested" and "results": shown only when at least 5 cadets of the platoon tested. A test block (run / walk / push-ups) is
  shown only when at least 5 cadets have a graded value for it (so counts per level, average, best and change are shown
  or hidden together). An accommodation cell (kind x scope) counts cadets and is shown only when it is at least 5.
* THE COURSE TOTALS ADD UP SHOWN GROUPS ONLY. If a total counted a hidden platoon, ``total - sum of shown platoons``
  would be the hidden platoon. So each total is the sum of the shown parts of exactly that figure, and there is no figure
  for "everyone". The report says so in its notes. The "no platoon" pseudo-group is treated as a platoon.
* CHANGE since the previous period of the same type: shown only when the group has at least 5 graded cadets in BOTH periods.
  For the course total the change is computed over the platoons shown in both periods (a platoon that appears or disappears
  between two periods cannot leak through the difference of two totals).
* No per-kind or per-scope totals for accommodations (only cells), so cells cannot be recovered by subtraction.
* RESIDUAL, accepted and documented: with a platoon's cadet count and tested count both shown, a reader can subtract to
  get the number who did not test. That is not a hidden group and no result, level or value of anybody is attached to it
  (the chief dashboard has the same property); it is listed in docs/runbook.md for the owner to reverse.

WHICH RESULT COUNTS: for every active cadet and every test, the LATEST result inside the period (by test date, then by
entry order) with a value for that test. Counts per level, average, best and change all use that one value. "Results" is
the plain number of result rows entered with a test date inside the period. Only ACTIVE cadets count (the same cadets as
``chief.platoon_summary``): deactivated, deleted and de-identified accounts are not in the report.
ACCOMMODATIONS: approved records that were in effect on at least one day of the period (up to today for a period that has
not ended); delete-requested ones still count until they are deleted.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Callable

from baror.domain import reports as schedule
from baror.storage import audit, chief, db
from baror.storage.chief import MIN_GROUP

__all__ = ["compute", "generate", "generate_due", "adhoc_report", "pending_deliveries", "report_pdf", "mark_sent", "mark_failed",
           "mail_state", "set_mail_enabled", "RETRY_DAYS"]

RETRY_DAYS = 14          # a report older than this is no longer retried by e-mail (it stays on the chief's screen)
_LEVELS = ("excellent", "pass", "fail")
_METRICS = ("run", "walk", "pushups")
_KINDS = ("relief", "exemption")
_SCOPES = ("run", "pushups", "both")
_CODE = re.compile(r"^[a-z_0-9]{1,40}$")
Grader = Callable[[str, str, str, int], "str | None"]
Renderer = Callable[[dict], bytes]


# ------------------------------------------------------------------------------------------------------ computation
def _block() -> dict[str, Any]:
    return {"n": 0, "excellent": 0, "pass": 0, "fail": 0, "sum": 0, "best": None}


def _group() -> dict[str, Any]:
    return {"members": 0, "tested": 0, "results": 0, "blocks": {m: _block() for m in _METRICS},
            "acc": {(k, s): set() for k in _KINDS for s in _SCOPES}}


def _latest_per_cadet(c, by_pseudonym, start: dt.date, end: dt.date) -> dict[str, dict[str, Any]]:
    per: dict[str, dict[str, Any]] = {}
    for r in c.execute("SELECT pseudonym,test_date,run_tenths,pushups FROM results WHERE test_date BETWEEN ? AND ? "
                       "ORDER BY test_date DESC, id DESC", (start.isoformat(), end.isoformat())):
        if r["pseudonym"] not in by_pseudonym:
            continue
        p = per.setdefault(r["pseudonym"], {"rows": 0, "run": None, "pushups": None})
        p["rows"] += 1
        if p["run"] is None and r["run_tenths"] is not None:
            p["run"] = (r["test_date"], r["run_tenths"])
        if p["pushups"] is None and r["pushups"] is not None:
            p["pushups"] = (r["test_date"], r["pushups"])
    return per


def _groups(cadets, per, grade: Grader) -> dict[Any, dict[str, Any]]:
    groups: dict[Any, dict[str, Any]] = {}
    for cad in cadets:
        g = groups.setdefault(cad["platoon_id"], _group())
        g["members"] += 1
        p = per.get(cad["pseudonym"])
        if p is None:
            continue
        g["tested"] += 1
        g["results"] += p["rows"]
        aerobic = "walk" if cad["is_km"] else "run"
        for metric, key in ((aerobic, "run"), ("pushups", "pushups")):
            if p[key] is None:
                continue
            level = grade(metric, cad["gender"], p[key][0], p[key][1])
            if level not in _LEVELS:                       # not gradable: not counted anywhere
                continue
            b = g["blocks"][metric]
            b["n"] += 1
            b[level] += 1
            b["sum"] += p[key][1]
            better = min if metric != "pushups" else max
            b["best"] = p[key][1] if b["best"] is None else better(b["best"], p[key][1])
    return groups


def _mean(metric: str, total: float, n: int):
    """Average as stored units: tenths of a second (int) for the aerobic tests, repetitions with one decimal."""
    return round(total / n) if metric != "pushups" else round(total / n, 1)


def _public_block(metric: str, cur: dict, prev: dict | None, min_group: int) -> dict[str, Any]:
    if cur["n"] < min_group:
        return {"suppressed": True}
    out = {"suppressed": False, "n": cur["n"], **{lv: cur[lv] for lv in _LEVELS}, "avg": _mean(metric, cur["sum"], cur["n"]),
           "best": cur["best"], "change": None}
    if prev is not None and prev["n"] >= min_group:        # both groups at least min_group, else the change is omitted
        out["change"] = _mean(metric, cur["sum"] / cur["n"] - prev["sum"] / prev["n"], 1)
    return out


def _merge(into: dict, other: dict, metric: str) -> None:
    for k in ("n", "sum", *_LEVELS):
        into[k] += other[k]
    if other["best"] is not None:
        better = min if metric != "pushups" else max
        into["best"] = other["best"] if into["best"] is None else better(into["best"], other["best"])


def compute(c, kind: str, start: dt.date, end: dt.date, grade: Grader, today: dt.date, *,
            min_group: int = MIN_GROUP) -> dict[str, Any]:
    """The report data for one period, read on connection ``c`` (the caller authorises). See the module text for the
    disclosure rules. Shape:
    {'kind','period':{'start','end','previous_start','previous_end'},'min_group','empty',
     'platoons':[{'platoon_id','platoon','suppressed','cadets','tested','results','tests':{run,walk,pushups},
                  'accommodations':{kind:{scope:int|None}}}],
     'total':{'cadets','tested','results','tests','accommodations'}}
    a test block is {'suppressed': True} or {'suppressed': False,'n','excellent','pass','fail','avg','best','change'}
    (run/walk values in tenths of a second, push-ups in repetitions; ``change`` is the average now minus the average then,
    or None when omitted)."""
    if min_group < MIN_GROUP:
        raise ValueError(f"min_group may not be lower than {MIN_GROUP}")
    pstart, pend = schedule.previous_period(kind, start, end)
    cadets = c.execute("SELECT id,pseudonym,gender,is_km,platoon_id FROM users WHERE role='cadet' AND active=1").fetchall()
    by_ps = {x["pseudonym"]: x for x in cadets}
    per = _latest_per_cadet(c, by_ps, start, end)
    cur = _groups(cadets, per, grade)
    prev = _groups(cadets, _latest_per_cadet(c, by_ps, pstart, pend), grade)
    by_id = {x["id"]: x for x in cadets}
    if min(end, today) >= start:                           # accommodations in effect on some day of the period so far
        for r in c.execute("SELECT user_id,kind,scope FROM accommodations WHERE approval='approved' "
                           "AND start_date<=? AND end_date>=?", (min(end, today).isoformat(), start.isoformat())):
            cad = by_id.get(r["user_id"])
            if cad is not None:
                cur[cad["platoon_id"]]["acc"][(r["kind"], r["scope"])].add(r["user_id"])
    names = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM platoons ORDER BY name, id")}
    order = [*names, None]
    tot_cadets = tot_tested = tot_results = None
    tot_blocks = {m: _block() for m in _METRICS}
    tot_seen = {m: False for m in _METRICS}
    common = {m: {"n": 0, "sum": 0, "pn": 0, "psum": 0} for m in _METRICS}      # shown in BOTH periods
    tot_acc: dict[tuple, int | None] = {(k, s): None for k in _KINDS for s in _SCOPES}
    out_platoons = []
    for pid in order:
        g = cur.get(pid) or _group()
        pg = prev.get(pid) or _group()
        if pid is None and g["members"] == 0:
            continue
        row: dict[str, Any] = {"platoon_id": pid, "platoon": names.get(pid)}
        hidden = g["members"] < min_group
        if hidden:
            row.update(suppressed=True, cadets=None, tested=None, results=None,
                       tests={m: {"suppressed": True} for m in _METRICS},
                       accommodations={k: {s: None for s in _SCOPES} for k in _KINDS})
            out_platoons.append(row)
            continue
        tested_ok = g["tested"] >= min_group
        tests = {m: _public_block(m, g["blocks"][m], pg["blocks"][m], min_group) for m in _METRICS}
        if not tested_ok:
            tests = {m: {"suppressed": True} for m in _METRICS}      # (a block cannot reach 5 with fewer than 5 testers)
        acc = {k: {s: (len(g["acc"][(k, s)]) if len(g["acc"][(k, s)]) >= min_group else None) for s in _SCOPES} for k in _KINDS}
        row.update(suppressed=False, cadets=g["members"], tested=g["tested"] if tested_ok else None,
                   results=g["results"] if tested_ok else None, tests=tests, accommodations=acc)
        out_platoons.append(row)
        tot_cadets = (tot_cadets or 0) + g["members"]
        if tested_ok:
            tot_tested = (tot_tested or 0) + g["tested"]
            tot_results = (tot_results or 0) + g["results"]
        for m in _METRICS:
            if not tests[m]["suppressed"]:
                _merge(tot_blocks[m], g["blocks"][m], m)
                tot_seen[m] = True
                if pg["blocks"][m]["n"] >= min_group:
                    common[m]["n"] += g["blocks"][m]["n"]
                    common[m]["sum"] += g["blocks"][m]["sum"]
                    common[m]["pn"] += pg["blocks"][m]["n"]
                    common[m]["psum"] += pg["blocks"][m]["sum"]
        for k in _KINDS:
            for s in _SCOPES:
                if acc[k][s] is not None:                  # only shown cells add up; there are no row or column totals
                    tot_acc[(k, s)] = (tot_acc[(k, s)] or 0) + acc[k][s]
    total_tests = {}
    for m in _METRICS:
        if not tot_seen[m]:
            total_tests[m] = {"suppressed": True}
            continue
        b = _public_block(m, tot_blocks[m], None, min_group)
        cm = common[m]
        if cm["n"] >= min_group and cm["pn"] >= min_group:
            b["change"] = _mean(m, cm["sum"] / cm["n"] - cm["psum"] / cm["pn"], 1)
        total_tests[m] = b
    return {"kind": kind, "min_group": min_group,
            "period": {"start": start.isoformat(), "end": end.isoformat(),
                       "previous_start": pstart.isoformat(), "previous_end": pend.isoformat()},
            "empty": not per,
            "platoons": out_platoons,
            "total": {"cadets": tot_cadets, "tested": tot_tested, "results": tot_results, "tests": total_tests,
                      "accommodations": {k: {s: tot_acc[(k, s)] for s in _SCOPES} for k in _KINDS}}}


# ------------------------------------------------------------------------------------------------- generate and store
def generate(db_path, kind: str, start: dt.date, end: dt.date, *, grade: Grader, render: Renderer, today: dt.date,
             now: dt.datetime | None = None) -> int | None:
    """Compute, render and store the report of one period, and create one delivery row for every active chief account.
    Returns the new report id, or None when that period was already stored (nothing changes: a stored report is the
    snapshot as of its generation). One transaction; one ``report.generate`` audit row."""
    if kind not in schedule.KINDS or end < start:
        raise ValueError("unknown report type or empty period")
    with db.transaction(db_path) as c:
        if c.execute("SELECT 1 FROM reports WHERE period_type=? AND period_start=?", (kind, start.isoformat())).fetchone():
            return None
        pdf = render(compute(c, kind, start, end, grade, today))
        rid = c.execute("INSERT INTO reports(period_type,period_start,period_end,generated_at,pdf) VALUES(?,?,?,?,?)",
                        (kind, start.isoformat(), end.isoformat(), audit.ts(now), pdf)).lastrowid
        c.execute("INSERT OR IGNORE INTO report_deliveries(report_id,user_id) SELECT ?, id FROM users "
                  "WHERE role='chief' AND active=1", (rid,))
        audit.record(c, actor_id=None, action="report.generate", now=now)
        return rid


def adhoc_report(db_path, actor_id, start: dt.date, end: dt.date, *, kind: str, grade: Grader, render: Renderer,
                 today: dt.date, now: dt.datetime | None = None) -> bytes:
    """A report generated ON DEMAND for the chief and returned as bytes: NOTHING is stored (no ``reports`` row, no
    delivery, no mail). ``kind`` is 'trailing' (the last 30 days: ``start``/``end`` must be exactly
    ``baror.domain.reports.trailing_period(today)``) or 'range' (a chosen range, checked by ``validate_range``: a bad range
    raises ValidationError REPORT_RANGE_ORDER / _FUTURE / _TOO_LONG). CHIEF ONLY through ``chief._run`` (role and ``active``
    re-read inside the transaction; anybody else gets AuthError and an existing account that is refused leaves a 'denied'
    ``chief.report_range`` row). The numbers come from ``compute`` unchanged, so every privacy rule of the stored reports
    applies identically (aggregates only, no names, groups under 5 hidden, no total for everyone, latest result per cadet
    per period). Exactly one ``chief.report_range`` audit row per successful generation; a refused or invalid request
    leaves no 'ok' row. Input is checked after the authorisation, so a refusal never depends on it."""
    def body(c, actor, _platoon, _cadet):
        if kind not in schedule.ADHOC_KINDS:
            raise ValueError("unknown on-demand report type")
        if kind == schedule.RANGE:
            s, e = schedule.validate_range(start, end, today)
        else:
            s, e = schedule.trailing_period(today)
            if (start, end) != (s, e):
                raise ValueError("a trailing report covers exactly the last 30 days")
        pdf = render(compute(c, kind, s, e, grade, today))
        audit.record(c, actor_id=actor, action="chief.report_range", now=now)
        return (bytes(pdf),)                      # a tuple: an empty file must not look like "not visible" to _run
    return chief._run(db_path, actor_id, "chief.report_range", None, None, now, body)[0]


def generate_due(db_path, today: dt.date, *, grade: Grader, render: Renderer,
                 now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Make the reports that ``baror.domain.reports.reports_due(today)`` lists for that date (none on most days).
    -> [{'kind','start','end','id' (None when it already existed)}]."""
    out = []
    for kind, start, end in schedule.reports_due(today):
        rid = generate(db_path, kind, start, end, grade=grade, render=render, today=today, now=now)
        out.append({"kind": kind, "start": start, "end": end, "id": rid})
    return out


# ------------------------------------------------------------------------------------------------------- delivery
def mail_state(db_path) -> dict[str, Any]:
    """{'enabled': bool, 'changed_at': ISO timestamp or None}: the automatic-mail switch (operator path, no actor). The
    chief screen reads it through ``chief.report_mail_state``."""
    with db.reader(db_path) as c:
        r = c.execute("SELECT mail_enabled, changed_at FROM report_settings WHERE id=1").fetchone()
    return {"enabled": bool(r and r["mail_enabled"]), "changed_at": r["changed_at"] if r else None}


def set_mail_enabled(db_path, actor_id, enabled, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Switch the automatic e-mailing of reports on or off. CHIEF ONLY (same discipline as ``baror.storage.chief``: role
    and ``active`` re-read inside the transaction; anybody else gets AuthError, and an existing account that is refused
    leaves a 'denied' ``chief.mail_switch`` row). Records who and when. Switching to the value it already has changes
    nothing (so ``changed_at``, the cut-off for the backlog, is never moved by a repeated press). Audited as
    ``chief.mail_on`` / ``chief.mail_off``. Returns the new state."""
    actor = chief._id(actor_id)
    denied: Exception | None = None
    with db.transaction(db_path) as c:
        denied = chief._denial(c, actor)
        if denied is not None:
            if actor is not None and c.execute("SELECT 1 FROM users WHERE id=?", (actor,)).fetchone():
                audit.record(c, actor_id=actor, action="chief.mail_switch", outcome="denied", now=now)
        else:
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be True or False")
            current = c.execute("SELECT mail_enabled FROM report_settings WHERE id=1").fetchone()
            if current is None or bool(current["mail_enabled"]) != enabled:
                c.execute("INSERT INTO report_settings(id, mail_enabled, changed_at, changed_by) VALUES(1,?,?,?) "
                          "ON CONFLICT(id) DO UPDATE SET mail_enabled=excluded.mail_enabled, "
                          "changed_at=excluded.changed_at, changed_by=excluded.changed_by",
                          (int(enabled), audit.ts(now), actor))
            audit.record(c, actor_id=actor, action="chief.mail_on" if enabled else "chief.mail_off", now=now)
    if denied is not None:
        raise denied
    return mail_state(db_path)


def pending_deliveries(db_path, *, now: dt.datetime | None = None, days: int = RETRY_DAYS) -> list[dict[str, Any]]:
    """Deliveries to send NOW: [] while the switch is off. When it is on: unsent deliveries to ACTIVE chief accounts for
    reports generated in the last ``days`` days AND at or after the moment the switch was turned on (so turning it on
    never sends the backlog). The address is returned for the sender (in memory only: never logged or stored here)."""
    cutoff = audit.ts((now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=days))
    with db.reader(db_path) as c:
        s = c.execute("SELECT mail_enabled, changed_at FROM report_settings WHERE id=1").fetchone()
        if not s or not s["mail_enabled"]:
            return []
        rows = c.execute(
            "SELECT d.report_id, d.user_id, d.attempts, r.period_type AS kind, r.period_start AS start, r.period_end AS end, "
            "u.email FROM report_deliveries d JOIN reports r ON r.id=d.report_id JOIN users u ON u.id=d.user_id "
            "WHERE d.sent_at IS NULL AND u.role='chief' AND u.active=1 AND r.generated_at>=? AND r.generated_at>=? "
            "ORDER BY r.id, u.id", (cutoff, s["changed_at"] or "")).fetchall()
    return [dict(r) for r in rows]


def report_pdf(db_path, report_id: int) -> bytes:
    with db.reader(db_path) as c:
        row = c.execute("SELECT pdf FROM reports WHERE id=?", (report_id,)).fetchone()
    if row is None:
        raise KeyError("no such report")
    return bytes(row["pdf"])


def mark_sent(db_path, report_id: int, user_id: int, *, now: dt.datetime | None = None) -> bool:
    """Record a successful send once. False when it was already recorded (never counted twice)."""
    with db.transaction(db_path) as c:
        done = c.execute("UPDATE report_deliveries SET sent_at=?, attempts=attempts+1, last_error=NULL "
                         "WHERE report_id=? AND user_id=? AND sent_at IS NULL", (audit.ts(now), report_id, user_id)).rowcount
        if done:
            audit.record(c, actor_id=None, action="report.mail", target_user_id=user_id, now=now)
        return bool(done)


def mark_failed(db_path, report_id: int, user_id: int, code: str) -> None:
    """Keep the delivery unsent with a short error code (anything else is stored as 'unknown'; never exception text)."""
    code = code if _CODE.match(code or "") else "unknown"
    with db.transaction(db_path) as c:
        c.execute("UPDATE report_deliveries SET attempts=attempts+1, last_error=? WHERE report_id=? AND user_id=? "
                  "AND sent_at IS NULL", (code, report_id, user_id))
