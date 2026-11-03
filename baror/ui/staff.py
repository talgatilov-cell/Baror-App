"""Instructor (own platoon) and chief (all platoons) screens. READ-ONLY; only reachable when BAROR_COHORT_VIEWS=1 and the
account has the role; authorisation is nevertheless enforced again inside ``baror.storage.cohort`` / ``chief`` on every
call, and every named read is audited there.

Design at 390 px: one card per cadet (no table), status = icon + word, each test graded on its own. There is NO overall
verdict anywhere (the owner has not yet explained "כ.מ" and the overall rule).

Safety: a cadet's name is user-typed text. It is only ever shown with ``st.text`` / ``st.subheader(esc(...))`` / widget
labels passed through ``esc`` and NEVER inside ``st.html`` or ``unsafe_allow_html``. The only Markdown built here contains
fixed copy, statuses and numbers.

Audit: an instructor page run reads (and logs) each cadet of the platoon, a rerun is a new read. The chief screen reads
aggregates per run, but a named read happens only after an explicit press of the open button; the member list is read
once per chosen platoon and kept in ``st.session_state`` under a key starting with "_" (removed at sign-out). The page
is only rendered while storage still says the account holds the role (the shell re-reads the role on every run)."""
from __future__ import annotations

from functools import partial

import pandas as pd
import streamlit as st

from baror.config import Config
from baror.domain.entry import grade_value
from baror.domain.grading import grade_stored_value
from baror.errors import BarorError
from baror.messages_he import render_exception, t
from baror.storage import accommodations, chief, cohort
from baror.ui import accom, charts, personal, report_section
from baror.ui.components import iso, status_badge
from baror.ui.format import time_text
from baror.ui.text import esc

_LEVELS = ("excellent", "pass", "fail")
_METRIC_KEY = {"run": "test_run", "walk": "test_walk", "pushups": "kpi_pushups"}   # short column heads
_TEST_KEY = {"run": "test_run", "walk": "test_walk", "pushups": "test_pushups"}
_BEST_KEY = {"run": "col_best_run", "walk": "col_best_walk", "pushups": "col_best_pushups"}
_DASH = "—"
_INS_OPEN = "_staff_open"             # instructor: {"cadet": id, "data": cadet_history(...)} of the ONE opened cadet (read only after the button)
_UNASSIGNED = 0                      # a selectbox returns None for "nothing chosen", so "no platoon" needs its own value
_ROWS = "_chief_rows"                 # {"platoon": id, "rows": [...]}   (read once per chosen platoon, audited per cadet)
_OPEN = "_chief_open"                 # {"cadet": id, "data": {...}}     (read only after the open button)
_PENDING = "_chief_pending"           # pending DELETION requests (read once, re-read after an action)
_REQUESTS = "_chief_requests"         # pending accommodation REQUESTS from cadets (read once, re-read after an action)


def _guard(fn):
    try:
        return fn()
    except BarorError as exc:
        st.error(render_exception(exc), icon=":material/error:")
        return None


def _banner(text: str) -> None:
    """Fixed copy in a keyed container (styled by .st-key-staff_banner); visible without scrolling."""
    with st.container(key="staff_banner"):
        st.write(text)


def _cadets(n: int) -> str:
    """'צוער אחד' / 'שני צוערים' / 'N צוערים' (Hebrew number agreement)."""
    return t("cadets_count_one") if n == 1 else t("cadets_count_two") if n == 2 else t("cadets_count", n=n)


def _dm(iso_date: str) -> str:
    """'2026-10-12' -> '12/10'."""
    y, m, d = iso_date.split("-")
    return f"{d}/{m}"


def _level(test: str, gender: str, date: str, value) -> str | None:
    return None if value is None else grade_stored_value(test, gender, date, value)


def _test_row(test: str, value_text: str, level: str | None, date: str) -> None:
    """One graded value on one row: 'ריצה 09:40 · 12/10' beside its status badge. Fixed copy, numbers and a status only."""
    left, right = st.columns([13, 8], gap="xsmall", vertical_alignment="center", wrap=False)
    left.markdown(f"**{t(_TEST_KEY[test])}** {iso(value_text)} · {iso(_dm(date))}")
    right.markdown(status_badge(level))


# ------------------------------------------------------------------------------------------------- shared card body
def _result_card(cadet: dict, r: dict, key: str) -> None:
    """One stored result (one date) of a cadet, each test graded separately."""
    run_test = "walk" if cadet["is_km"] else "run"
    with st.container(border=True, key=key):
        if r["run_tenths"] is not None:
            lvl = _level(run_test, cadet["gender"], r["test_date"], r["run_tenths"])
            _test_row(run_test, time_text(r["run_tenths"]), lvl, r["test_date"])
        if r["pushups"] is not None:
            lvl = _level("pushups", cadet["gender"], r["test_date"], r["pushups"])
            _test_row("pushups", str(r["pushups"]), lvl, r["test_date"])


def _cadet_results(cadet: dict, results: list[dict], prefix: str) -> None:
    st.subheader(esc(cadet["name"]))
    if not results:
        st.caption(t("no_results_cadet"))
        return
    for i, r in enumerate(results):
        _result_card(cadet, r, f"{prefix}_res_{i}")


# ------------------------------------------------------------------------------------------------------ instructor
def _gender_word(gender: str) -> str:
    return t("gender_female" if gender == "F" else "gender_male")


def _identity(r: dict) -> None:
    """Gender (a plain word) and, for the walk track, the כ.מ marker (no dates: it is a setting, not a record)."""
    st.caption(t("staff_gender", gender=_gender_word(r["gender"])))
    if r["is_km"]:
        st.markdown(f":gray-badge[:material/directions_walk: {t('acc_koam')}]")


def _records(cfg: Config, user: dict, cadet_id: int, records: list[dict], today, *, can_request: bool, can_delete: bool,
             can_decide: bool = False) -> None:
    """The accommodations of one cadet. Instructors may REQUEST a deletion; the chief deletes (after a confirmation) and
    approves or rejects a record that is still waiting for approval (``can_decide``)."""
    for rec in records:
        accom.record_row(rec, today, key=f"acc_{cadet_id}_{rec['id']}")
        if can_decide and rec.get("approval") == "pending":
            _decision_buttons(cfg, user, rec["id"], prefix="dec")
        if can_request and not rec["delete_requested"]:
            if st.button(t("btn_request_delete"), key=f"reqdel_{rec['id']}", icon=":material/delete_sweep:", width="stretch",
                         type="tertiary"):
                accom.request_delete_dialog(cfg, user, rec)
        if can_delete:
            if st.button(t("btn_delete_record"), key=f"deldirect_{rec['id']}", icon=":material/delete:", width="stretch"):
                accom.approve_dialog(cfg, user, rec["id"])


def _cadet_card(cfg: Config, user: dict, r: dict, today) -> None:
    """One compact card per cadet: name, gender, כ.מ, the BEST value of each test with its own status, the accommodations
    (dates and source; always fresh, from the list read) and a toggle that opens the progress chart and the full history
    of that one cadet (read, and audited, only when the toggle is pressed)."""
    run_test = "walk" if r["is_km"] else "run"
    best = r["best"]
    opened = st.session_state.get(_INS_OPEN)
    with st.container(border=True, key=f"cadet_{r['id']}"):
        st.text(r["name"])                                   # st.text: no Markdown interpretation of the name
        _identity(r)
        if not r["results_count"]:
            st.caption(t("no_results_cadet"))
        # The level of a best value is judged with the rules of the date of that test's latest result (no per-best date is read).
        if best["run_tenths"] is not None:
            _best_row(run_test, time_text(best["run_tenths"]),
                      _level(run_test, r["gender"], r["latest_run"]["test_date"], best["run_tenths"]))
        if best["pushups"] is not None:
            _best_row("pushups", str(best["pushups"]),
                      _level("pushups", r["gender"], r["latest_pushups"]["test_date"], best["pushups"]))
        _records(cfg, user, r["id"], r.get("accommodations", []), today, can_request=True, can_delete=False)
        if st.button(t("btn_add_accommodation"), key=f"accadd_{r['id']}", icon=":material/add:", width="stretch",
                     type="tertiary"):             # the repeated action on every card stays quiet
            accom.add_dialog(cfg, user, r["id"])
        if r["results_count"]:
            _details_toggle(cfg, user, r["id"], opened if opened is not None and opened["cadet"] == r["id"] else None)


def _best_row(test: str, value_text: str, level: str | None) -> None:
    """'שיא ריצה 09:40' beside its status badge. Fixed copy, numbers and a status only."""
    left, right = st.columns([13, 8], gap="xsmall", vertical_alignment="center", wrap=False)
    left.markdown(f"**{t(_BEST_KEY[test])}** {iso(value_text)}")
    right.markdown(status_badge(level))


def _progress(cadet: dict, results: list[dict], chart_key: str, prefix: str) -> None:
    """One cadet's progress, drawn the same way as on the chief screen: tiles, a status per test (latest result), the
    progress chart (with its data table) and the full history (every stored result, newest first) in an expander."""
    aero = "walk" if cadet["is_km"] else "run"
    personal._kpis(results, cadet, key="kpis_cadet")
    lr = next((x for x in results if x["run_tenths"] is not None), None)
    lp = next((x for x in results if x["pushups"] is not None), None)
    if lr:
        personal._status_block(_TEST_KEY[aero], time_text(lr["run_tenths"]),
                               grade_value(aero, cadet["gender"], lr["test_date"], lr["run_tenths"]))
    if lp:
        personal._status_block("test_pushups", str(lp["pushups"]),
                               grade_value("pushups", cadet["gender"], lp["test_date"], lp["pushups"]))
    charts.render(results, cadet, key=chart_key)
    with st.expander(t("history_title")):
        for i, x in enumerate(results):
            _result_card(cadet, x, f"{prefix}_res_{i}")


def _details_toggle(cfg: Config, user: dict, cadet_id: int, opened: dict | None) -> None:
    """Open / close the details of one cadet (one at a time). The history is read, and logged, only on the open press and
    kept under a '_' key, so a rerun is not a second read."""
    if opened is not None:
        if st.button(t("btn_close_details"), key=f"ins_close_{cadet_id}", width="stretch", icon=":material/expand_less:"):
            st.session_state.pop(_INS_OPEN, None)
            st.rerun()
        st.success(t("chief_opened_logged"), icon=":material/fact_check:")
        _progress(opened["data"]["cadet"], opened["data"]["results"], "ins_cadet_chart", "ins")
    elif st.button(t("btn_open_details"), key=f"ins_open_{cadet_id}", width="stretch", icon=":material/expand_more:"):
        data = _guard(lambda: cohort.cadet_history(cfg.db_path, user["id"], cadet_id))      # the named, audited read
        if data is not None:
            st.session_state[_INS_OPEN] = {"cadet": cadet_id, "data": data}
            st.rerun()


def platoon_page(cfg: Config, user: dict) -> None:
    with st.container(key="staff_page"):
        _platoon_body(cfg, user)


def _platoon_body(cfg: Config, user: dict) -> None:
    st.title(t("platoon_title"))
    _banner(t("platoon_banner"))
    accom.take_notice()
    platoons = _guard(lambda: cohort.my_platoons(cfg.db_path, user["id"]))
    if platoons is None:
        return
    if not platoons:
        st.write(t("platoon_none"))
        return
    names = {p["id"]: p["name"] for p in platoons}
    if len(names) > 1:
        pid = st.segmented_control(t("pick_platoon"), list(names), default=next(iter(names)), key="staff_platoon",
                                   required=True, width="stretch", format_func=lambda i: esc(names[i]))
    else:
        pid = next(iter(names))
        st.subheader(esc(names[pid]))
    rows = _guard(lambda: cohort.cohort_summary(cfg.db_path, user["id"], pid))
    if rows is None:
        return
    if not rows:
        st.write(t("empty_platoon"))
        return
    st.caption(_cadets(len(rows)))
    today = accom.today_of(cfg)
    with st.container(key="staff_cards"):
        for r in rows:
            _cadet_card(cfg, user, r, today)


# ------------------------------------------------------------------------------------------------------------ chief
def _metrics_present(ov: dict) -> list[str]:
    """Run, then walk only when some shown group has walk data, then push-ups."""
    out = ["run"]
    if any(not p["walk"].get("suppressed") for p in ov["platoons"]) or not ov["total"]["walk"].get("suppressed"):
        out.append("walk")
    out.append("pushups")
    return out


def _bar_rows(ov: dict, metric: str) -> list[tuple[str, dict | None]]:
    rows = []
    for p in ov["platoons"]:
        label = p["platoon"] if p["platoon"] is not None else t("unassigned")
        block = p[metric]
        rows.append((label, None if (p["suppressed"] or block.get("suppressed")) else block))
    return rows


def _totals(ov: dict, metrics: list[str]) -> None:
    """One tile per test: the share of the counted cadets who reached the pass level in that test on its own. There
    is no combined figure."""
    tiles = st.container(key="kpis_chief")
    cols = tiles.columns(len(metrics), wrap=False, gap="xsmall")
    for col, m in zip(cols, metrics):
        block = ov["total"][m]
        with col:
            if block.get("suppressed") or not block["n"]:
                st.metric(t(_TEST_KEY[m]), _DASH, border=True)
            else:
                st.metric(t(_TEST_KEY[m]), f"{round(100 * (block['excellent'] + block['pass']) / block['n'])}%", border=True)
    st.caption(t("chief_kpi_note"))


def _dashboard(ov: dict) -> None:
    """Per-platoon counts as bars (one test at a time), the totals as tiles, the table as the text alternative."""
    metrics = _metrics_present(ov)
    st.subheader(t("chief_aggregates_title"))
    st.caption(t("group_too_small"))
    _totals(ov, metrics)
    metric = st.segmented_control(t("chief_chart_metric"), metrics, default=metrics[0], key="chief_metric", required=True,
                                  width="stretch", label_visibility="collapsed", format_func=lambda m: t(_TEST_KEY[m]))
    rows = _bar_rows(ov, metric)
    if not rows:
        return
    with st.container(key="chart_card_platoons"):
        st.plotly_chart(charts.platoon_figure(rows), config=charts.PLOTLY_CONFIG, theme=None, width="stretch",
                        key="chief_chart", alt=t("chief_chart_alt"))
    with st.expander(t("chart_table")):
        st.table(pd.DataFrame(charts.platoon_table(rows)).set_index(t("col_platoon")))


def _sync_snapshots(rec_id: int, outcome: str, ok: bool) -> None:
    """After a decision on a request: the opened cadet and the platoon list are snapshots in session state. Mark the
    record in them (no extra read, so no extra audit row); if storage refused (already decided elsewhere) drop them so
    the next run reads the truth."""
    s = st.session_state
    if not ok:
        s.pop(_OPEN, None)
        s.pop(_ROWS, None)
        return
    lists = [s[_OPEN]["data"].get("accommodations", [])] if s.get(_OPEN) else []
    lists += [row.get("accommodations", []) for row in s[_ROWS]["rows"]] if s.get(_ROWS) else []
    for recs in lists:
        for rec in recs:
            if rec["id"] == rec_id:
                rec["approval"] = outcome


def _decision_buttons(cfg: Config, user: dict, rec_id: int, *, prefix: str) -> None:
    """Approve / reject one pending accommodation request (the same two buttons in the request list and in the opened
    cadet). Approve is a single tap: the chief has the whole record in front of them and can still delete it. Reject asks
    once: it is final and the cadet would have to send a new request."""
    c1, c2 = st.columns(2, wrap=False, gap="xsmall")
    with c1:
        if st.button(t("btn_approve_request"), key=f"{prefix}_ok_{rec_id}", type="primary", width="stretch",
                     icon=":material/check:"):
            accom.decide_request(cfg, user["id"], rec_id, approve=True, on_done=partial(_sync_snapshots, rec_id, "approved"))
            st.rerun()
    with c2:
        if st.button(t("btn_reject_request"), key=f"{prefix}_no_{rec_id}", width="stretch", icon=":material/close:"):
            accom.reject_request_dialog(cfg, user, rec_id, partial(_sync_snapshots, rec_id, "rejected"))


def _chief_requests(cfg: Config, user: dict) -> None:
    """Accommodation REQUESTS from cadets waiting for the chief (approve or reject; not the deletion requests below).
    Read once (kept under a '_' key), re-read after an action, so a rerun is not another audited read."""
    held = st.session_state.get(_REQUESTS)
    if held is None:
        held = _guard(lambda: accommodations.pending_requests(cfg.db_path, user["id"]))
        if held is None:
            return
        st.session_state[_REQUESTS] = held
    st.subheader(t("chief_requests_title"))
    if not held:
        st.caption(t("chief_requests_none"))
        return
    st.markdown(f"**{t('chief_requests_one') if len(held) == 1 else t('chief_requests_many', n=len(held))}**")
    today = accom.today_of(cfg)
    for p in held:
        with st.container(border=True, key=f"request_{p['id']}"):
            st.text(p["cadet_name"])
            if p.get("platoon"):
                st.caption(esc(p["platoon"]))
            accom.record_row({**p, "approval": "pending"}, today, key=f"request_rec_{p['id']}")
            if p.get("created_at"):
                st.caption(iso(t("acc_submitted_on", date=accom.dmy(p["created_at"][:10]))))
            _decision_buttons(cfg, user, p["id"], prefix="req")


def _pending(cfg: Config, user: dict) -> None:
    """DELETION requests (an instructor asked to delete a record) waiting for the chief. Read once (kept under a '_'
    key), re-read after an action."""
    held = st.session_state.get(_PENDING)
    if held is None:
        held = _guard(lambda: accommodations.pending_deletions(cfg.db_path, user["id"]))
        if held is None:
            return
        st.session_state[_PENDING] = held
    st.subheader(t("chief_pending_title"))
    if not held:
        st.caption(t("chief_pending_none"))
        return
    st.markdown(f"**{t('chief_pending_one') if len(held) == 1 else t('chief_pending_many', n=len(held))}**")
    today = accom.today_of(cfg)
    for p in held:
        with st.container(border=True, key=f"pending_{p['id']}"):
            st.text(p["cadet_name"])
            if p.get("platoon"):
                st.caption(esc(p["platoon"]))
            accom.record_row({**p, "delete_requested": True}, today, key=f"pending_rec_{p['id']}", show_status=False)
            if p.get("requested_by_name"):
                st.text(f"{t('acc_requested_by_label')} {p['requested_by_name']}")
            # stacked, full width: "דחיית בקשת המחיקה" is too long for half a 390 px card (it was cut with "...")
            if st.button(t("btn_approve_delete"), key=f"pend_ok_{p['id']}", type="primary", width="stretch",
                         icon=":material/delete:"):
                accom.approve_dialog(cfg, user, p["id"])
            if st.button(t("btn_reject_delete"), key=f"pend_no_{p['id']}", width="stretch", icon=":material/close:"):
                problem = accom.apply_reject(cfg, user["id"], p["id"])
                st.session_state.pop(_PENDING, None)
                st.session_state["_notice_error" if problem else "_notice"] = problem or t("acc_rejected")
                st.rerun()


def _cadet_details(cfg: Config, user: dict, data: dict, today) -> None:
    """Everything about ONE cadet, drawn under their row: tiles, status per test, the progress chart, accommodations."""
    cadet, results = data["cadet"], data["results"]
    aero = "walk" if cadet["is_km"] else "run"
    if not results:
        st.caption(t("no_results_cadet"))
    else:
        personal._kpis(results, cadet, key="kpis_cadet")
        lr = next((r for r in results if r["run_tenths"] is not None), None)
        lp = next((r for r in results if r["pushups"] is not None), None)
        if lr:
            personal._status_block(_TEST_KEY[aero], time_text(lr["run_tenths"]),
                                   grade_value(aero, cadet["gender"], lr["test_date"], lr["run_tenths"]))
        if lp:
            personal._status_block("test_pushups", str(lp["pushups"]),
                                   grade_value("pushups", cadet["gender"], lp["test_date"], lp["pushups"]))
        charts.render(results, cadet, key="chief_cadet_chart")
    st.subheader(t("acc_title"))
    if data["accommodations"]:
        _records(cfg, user, cadet["id"], data["accommodations"], today, can_request=False, can_delete=True, can_decide=True)
    else:
        st.caption(t("acc_none"))
    if st.button(t("btn_add_accommodation"), key=f"chief_accadd_{cadet['id']}", icon=":material/add:", width="stretch"):
        accom.add_dialog(cfg, user, cadet["id"])
    if results:
        st.subheader(t("history_title"))
        for i, r in enumerate(results):
            _result_card(cadet, r, f"chief_res_{i}")


def _cadet_row(cfg: Config, user: dict, r: dict, today) -> None:
    run_test = "walk" if r["is_km"] else "run"
    lr, lp = r["latest_run"], r["latest_pushups"]
    opened = st.session_state.get(_OPEN)
    is_open = opened is not None and opened["cadet"] == r["id"]
    with st.container(border=True, key=f"chief_row_{r['id']}"):
        st.text(r["name"])
        marker = f" · {t('acc_active_marker')}" if accom.active_count(r.get("accommodations", []), today) else ""
        st.caption(t("staff_gender", gender=_gender_word(r["gender"])) + marker)
        # An opened row does not repeat the latest values and their status: the details below show them (once).
        if lr is not None and not is_open:
            _test_row(run_test, time_text(lr["run_tenths"]), _level(run_test, r["gender"], lr["test_date"], lr["run_tenths"]), lr["test_date"])
        if lp is not None and not is_open:
            _test_row("pushups", str(lp["pushups"]), _level("pushups", r["gender"], lp["test_date"], lp["pushups"]), lp["test_date"])
        if lr is None and lp is None and not is_open:
            st.caption(t("no_results_cadet"))
        if is_open:
            if st.button(t("btn_close_details"), key=f"chief_close_{r['id']}", width="stretch", icon=":material/expand_less:"):
                st.session_state.pop(_OPEN, None)
                st.rerun()
            st.success(t("chief_opened_logged"), icon=":material/fact_check:")
            _cadet_details(cfg, user, opened["data"], today)
        elif st.button(t("btn_open_details"), key=f"chief_open_{r['id']}", width="stretch", icon=":material/expand_more:"):
            data = _guard(lambda: chief.get_cadet_results(cfg.db_path, user["id"], r["id"]))      # the named, audited read
            if data is not None:
                st.session_state[_OPEN] = {"cadet": r["id"], "data": data}
                st.rerun()


def _platoons(cfg: Config, user: dict, ov: dict) -> None:
    st.subheader(t("chief_drill_title"))
    st.caption(t("chief_list_notice"))
    st.caption(t("chief_open_notice"))
    names = {(p["platoon_id"] or _UNASSIGNED): (p["platoon"] if p["platoon"] is not None else t("unassigned"))
             for p in ov["platoons"]}
    if not names:
        return
    sel = st.segmented_control(t("pick_platoon"), list(names), key="chief_platoon", width="stretch",
                               format_func=lambda i: esc(names[i])) if len(names) <= 4 else \
        st.selectbox(t("pick_platoon"), list(names), index=None, key="chief_platoon", filter_mode=None,
                     placeholder=t("choose_placeholder"), format_func=names.get)
    if sel is None:
        st.session_state.pop(_ROWS, None)
        st.session_state.pop(_OPEN, None)
        st.caption(t("chief_pick_hint"))
        return
    held = st.session_state.get(_ROWS)
    if held is None or held["platoon"] != sel:             # one audited read per chosen platoon, not one per rerun
        rows = _guard(lambda: chief.platoon_summary(cfg.db_path, user["id"], None if sel == _UNASSIGNED else sel))
        if rows is None:
            return
        held = {"platoon": sel, "rows": rows}
        st.session_state[_ROWS] = held
        st.session_state.pop(_OPEN, None)
    if not held["rows"]:
        st.write(t("empty_platoon"))
        return
    st.caption(_cadets(len(held["rows"])))
    today = accom.today_of(cfg)
    with st.container(key="chief_cards"):
        for r in held["rows"]:
            _cadet_row(cfg, user, r, today)


def chief_page(cfg: Config, user: dict) -> None:
    with st.container(key="staff_page"):
        _chief_body(cfg, user)


def _chief_body(cfg: Config, user: dict) -> None:
    st.title(t("chief_title"))
    _banner(t("chief_banner"))
    accom.take_notice()
    ov = _guard(lambda: chief.overview(cfg.db_path, user["id"], grade_stored_value))
    if ov is None:
        return
    _chief_requests(cfg, user)
    _pending(cfg, user)
    _dashboard(ov)
    report_section.render(cfg, user)
    _platoons(cfg, user, ov)
