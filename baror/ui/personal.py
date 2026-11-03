"""The cadet's own area: progress charts, entering a result, history. Everything is read and written through the
user-scoped repository with the id of the SIGNED-IN user (never a widget value)."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from baror.config import Config
from baror.domain.entry import grade_entry, parse_entry
from baror.errors import BarorError
from baror.logging_setup import log_event
from baror.messages_he import render_exception, t
from baror.storage import repo
from baror.ui import charts
from baror.ui.format import graded_lines, time_text
from baror.ui.text import esc


def _scale_label(user: dict) -> str:
    gender = t("scale_female" if user["gender"] == "F" else "scale_male")
    return t("scale_walk", gender=gender) if user["is_km"] else gender


def _chart_area(area, rows: list[dict], user: dict) -> None:
    area.empty()
    with area.container():
        if not rows:
            st.subheader(t("empty_summary_title"))
            st.write(t("empty_summary_body"))
            return
        st.subheader(t("trend_title"))
        run_fig, push_fig = charts.run_chart(rows, bool(user["is_km"])), charts.pushups_chart(rows)
        if run_fig is not None:
            st.markdown(f"**{t('chart_walk' if user['is_km'] else 'chart_run')}**")
            st.plotly_chart(run_fig, width="stretch", key="chart_run")
        if push_fig is not None:
            st.markdown(f"**{t('chart_pushups')}**")
            st.plotly_chart(push_fig, width="stretch", key="chart_pushups")


def _history(rows: list[dict]) -> None:
    if not rows:
        return
    df = pd.DataFrame([{t("col_date"): r["test_date"],
                        t("col_run"): time_text(r["run_tenths"]),
                        t("col_pushups"): "" if r["pushups"] is None else r["pushups"]} for r in rows])
    with st.expander(t("history_title")):
        st.dataframe(df, hide_index=True, width="stretch")


def personal_page(cfg: Config, user: dict) -> None:
    uid = user["id"]
    st.title(t("greeting", name=esc(user["name"])))
    st.write(t("scale_line", scale=_scale_label(user)))
    chart_area = st.empty()          # filled AFTER the save handler, so a new result is in the chart of the same run
    st.divider()
    st.subheader(t("add_title"))
    today = dt.datetime.now(cfg.tz).date()
    with st.form("baror_form"):
        test_date = st.date_input(t("field_date"), value=today, max_value=today, key="add_date")
        label = t("field_run_time_walk" if user["is_km"] else "field_run_time_run")
        run_text = st.text_input(label, key="add_run", max_chars=8)
        pushups = st.number_input(t("field_pushups"), min_value=0, max_value=200, step=1, value=None, key="add_pushups")
        submitted = st.form_submit_button(t("btn_save_result"), type="primary")
    if submitted:
        try:
            entry = parse_entry(run_text=run_text, pushups=None if pushups is None else int(pushups),
                                test_date=test_date, today=today, is_km=bool(user["is_km"]))
            repo.add_result(cfg.db_path, uid, test_date=entry.test_date, run_tenths=entry.run_tenths,
                            pushups=entry.pushups)
        except BarorError as exc:
            st.error(render_exception(exc))
            st.caption(t("input_kept"))
        else:
            log_event("result_added", user_id=uid)
            st.success(t("saved"))
            if entry.pushups == 0:
                st.warning(t("warn_zero_pushups"))
            for line in graded_lines(grade_entry(entry, user["gender"], bool(user["is_km"]))):
                st.write(line)
    rows = repo.list_results(cfg.db_path, uid)
    _chart_area(chart_area, rows, user)
    _history(rows)
