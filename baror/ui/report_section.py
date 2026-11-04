"""The chief screen's section 'דוחות': the switch for automatic e-mailing and the list of stored reports (weekly / monthly)
with a download button each. Reports are made by ``scripts/generate_reports.py``, never here.

Reads and audit:
* The switch state is read from the database on EVERY run (``chief.report_mail_state``, chief-only, not audited on success)
  and written back to the widget before it is drawn, so a stale browser state can never write the old value back. The
  change itself runs in the widget callback, only on a click, and is audited in storage.
* The list of one type is read ONCE (``chief.list_reports``, one audit row) and kept under a '_' key; it is read again when
  the type is switched. A report's PDF is read only when "הכנה להורדה" is pressed (``chief.get_report``, one audit row) and
  kept under a '_' key, so the download itself and every rerun add no rows.
Nothing user-typed is drawn: only fixed copy, dates and numbers.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

from baror import export_report_pdf
from baror.config import Config, mail_from_env
from baror.domain import reports as schedule
from baror.domain.grading import grade_stored_value
from baror.errors import BarorError, ConfigError
from baror.messages_he import month_label, render_exception, t
from baror.storage import chief, reports
from baror.ui import accom

_TYPES = ("weekly", "monthly", "trailing", "range")
_LIST = "_report_list"          # {"type": 'weekly'|'monthly', "rows": [...]}
_OPEN = "_report_open"          # {"id": int, "name": str, "pdf": bytes}   (the ONE prepared report)
_ADHOC = "_report_adhoc"        # {"kind", "start", "end", "name", "pdf"}   (the ONE on-demand report, never stored)
_ERR = "_report_error"
_TOGGLE = "report_mail_toggle"
_SHOWN = 8                      # newest reports shown directly; the rest are in an expander


def guard(fn):
    try:
        return fn()
    except BarorError as exc:
        st.error(render_exception(exc), icon=":material/error:")
        return None


def _dmy(iso_date: str) -> str:
    return dt.date.fromisoformat(iso_date).strftime("%d/%m/%Y")


def row_title(r: dict) -> str:
    if r["period_type"] == "weekly":
        return t("report_row_weekly", start=_dmy(r["period_start"]), end=_dmy(r["period_end"]))
    return t("report_row_monthly", label=month_label(dt.date.fromisoformat(r["period_start"])), end=_dmy(r["period_end"]))


def file_name(r: dict) -> str:
    return f"baror-{r['period_type']}-{r['period_end']}.pdf"


def _mail_configured() -> bool:
    try:
        return mail_from_env() is not None
    except ConfigError:
        return False


def _switch_changed(cfg: Config, actor_id: int) -> None:
    """Widget callback: runs on a click only. The new value is the widget's; storage re-checks the role and audits."""
    try:
        reports.set_mail_enabled(cfg.db_path, actor_id, bool(st.session_state.get(_TOGGLE)))
        st.session_state["_notice"] = t("report_mail_saved")
    except BarorError as exc:
        st.session_state[_ERR] = render_exception(exc)


def _switch(cfg: Config, user: dict) -> None:
    state = guard(lambda: chief.report_mail_state(cfg.db_path, user["id"]))
    if state is None:
        return
    st.session_state[_TOGGLE] = state["enabled"]           # the database wins on every run
    st.toggle(t("report_mail_toggle"), key=_TOGGLE, on_change=_switch_changed, args=(cfg, user["id"]))
    st.caption(t("report_mail_explain"))
    if state["enabled"] and state["changed_at"]:
        st.caption(t("report_mail_on_since", date=_dmy(state["changed_at"][:10])))
    elif not state["enabled"]:
        st.caption(t("report_mail_off_note"))
    if state["enabled"] and not _mail_configured():
        st.warning(t("report_mail_not_configured"), icon=":material/mail_off:")
    st.caption(t("report_mail_privacy"))


def _row(cfg: Config, user: dict, r: dict) -> None:
    opened = st.session_state.get(_OPEN)
    with st.container(border=True, key=f"report_{r['id']}"):
        st.markdown(f"**{row_title(r)}**")
        st.caption(t("report_row_generated", date=_dmy(r["generated_at"][:10])))
        if opened is not None and opened["id"] == r["id"]:
            st.download_button(t("report_btn_download"), data=opened["pdf"], file_name=opened["name"], mime="application/pdf",
                               icon=":material/picture_as_pdf:", width="stretch", key=f"report_dl_{r['id']}", type="primary")
        elif st.button(t("report_btn_prepare"), key=f"report_prep_{r['id']}", icon=":material/description:", width="stretch"):
            data = guard(lambda: chief.get_report(cfg.db_path, user["id"], r["id"]))     # the audited read, once per press
            if data is not None:
                st.session_state[_OPEN] = {"id": r["id"], "name": file_name(r), "pdf": data["pdf"]}
                st.rerun()


def _adhoc_name(kind: str, start: dt.date, end: dt.date) -> str:
    return f"baror-{kind}-{end.isoformat()}.pdf" if kind == "trailing" else f"baror-{kind}-{start.isoformat()}_{end.isoformat()}.pdf"


def _make(cfg: Config, user: dict, kind: str, start: dt.date, end: dt.date, today: dt.date) -> None:
    """Generate ONE on-demand report (one audit row, nothing stored) and keep the bytes in session state for the download."""
    pdf = guard(lambda: reports.adhoc_report(
        cfg.db_path, user["id"], start, end, kind=kind, grade=grade_stored_value, today=today,
        render=lambda data: export_report_pdf.build_report_pdf(data, generated=today)))
    if pdf is None:
        st.session_state.pop(_ADHOC, None)
        return
    st.session_state[_ADHOC] = {"kind": kind, "start": start, "end": end, "name": _adhoc_name(kind, start, end), "pdf": pdf}


def _adhoc(cfg: Config, user: dict, kind: str) -> None:
    """The last 30 days, or a chosen date range of at most 5 calendar months. The range is checked here with the same
    domain function storage uses (so the message appears before the press); storage checks it again. Nothing is stored:
    the file lives under a '_' key until sign-out or the next change."""
    today = accom.today_of(cfg)
    if kind == "trailing":
        start, end = schedule.trailing_period(today)
        st.caption(t("report_trailing_help"))
        problem = None
    else:
        c1, c2 = st.columns(2, wrap=False, gap="small")
        with c1:
            start = st.date_input(t("report_range_start"), value=today - dt.timedelta(days=30), max_value=today,
                                  format="DD/MM/YYYY", key="report_range_from")
        with c2:
            end = st.date_input(t("report_range_end"), value=today, max_value=today, format="DD/MM/YYYY",
                                key="report_range_to")
        st.caption(t("report_range_help"))
        problem = None
        try:
            schedule.validate_range(start, end, today)
        except BarorError as exc:
            problem = render_exception(exc)
    st.caption(t("report_range_period", start=_dmy(start.isoformat()), end=_dmy(end.isoformat())))
    if problem:
        st.error(problem, icon=":material/error:")
    st.caption(t("report_adhoc_note"))
    held = st.session_state.get(_ADHOC)
    if held is not None and (held["kind"], held["start"], held["end"]) != (kind, start, end):
        st.session_state.pop(_ADHOC, None)                         # the choice changed: the old file no longer matches
        held = None
    if st.button(t("report_range_btn_make"), key="report_make", icon=":material/description:", width="stretch",
                 disabled=problem is not None):
        _make(cfg, user, kind, start, end, today)
        st.rerun()
    if held is not None:
        st.success(t("report_range_ready"), icon=":material/check_circle:")
        st.download_button(t("report_btn_download"), data=held["pdf"], file_name=held["name"], mime="application/pdf",
                           icon=":material/picture_as_pdf:", width="stretch", key="report_dl_adhoc", type="primary",
                           on_click="ignore")


def render(cfg: Config, user: dict) -> None:
    st.subheader(t("report_section_title"))
    st.caption(t("report_intro"))
    if st.session_state.get(_ERR):
        st.error(st.session_state.pop(_ERR), icon=":material/error:")
    _switch(cfg, user)
    kind = st.selectbox(t("report_type_label"), _TYPES, index=0, key="report_type", filter_mode=None,
                        format_func=lambda k: t("report_type_" + k))
    if kind in schedule.ADHOC_KINDS:
        st.session_state.pop(_OPEN, None)
        _adhoc(cfg, user, kind)
        return
    st.session_state.pop(_ADHOC, None)
    held = st.session_state.get(_LIST)
    if held is None or held["type"] != kind:               # one audited read per type, not one per rerun
        rows = guard(lambda: chief.list_reports(cfg.db_path, user["id"], kind))
        if rows is None:
            return
        held = {"type": kind, "rows": rows}
        st.session_state[_LIST] = held
        st.session_state.pop(_OPEN, None)
    if not held["rows"]:
        st.caption(t("report_list_empty"))
        return
    for r in held["rows"][:_SHOWN]:
        _row(cfg, user, r)
    if len(held["rows"]) > _SHOWN:
        with st.expander(t("report_older")):
            for r in held["rows"][_SHOWN:]:
                _row(cfg, user, r)
