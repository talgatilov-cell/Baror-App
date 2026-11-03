"""READ-ONLY all-platoons view for the chief and deputy chief (ADR-0001). Stdlib only; never imports streamlit.

Same discipline as ``baror.storage.cohort``:
* The actor is identified by ``actor_id`` only; role and ``active`` are re-read from ``users`` inside the transaction on
  EVERY call. Access = active account with role ``chief``. Admins and instructors have none; a chief has no instructor
  rights either (``cohort`` demands role instructor/admin).
* Errors: an actor who may not use the chief view at all -> ``AuthError``. A target that cannot be shown (no such
  cadet, staff account, deactivated or de-identified cadet) -> the SAME ``NotFoundError``.
* Authorisation check, data and audit row run in ONE ``BEGIN IMMEDIATE`` transaction. A refused call by an existing
  account writes a 'denied' audit row; unknown actors write nothing (strangers cannot fill the log).
* No write statement except INSERTs into ``audit_log`` (tests/test_chief.py scans this source).

Reads (the last two serve the stored period reports of ``baror.storage.reports``):
* ``overview``              aggregates per platoon and in total. NO names, ids or raw values. A group with fewer than
                            ``MIN_GROUP`` contributing cadets is returned as ``suppressed`` without any figure, and the
                            totals add up SHOWN groups only (otherwise ``total - shown`` would reveal a hidden group).
* ``list_platoon_members``  names of the active cadets of one platoon (or of the unassigned), to choose whom to open.
* ``get_cadet_results``     name and results of ONE cadet in any platoon: the audited drill-down (``chief.cadet``).

* ``list_reports``          metadata of the stored aggregated reports (weekly / monthly), newest first. One audit row.
* ``get_report``            one stored report with its PDF bytes (aggregated, no names). One audit row per call.

Grading is injected (``grade(metric, gender, test_date, value) -> 'excellent'|'pass'|'fail'|None``), so this layer does
not import the domain package; see ``baror.domain.grading.grade_stored_value``.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from baror.errors import AuthError, NotFoundError
from baror.storage import accommodations, audit, db
from baror.storage.summaries import fold_summary

__all__ = ["MIN_GROUP", "overview", "list_platoon_members", "platoon_summary", "get_cadet_results", "list_reports",
           "get_report", "report_mail_state"]

MIN_GROUP = 5   # suggested minimum from the research, not a legal standard (ADR-0001)
_LEVELS = ("excellent", "pass", "fail")
Grader = Callable[[str, str, str, int], "str | None"]


def _id(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2**63 else None


def _denial(c, actor):
    a = c.execute("SELECT role,active FROM users WHERE id=?", (actor,)).fetchone() if actor else None
    if a is None or not a["active"] or a["role"] != "chief":
        return AuthError("NOT_AUTHORISED", detail="chief view requires an active chief")
    return None


def _run(db_path, actor_id, action, platoon_id, cadet_id, now, body):
    actor, platoon, cadet = _id(actor_id), platoon_id, _id(cadet_id)
    denied: Exception | None = None
    out = None
    with db.transaction(db_path) as c:
        denied = _denial(c, actor)
        if denied is None:
            out = body(c, actor, platoon, cadet)
            if out is None:
                denied = NotFoundError("COHORT_NOT_FOUND", detail=f"{action} not visible")
        if denied is not None and actor is not None and \
                c.execute("SELECT 1 FROM users WHERE id=?", (actor,)).fetchone():
            audit.record(c, actor_id=actor, action=action, target_user_id=cadet,
                         platoon_id=_id(platoon), outcome="denied", now=now)
    if denied is not None:
        raise denied
    return out


# ------------------------------------------------------------------ aggregates
def _people(c):
    """One row per cadet who counts: live active cadets plus de-identified ones. Staff are never included."""
    return c.execute(
        "SELECT x.ps, x.gender, x.is_km, x.platoon_id, r.test_date, r.run_tenths, r.pushups FROM ("
        " SELECT u.pseudonym AS ps, u.gender AS gender, u.is_km AS is_km, u.platoon_id AS platoon_id FROM users u"
        "  WHERE u.role='cadet' AND u.active=1"
        " UNION ALL"
        " SELECT p.pseudonym, p.gender, p.is_km, p.platoon_id FROM pseudonyms p WHERE p.deidentified_at IS NOT NULL"
        ") x LEFT JOIN results r ON r.pseudonym=x.ps ORDER BY x.ps, r.test_date DESC, r.id DESC").fetchall()


def _latest_per_cadet(rows):
    people: dict[str, dict[str, Any]] = {}
    for r in rows:   # newest first within a cadet
        p = people.setdefault(r["ps"], {"gender": r["gender"], "is_km": bool(r["is_km"]),
                                        "platoon_id": r["platoon_id"], "run": None, "pushups": None})
        if r["test_date"] is None:
            continue
        if p["run"] is None and r["run_tenths"] is not None:
            p["run"] = (r["test_date"], r["run_tenths"])
        if p["pushups"] is None and r["pushups"] is not None:
            p["pushups"] = (r["test_date"], r["pushups"])
    return people


def _empty():
    return {"n": 0, **{lv: 0 for lv in _LEVELS}}


def _shown(block):
    return {"suppressed": False, **block}


def overview(db_path, actor_id, grade: Grader, *, min_group: int = MIN_GROUP,
             now: dt.datetime | None = None) -> dict[str, Any]:
    """{'min_group': 5, 'platoons': [{'platoon_id','platoon','suppressed','cadets', 'run','walk','pushups'}],
        'total': {'run','walk','pushups'}}. A metric block is ``{'suppressed': True}`` or
    ``{'suppressed': False, 'n', 'excellent', 'pass', 'fail'}``; the 'unassigned' pseudo-platoon has ``platoon_id``
    None. Audited as one ``chief.overview`` row."""
    if isinstance(min_group, bool) or not isinstance(min_group, int) or min_group < MIN_GROUP:
        raise ValueError(f"min_group may not be lower than {MIN_GROUP}")   # a caller cannot weaken the rule

    def body(c, actor, _platoon, _cadet):
        platoons = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM platoons ORDER BY name, id")}
        people = _latest_per_cadet(_people(c))
        groups: dict[Any, dict[str, Any]] = {pid: {"members": 0, "run": _empty(), "walk": _empty(), "pushups": _empty()}
                                             for pid in platoons}
        groups[None] = {"members": 0, "run": _empty(), "walk": _empty(), "pushups": _empty()}
        for p in people.values():
            g = groups.setdefault(p["platoon_id"], {"members": 0, "run": _empty(), "walk": _empty(), "pushups": _empty()})
            g["members"] += 1
            aerobic = "walk" if p["is_km"] else "run"
            for metric, key in ((aerobic, "run"), ("pushups", "pushups")):
                if p[key] is None:
                    continue
                level = grade(metric, p["gender"], p[key][0], p[key][1])
                if level in _LEVELS:                      # None / anything else: not gradable, not counted
                    g[metric]["n"] += 1
                    g[metric][level] += 1
        out_platoons, total = [], {m: _empty() for m in ("run", "walk", "pushups")}
        for pid, g in groups.items():
            if pid is None and g["members"] == 0:
                continue
            row: dict[str, Any] = {"platoon_id": pid, "platoon": platoons.get(pid)}
            if g["members"] < min_group:
                row.update(suppressed=True, cadets=None, run={"suppressed": True}, walk={"suppressed": True},
                           pushups={"suppressed": True})
            else:
                row.update(suppressed=False, cadets=g["members"])
                for m in ("run", "walk", "pushups"):
                    if g[m]["n"] >= min_group:
                        row[m] = _shown(g[m])
                        for k, v in g[m].items():       # totals add up SHOWN blocks only
                            total[m][k] += v
                    else:
                        row[m] = {"suppressed": True}
            out_platoons.append(row)
        totals = {m: (_shown(t) if t["n"] >= min_group else {"suppressed": True}) for m, t in total.items()}
        audit.record(c, actor_id=actor, action="chief.overview", now=now)
        return {"min_group": min_group, "platoons": out_platoons, "total": totals}
    return _run(db_path, actor_id, "chief.overview", None, None, now, body)


# ------------------------------------------------------------------ named reads
def list_platoon_members(db_path, actor_id, platoon_id, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Active cadets of one platoon by name: [{id,name,gender,is_km}]. ``platoon_id=None`` lists the cadets who have no
    platoon. Unknown platoon -> NotFoundError. Audited as one ``chief.members`` row."""
    def body(c, actor, platoon, _cadet):
        if platoon is not None:
            if _id(platoon) is None or c.execute("SELECT 1 FROM platoons WHERE id=?", (platoon,)).fetchone() is None:
                return None
            rows = c.execute("SELECT id,name,gender,is_km FROM users WHERE platoon_id=? AND role='cadet' AND active=1 "
                             "ORDER BY name, id", (platoon,)).fetchall()
        else:
            rows = c.execute("SELECT id,name,gender,is_km FROM users WHERE platoon_id IS NULL AND role='cadet' AND active=1 "
                             "ORDER BY name, id").fetchall()
        out = [dict(r) for r in rows]
        audit.record(c, actor_id=actor, action="chief.members", platoon_id=_id(platoon), now=now)
        return out
    return _run(db_path, actor_id, "chief.members", platoon_id, None, now, body)


def platoon_summary(db_path, actor_id, platoon_id, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """One entry per ACTIVE cadet of one platoon (``platoon_id=None``: the cadets without a platoon), by name: the same
    shape as ``cohort.cohort_summary`` (raw latest/best values, gender, is_km, accommodations; no grading). Unknown
    platoon -> NotFoundError. Audited with one ``chief.summary`` row PER CADET read (each disclosure is exact); an empty
    platoon gets one platoon-level row."""
    def body(c, actor, platoon, _cadet):
        if platoon is not None:
            if _id(platoon) is None or c.execute("SELECT 1 FROM platoons WHERE id=?", (platoon,)).fetchone() is None:
                return None
            where, args = "u.platoon_id=?", (platoon,)
        else:
            where, args = "u.platoon_id IS NULL", ()
        rows = c.execute(
            "SELECT u.id,u.name,u.gender,u.is_km,r.test_date,r.run_tenths,r.pushups FROM users u "
            "LEFT JOIN results r ON r.pseudonym=u.pseudonym WHERE " + where + " AND u.role='cadet' AND u.active=1 "
            "ORDER BY u.name, u.id, r.test_date DESC, r.id DESC", args).fetchall()     # `where` is one of two literals
        out = fold_summary(c, rows)
        if out:
            for s in out:
                audit.record(c, actor_id=actor, action="chief.summary", target_user_id=s["id"], platoon_id=_id(platoon), now=now)
        else:
            audit.record(c, actor_id=actor, action="chief.summary", platoon_id=_id(platoon), now=now)
        return out or [None]
    out = _run(db_path, actor_id, "chief.summary", platoon_id, None, now, body)
    return [] if out == [None] else out


def get_cadet_results(db_path, actor_id, cadet_id, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """{'cadet': {id,name,gender,is_km,platoon_id,platoon}, 'results': [{test_date,run_tenths,pushups}] newest first,
    'accommodations': [...] newest first}
    for ONE active cadet of any platoon. Audited per call as ``chief.cadet`` with actor, target and platoon."""
    def body(c, actor, _platoon, cadet):
        t = c.execute("SELECT t.id,t.name,t.gender,t.is_km,t.platoon_id,t.pseudonym,p.name AS platoon FROM users t "
                      "LEFT JOIN platoons p ON p.id=t.platoon_id WHERE t.id=? AND t.role='cadet' AND t.active=1",
                      (cadet,)).fetchone()
        if t is None:
            return None
        res = c.execute("SELECT test_date,run_tenths,pushups FROM results WHERE pseudonym=? "
                        "ORDER BY test_date DESC, id DESC", (t["pseudonym"],)).fetchall()
        audit.record(c, actor_id=actor, action="chief.cadet", target_user_id=t["id"], platoon_id=t["platoon_id"], now=now)
        return {"cadet": {k: t[k] for k in ("id", "name", "gender", "is_km", "platoon_id", "platoon")},
                "results": [dict(r) for r in res],
                "accommodations": accommodations.fetch_public(c, [t["id"]])[t["id"]]}
    return _run(db_path, actor_id, "chief.cadet", None, cadet_id, now, body)


# ------------------------------------------------------------------ stored period reports
def list_reports(db_path, actor_id, period_type: str | None = None, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """[{id, period_type ('weekly'|'monthly'), period_start, period_end, generated_at, size}] newest period first, for
    one type or (``period_type=None``) both. No PDF bytes. Audited as one ``chief.reports`` row; an unknown type is a
    ValueError (after the authorisation check, so a refusal never depends on the input)."""
    def body(c, actor, _platoon, _cadet):
        if period_type not in (None, "weekly", "monthly"):
            raise ValueError("unknown report type")
        where, args = ("WHERE period_type=?", (period_type,)) if period_type else ("", ())
        rows = c.execute("SELECT id, period_type, period_start, period_end, generated_at, length(pdf) AS size FROM reports "
                         + where + " ORDER BY period_end DESC, id DESC", args).fetchall()   # `where` is one of two literals
        audit.record(c, actor_id=actor, action="chief.reports", now=now)
        return [dict(r) for r in rows] or [None]
    out = _run(db_path, actor_id, "chief.reports", None, None, now, body)
    return [] if out == [None] else out


def get_report(db_path, actor_id, report_id, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """One stored report: {id, period_type, period_start, period_end, generated_at, pdf (bytes)}. Unknown id ->
    NotFoundError. Audited as one ``chief.report`` row per call (the UI calls it once per download, not per rerun)."""
    def body(c, actor, _platoon, _cadet):
        rid = _id(report_id)
        row = c.execute("SELECT id, period_type, period_start, period_end, generated_at, pdf FROM reports WHERE id=?",
                        (rid,)).fetchone() if rid else None
        if row is None:
            return None
        audit.record(c, actor_id=actor, action="chief.report", now=now)
        return {**{k: row[k] for k in ("id", "period_type", "period_start", "period_end", "generated_at")},
                "pdf": bytes(row["pdf"])}
    return _run(db_path, actor_id, "chief.report", None, None, now, body)


def report_mail_state(db_path, actor_id, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """{'enabled': bool, 'changed_at': ISO timestamp or None}: the automatic-mail switch. CHIEF ONLY (AuthError otherwise,
    a refusal by an existing account leaves a 'denied' row). NOT audited on success: it is a configuration flag with no
    data about anybody, and the screen reads it on every run, which would flood the log. Changing it is audited
    (``baror.storage.reports.set_mail_enabled``)."""
    actor = _id(actor_id)
    with db.transaction(db_path) as c:                    # check and read in ONE transaction, like the other reads
        denied = _denial(c, actor)
        if denied is None:
            r = c.execute("SELECT mail_enabled, changed_at FROM report_settings WHERE id=1").fetchone()
        elif actor is not None and c.execute("SELECT 1 FROM users WHERE id=?", (actor,)).fetchone():
            audit.record(c, actor_id=actor, action="chief.mail_state", outcome="denied", now=now)
    if denied is not None:
        raise denied
    return {"enabled": bool(r and r["mail_enabled"]), "changed_at": r["changed_at"] if r else None}
