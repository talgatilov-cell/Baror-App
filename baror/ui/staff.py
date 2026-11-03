"""Instructor (own platoon) and chief (all platoons) screens. READ-ONLY; only reachable when BAROR_COHORT_VIEWS=1 and
the account has the role; authorisation is nevertheless enforced again inside ``baror.storage.cohort`` / ``chief`` on
every call. Every named read is audited there, and a rerun is a new read, so a page may log more than once.

Tables are shown with ``st.dataframe`` (no Markdown interpretation of names)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from baror.config import Config
from baror.domain.entry import grade_value
from baror.domain.grading import grade_stored_value
from baror.errors import BarorError
from baror.messages_he import render_exception, t
from baror.storage import chief, cohort
from baror.ui.format import level_text, time_text
from baror.ui.text import esc

_METRIC_KEY = {"run": "metric_run", "walk": "metric_walk", "pushups": "metric_pushups"}
_DASH = "—"


def _level(test: str, gender: str, date: str, value) -> str:
    return "" if value is None else level_text(grade_value(test, gender, date, value).level)


def _cell(time_or_reps, level_text_: str) -> str:
    return "" if time_or_reps in (None, "") else f"{time_or_reps}  ({level_text_})"


def _results_table(cadet: dict, results: list[dict]) -> None:
    if not results:
        st.write(t("no_results_cadet"))
        return
    run_test = "walk" if cadet["is_km"] else "run"
    rows = []
    for r in results:
        rows.append({t("col_date"): r["test_date"],
                     t("col_run"): _cell(time_text(r["run_tenths"]), _level(run_test, cadet["gender"], r["test_date"], r["run_tenths"])),
                     t("col_pushups"): _cell(r["pushups"], _level("pushups", cadet["gender"], r["test_date"], r["pushups"]))})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _guard(fn):
    try:
        return fn()
    except BarorError as exc:
        st.error(render_exception(exc))
        return None


# ------------------------------------------------------------------------------------------------- instructor
def platoon_page(cfg: Config, user: dict) -> None:
    st.title(t("platoon_title"))
    st.info(t("platoon_banner"))
    platoons = _guard(lambda: cohort.my_platoons(cfg.db_path, user["id"]))
    if platoons is None:
        return
    if not platoons:
        st.write(t("platoon_none"))
        return
    names = {p["id"]: p["name"] for p in platoons}
    pid = st.selectbox(t("pick_platoon"), list(names), format_func=names.get, key="staff_platoon")
    rows = _guard(lambda: cohort.cohort_summary(cfg.db_path, user["id"], pid))
    if rows is None:
        return
    table = []
    for r in rows:
        run_test = "walk" if r["is_km"] else "run"
        lr, lp = r["latest_run"], r["latest_pushups"]
        table.append({t("col_name"): r["name"],
                      t("col_run"): _cell(time_text(lr["run_tenths"]) if lr else None,
                                          _level(run_test, r["gender"], lr["test_date"], lr["run_tenths"]) if lr else ""),
                      t("col_pushups"): _cell(lp["pushups"] if lp else None,
                                              _level("pushups", r["gender"], lp["test_date"], lp["pushups"]) if lp else ""),
                      t("col_best_run"): time_text(r["best_run_tenths"]),
                      t("col_best_pushups"): "" if r["best_pushups"] is None else r["best_pushups"],
                      t("col_results"): r["result_count"]})
    if not table:
        st.write(t("no_results_cadet"))
        return
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    by_id = {r["id"]: r["name"] for r in rows}
    pick = st.selectbox(t("pick_cadet"), list(by_id), index=None, format_func=by_id.get, key="staff_cadet", placeholder=t("choose_placeholder"))
    if pick is not None:
        data = _guard(lambda: cohort.get_cadet_results(cfg.db_path, user["id"], pick))
        if data:
            st.subheader(esc(data["cadet"]["name"]))    # a name is user-controlled text: never raw into Markdown
            _results_table(data["cadet"], data["results"])


# ------------------------------------------------------------------------------------------------------ chief
def _overview_table(ov: dict) -> pd.DataFrame:
    rows = []

    def add(label, metric, block):
        row = {t("col_platoon"): label, t("col_metric"): t(_METRIC_KEY[metric])}
        if block.get("suppressed"):
            row.update({t("col_n"): _DASH, t("col_excellent"): _DASH, t("col_pass"): _DASH, t("col_fail"): _DASH})
        else:
            row.update({t("col_n"): block["n"], t("col_excellent"): block["excellent"], t("col_pass"): block["pass"],
                        t("col_fail"): block["fail"]})
        rows.append(row)
    for p in ov["platoons"]:
        label = p["platoon"] if p["platoon"] is not None else t("unassigned")
        for metric in ("run", "walk", "pushups"):
            block = p[metric]
            if block.get("suppressed") and metric == "walk":
                continue                      # keep the table short: the walk track is rarely used
            add(label, metric, block)
    for metric in ("run", "walk", "pushups"):
        add(t("total_row"), metric, ov["total"][metric])
    return pd.DataFrame(rows)


def chief_page(cfg: Config, user: dict) -> None:
    st.title(t("chief_title"))
    st.info(t("chief_banner"))
    ov = _guard(lambda: chief.overview(cfg.db_path, user["id"], grade_stored_value))
    if ov is None:
        return
    st.caption(t("group_too_small"))
    st.dataframe(_overview_table(ov), hide_index=True, width="stretch")
    UNASSIGNED = 0      # selectbox returns None for "nothing chosen", so "no platoon" needs its own value
    names = {(p["platoon_id"] or UNASSIGNED): (p["platoon"] if p["platoon"] is not None else t("unassigned"))
             for p in ov["platoons"]}
    if not names:
        return
    sel = st.selectbox(t("pick_platoon"), list(names), index=None, format_func=names.get, key="chief_platoon", placeholder=t("choose_placeholder"))
    if sel is None:
        return
    members = _guard(lambda: chief.list_platoon_members(cfg.db_path, user["id"], None if sel == UNASSIGNED else sel))
    if not members:
        return
    by_id = {m["id"]: m["name"] for m in members}
    pick = st.selectbox(t("pick_cadet"), list(by_id), index=None, format_func=by_id.get, key="chief_cadet", placeholder=t("choose_placeholder"))
    if pick is not None:
        data = _guard(lambda: chief.get_cadet_results(cfg.db_path, user["id"], pick))
        if data:
            st.subheader(esc(data["cadet"]["name"]))
            _results_table(data["cadet"], data["results"])
