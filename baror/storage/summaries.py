"""Folding the rows of a cadet read into one summary dict per cadet. Read-only helper shared by ``baror.storage.cohort``
and ``baror.storage.chief`` (stdlib only; never imports streamlit; it runs no statement except the accommodations read
on the caller's connection, so the data is exactly as authorised as the rows the caller passes in)."""
from __future__ import annotations

from typing import Any

from baror.storage import accommodations


def fold_summary(c, rows) -> list[dict[str, Any]]:
    """Rows of (users LEFT JOIN results), newest result first per cadet -> one summary dict per cadet, with the cadet's
    ``accommodations`` (read on the caller's connection ``c``, so they are exactly as authorised as the rows are).
    Shared with ``baror.storage.chief.platoon_summary``."""
    by: dict[int, dict[str, Any]] = {}
    for r in rows:  # rows arrive newest-first per cadet
        s = by.setdefault(r["id"], {
            "id": r["id"], "name": r["name"], "gender": r["gender"], "is_km": r["is_km"], "result_count": 0,
            "last_test_date": None, "latest_run": None, "latest_pushups": None,
            "best_run_tenths": None, "best_pushups": None})
        if r["test_date"] is None:
            continue
        s["result_count"] += 1
        s["last_test_date"] = s["last_test_date"] or r["test_date"]
        if r["run_tenths"] is not None:
            s["latest_run"] = s["latest_run"] or {"test_date": r["test_date"], "run_tenths": r["run_tenths"]}
            s["best_run_tenths"] = r["run_tenths"] if s["best_run_tenths"] is None else min(s["best_run_tenths"], r["run_tenths"])
        if r["pushups"] is not None:
            s["latest_pushups"] = s["latest_pushups"] or {"test_date": r["test_date"], "pushups": r["pushups"]}
            s["best_pushups"] = r["pushups"] if s["best_pushups"] is None else max(s["best_pushups"], r["pushups"])
    out = list(by.values())
    held = accommodations.fetch_public(c, [s["id"] for s in out])
    for s in out:
        s["accommodations"] = held[s["id"]]
    return out
