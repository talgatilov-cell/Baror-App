"""Instructor (own platoon) and chief (all platoons) screens. Only reachable when BAROR_COHORT_VIEWS=1 and the account has
the role; authorisation is nevertheless enforced again inside ``baror.storage.cohort`` / ``chief`` / ``people`` on every
call, and every named read is audited there.

Design at 390 px: one card per cadet showing the NAME ONLY (no table). The name is a button: a press opens the card (best
results, accommodations, progress chart, full history, and the actions on that cadet), a second press closes it. Each test
is graded on its own. There is NO overall verdict anywhere (the owner has not yet explained "כ.מ" and the overall rule).

Safety: a cadet's name is user-typed text. It is only ever shown with ``st.text`` / ``st.subheader(esc(...))`` / widget
labels passed through ``esc`` and NEVER inside ``st.html`` or ``unsafe_allow_html``. The only Markdown built here contains
fixed copy, statuses and numbers.

Reads and audit (nothing is read for a closed card):
* The member list (names only) of the instructor's platoon / the chosen platoon is read once and kept in ``st.session_state``
  under a key starting with "_" (removed at sign-out); it is read again after an action here and after two minutes. One
  platoon-level audit row per read, never one per cadet.
* A cadet's details are read, and audited, only when the name is pressed (``cohort.cadet_history`` /
  ``chief.get_cadet_results``) and kept under ``_staff_open`` / ``_chief_open``; a rerun is not a second read.
* The chief's request cards are read only while open (see ``_requests``); the cadet-change list is in ``requests_section``.

Who may change what is storage's decision (``baror.storage.people``): an instructor's edit, removal and add-of-a-cadet-from-
another-platoon become REQUESTS for the chief or admin; a מ"מ can also nominate a מ"כ. This module draws and forwards."""
from __future__ import annotations

import time
from functools import partial

import pandas as pd
import streamlit as st

from baror.config import Config
from baror.domain.entry import grade_value
from baror.domain.grading import grade_stored_value
from baror.errors import BarorError
from baror.messages_he import render_exception, t
from baror.storage import accommodations, chief, cohort, notifications, people
from baror.ui import accom, charts, personal, report_section, requests_section
from baror.ui.components import iso, status_badge
from baror.ui.format import time_text
from baror.ui.text import esc

try:                                         # shared admin components (built by the admin-screen workstream)
    from baror.ui import accounts_panel
except ImportError:                          # pragma: no cover - until that module exists the section is left out
    accounts_panel = None
try:
    from baror.ui import cadet_admin
except ImportError:                          # pragma: no cover
    cadet_admin = None

_LEVELS = ("excellent", "pass", "fail")
_METRIC_KEY = {"run": "test_run", "walk": "test_walk", "pushups": "kpi_pushups"}   # short column heads
_TEST_KEY = {"run": "test_run", "walk": "test_walk", "pushups": "test_pushups"}
_BEST_KEY = {"run": "col_best_run", "walk": "col_best_walk", "pushups": "col_best_pushups"}
_DASH = "—"
_INS_OPEN = "_staff_open"             # instructor: {"cadet": id, "data": cadet_history(...)} of the ONE opened cadet (read only after the press)
_UNASSIGNED = 0                      # a selectbox returns None for "nothing chosen", so "no platoon" needs its own value
# The kept member list (names only, one platoon-level audit row per read): {"platoon": id, "rows": [...], "at": monotonic
# seconds}. ONE key for both roles (an account has one role, and sign-out removes every '_' key): the shared cadet
# dialogs of ``cadet_admin`` drop "_chief_rows" after every change, so the list is read again.
_MEMBERS = "_chief_rows"
_OPEN = "_chief_open"                 # {"cadet": id, "data": {...}}     (read only after the press on the name)
_PENDING = "_chief_pending"           # pending DELETION requests (read when that card is open, re-read after an action)
_REQUESTS = "_chief_requests"         # pending accommodation REQUESTS from cadets (same)
_REQ_OPEN = "_req_open"               # which of the three request cards is open: "approve" | "delete" | "changes" | None
_REQ_FOCUS = "_req_focus"             # set by the top-bar bell: open that card
_TTL = 120                            # seconds before a kept member list is read again


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


def _fresh(held: dict | None, platoon) -> bool:
    return held is not None and held["platoon"] == platoon and time.monotonic() - held["at"] < _TTL


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


def _gender_word(gender: str) -> str:
    return t("gender_female" if gender == "F" else "gender_male")


def _identity(cadet: dict) -> None:
    """Gender (a plain word) and, for the walk track, the כ.מ marker (no dates: it is a setting, not a record)."""
    st.caption(t("staff_gender", gender=_gender_word(cadet["gender"])))
    if cadet["is_km"]:
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


def _best_row(test: str, value_text: str, level: str | None) -> None:
    """'שיא ריצה 09:40' beside its status badge. Fixed copy, numbers and a status only."""
    left, right = st.columns([13, 8], gap="xsmall", vertical_alignment="center", wrap=False)
    left.markdown(f"**{t(_BEST_KEY[test])}** {iso(value_text)}")
    right.markdown(status_badge(level))


def _best_rows(data: dict) -> None:
    """The BEST value of each test with its own status. A best value is judged with the rules of the date of that test's
    latest result (no per-best date is read)."""
    cadet, results, best = data["cadet"], data["results"], data["best"]
    run_test = "walk" if cadet["is_km"] else "run"
    lr = next((x for x in results if x["run_tenths"] is not None), None)
    lp = next((x for x in results if x["pushups"] is not None), None)
    if lr is not None and best["run_tenths"] is not None:
        _best_row(run_test, time_text(best["run_tenths"]), _level(run_test, cadet["gender"], lr["test_date"], best["run_tenths"]))
    if lp is not None and best["pushups"] is not None:
        _best_row("pushups", str(best["pushups"]), _level("pushups", cadet["gender"], lp["test_date"], best["pushups"]))


def _progress(cadet: dict, results: list[dict], chart_key: str, prefix: str) -> None:
    """One cadet's progress: tiles, a status per test (latest result), the progress chart (with its data table) and the
    full history (every stored result, newest first) in an expander."""
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


def _name_button(name: str, *, key: str, is_open: bool) -> bool:
    """The card's heading is a button with the cadet's name: it opens or closes the card. The name is escaped (labels are
    Markdown); the state is also in the tooltip, not only in the icon."""
    return st.button(esc(name), key=key, width="stretch", help=t("btn_close_details" if is_open else "btn_open_details"),
                     icon=":material/expand_less:" if is_open else ":material/expand_more:")


# ------------------------------------------------------------------------------------------------------ instructor
def _sent(notice_key: str) -> None:
    """After a successful nomination: tell, drop the kept member list and redraw."""
    st.session_state["_notice"] = t(notice_key)
    st.session_state.pop(_MEMBERS, None)
    st.rerun()


# Add, edit, move and remove of a cadet, and the join link, are the shared components of ``baror.ui.cadet_admin`` (they send a
# REQUEST when the actor is an instructor, and apply at once for the admin / chief: storage decides). Only the nomination
# of a מ"כ, which exists for the מ"מ alone, is drawn here.
@st.dialog(t("nominate_title"))
def nominate_dialog(cfg: Config, user: dict) -> None:
    """A מ"מ proposes a registered cadet as מ"כ of the own platoon; the chief or admin decides."""
    st.write(t("nominate_help"))
    email = st.text_input(t("field_nominee_email"), max_chars=254, key="nominate_email")
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        go = st.button(t("btn_nominate"), type="primary", width="stretch", key="nominate_ok")
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key="nominate_cancel"):
            st.rerun()
    if go:
        try:
            people.nominate_cmdr(cfg.db_path, user["id"], email)
        except BarorError as exc:
            st.error(render_exception(exc), icon=":material/error:")
            return
        st.session_state.pop("nominate_email", None)
        _sent("nominate_sent")


def _ins_details(cfg: Config, user: dict, data: dict, today) -> None:
    """The inside of an opened instructor card, built from the one named read."""
    cadet, results = data["cadet"], data["results"]
    st.success(t("chief_opened_logged"), icon=":material/fact_check:")
    _identity(cadet)
    if not results:
        st.caption(t("no_results_cadet"))
    _best_rows(data)
    st.markdown(f"**{t('acc_title')}**")
    if data["accommodations"]:
        _records(cfg, user, cadet["id"], data["accommodations"], today, can_request=True, can_delete=False)
    else:
        st.caption(t("acc_none"))
    if st.button(t("btn_add_accommodation"), key=f"accadd_{cadet['id']}", icon=":material/add:", width="stretch",
                 type="tertiary"):
        accom.add_dialog(cfg, user, cadet["id"])
    if results:
        _progress(cadet, results, "ins_cadet_chart", "ins")
    if cadet_admin is not None:
        full = {k: cadet.get(k) for k in ("id", "name", "gender", "is_km")}
        full["platoon_id"] = user.get("platoon_id")                # the cadet is in the instructor's own platoon
        c1, c2 = st.columns(2, gap="xsmall", wrap=False)
        with c1:
            if st.button(t("btn_request_edit"), key=f"insedit_{cadet['id']}", icon=":material/edit:", width="stretch"):
                cadet_admin.edit_dialog(cfg, user, full)
        with c2:
            if st.button(t("btn_request_remove"), key=f"insrm_{cadet['id']}", icon=":material/person_remove:",
                         width="stretch"):
                cadet_admin.remove_dialog(cfg, user, full)


def _cadet_card(cfg: Config, user: dict, m: dict, today) -> None:
    """A card with the name only; the details are read, and audited, when the name is pressed (one cadet at a time)."""
    opened = st.session_state.get(_INS_OPEN)
    is_open = opened is not None and opened["cadet"] == m["id"]
    with st.container(border=True, key=f"cadet_{m['id']}"):
        if is_open:
            if _name_button(m["name"], key=f"ins_close_{m['id']}", is_open=True):
                st.session_state.pop(_INS_OPEN, None)
                st.rerun()
            _ins_details(cfg, user, opened["data"], today)
        elif _name_button(m["name"], key=f"ins_open_{m['id']}", is_open=False):
            data = _guard(lambda: cohort.cadet_history(cfg.db_path, user["id"], m["id"]))      # the named, audited read
            if data is not None:
                st.session_state[_INS_OPEN] = {"cadet": m["id"], "data": data}
                st.rerun()


def _members_of_platoon(cfg: Config, user: dict, pid: int) -> list[dict] | None:
    held = st.session_state.get(_MEMBERS)
    if not _fresh(held, pid):                  # one platoon-level audit row per read, not one per rerun
        rows = _guard(lambda: cohort.list_platoon_members(cfg.db_path, user["id"], pid))
        if rows is None:
            return None
        held = {"platoon": pid, "rows": rows, "at": time.monotonic()}
        st.session_state[_MEMBERS] = held
    return held["rows"]


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
    pid, pname = platoons[0]["id"], platoons[0]["name"]            # an instructor belongs to exactly one platoon
    st.subheader(esc(pname))
    c1, c2 = st.columns(2, gap="xsmall", wrap=False)
    with c1:
        if cadet_admin is not None and st.button(t("btn_add_cadet"), key="ins_add_cadet", icon=":material/person_add:",
                                                 width="stretch"):
            cadet_admin.add_dialog(cfg, user, pid)
    if user.get("title") == "mm":                                  # only a platoon commander proposes his commanders
        with c2:
            if st.button(t("nominate_title"), key="ins_nominate", icon=":material/military_tech:", width="stretch"):
                nominate_dialog(cfg, user)
    rows = _members_of_platoon(cfg, user, pid)
    if rows is None:
        return
    if not rows:
        st.write(t("empty_platoon"))
    else:
        st.caption(_cadets(len(rows)))
        today = accom.today_of(cfg)
        with st.container(key="staff_cards"):
            for m in rows:
                _cadet_card(cfg, user, m, today)
    if cadet_admin is not None:
        with st.expander(t("staff_join_title")):
            cadet_admin.join_link_box(cfg, user, pid)
    with st.expander(t("ins_requests_title")):
        requests_section.render_mine(cfg, user)


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
        # text on purpose: a column that mixes the dash of a hidden group with counts cannot be sent as numbers
        st.table(pd.DataFrame(charts.platoon_table(rows)).set_index(t("col_platoon")).astype(str))


def _sync_snapshots(rec_id: int, outcome: str, ok: bool) -> None:
    """After a decision on a request: the opened cadet is a snapshot in session state. Mark the record in it (no extra
    read, so no extra audit row); if storage refused (already decided elsewhere) drop it so the next run reads the truth."""
    s = st.session_state
    if not ok:
        s.pop(_OPEN, None)
        return
    for rec in (s[_OPEN]["data"].get("accommodations", []) if s.get(_OPEN) else []):
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
    Read when the card is opened (kept under a '_' key), re-read after an action, so a rerun is not another audited read."""
    held = st.session_state.get(_REQUESTS)
    if held is None:
        held = _guard(lambda: accommodations.pending_requests(cfg.db_path, user["id"]))
        if held is None:
            return
        st.session_state[_REQUESTS] = held
    if not held:
        st.caption(t("chief_requests_none"))
        return
    st.markdown(f"**{t('chief_requests_one') if len(held) == 1 else t('chief_requests_many', n=len(held))}**")
    today = accom.today_of(cfg)
    for p in held:
        with st.container(border=True, key=f"request_{p['id']}"):
            st.markdown(f"**{t('req_acc_card_title')}**")
            st.text(p["cadet_name"])
            if p.get("platoon"):
                st.caption(esc(p["platoon"]))
            accom.record_row({**p, "approval": "pending"}, today, key=f"request_rec_{p['id']}")
            if p.get("created_at"):
                st.caption(iso(t("acc_submitted_on", date=accom.dmy(p["created_at"][:10]))))
            _decision_buttons(cfg, user, p["id"], prefix="req")


def _pending(cfg: Config, user: dict) -> None:
    """DELETION requests (an instructor asked to delete a record) waiting for the chief. Read when the card is opened
    (kept under a '_' key), re-read after an action."""
    held = st.session_state.get(_PENDING)
    if held is None:
        held = _guard(lambda: accommodations.pending_deletions(cfg.db_path, user["id"]))
        if held is None:
            return
        st.session_state[_PENDING] = held
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


_CARDS = (("approve", "req_card_approve", "accommodation_requests", _chief_requests),
          ("delete", "req_card_delete", "deletion_requests", _pending),
          ("changes", "req_card_changes", "change_requests", requests_section.render_changes))


def _requests(cfg: Config, user: dict) -> None:
    """The LAST section: three collapsed cards. Only the OPEN card is read. The bell sets ``_req_focus`` to open one."""
    st.subheader(t("req_section_title"))
    focus = st.session_state.pop(_REQ_FOCUS, None)
    if focus in {c[0] for c in _CARDS}:
        st.session_state[_REQ_OPEN] = focus
    try:                                                              # numbers only, not audited on success
        counts = notifications.counts(cfg.db_path, user["id"])
    except BarorError:
        counts = {}
    if _REQ_OPEN not in st.session_state:                             # first visit: open the card if only one has requests
        waiting = [c[0] for c in _CARDS if counts.get(c[2])]
        st.session_state[_REQ_OPEN] = waiting[0] if len(waiting) == 1 else None
    open_now = st.session_state.get(_REQ_OPEN)
    for name, label_key, count_key, body in _CARDS:
        is_open = open_now == name
        label = t(label_key) if count_key not in counts else t("req_card_label", label=t(label_key), n=counts[count_key])
        with st.container(border=True, key=f"reqcard_{name}"):
            if st.button(label, key=f"reqcard_btn_{name}", width="stretch",
                         icon=":material/expand_less:" if is_open else ":material/expand_more:"):
                for stale in (_REQUESTS, _PENDING, "_changes_pending"):      # opening reads fresh, once
                    st.session_state.pop(stale, None)
                st.session_state[_REQ_OPEN] = None if is_open else name
                st.rerun()
            if is_open:
                body(cfg, user)


def _cadet_actions(cfg: Config, user: dict, cadet: dict) -> None:
    """Edit / move / remove the opened cadet (admin components, built by the admin-screen workstream)."""
    if cadet_admin is None:
        return
    full = {k: cadet.get(k) for k in ("id", "name", "gender", "is_km", "platoon_id")}
    cols = st.columns(3, gap="xsmall", wrap=False)
    for col, (label, name, icon) in zip(cols, (("btn_staff_edit", "edit_dialog", ":material/edit:"),
                                               ("btn_staff_move", "move_dialog", ":material/swap_horiz:"),
                                               ("btn_staff_remove", "remove_dialog", ":material/person_remove:"))):
        fn = getattr(cadet_admin, name, None)
        if fn is None:
            continue
        with col:
            if st.button(t(label), key=f"chief_{name}_{cadet['id']}", icon=icon, width="stretch"):
                fn(cfg, user, full)


def _cadet_details(cfg: Config, user: dict, data: dict, today) -> None:
    """Everything about ONE cadet, drawn under their name: tiles, status per test, the progress chart, accommodations."""
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
    _cadet_actions(cfg, user, cadet)
    if results:
        st.subheader(t("history_title"))
        for i, r in enumerate(results):
            _result_card(cadet, r, f"chief_res_{i}")


def _cadet_row(cfg: Config, user: dict, m: dict, today) -> None:
    """A card with the name only; the cadet is read, and audited, when the name is pressed (one cadet at a time)."""
    opened = st.session_state.get(_OPEN)
    is_open = opened is not None and opened["cadet"] == m["id"]
    with st.container(border=True, key=f"chief_row_{m['id']}"):
        if is_open:
            if _name_button(m["name"], key=f"chief_close_{m['id']}", is_open=True):
                st.session_state.pop(_OPEN, None)
                st.rerun()
            st.success(t("chief_opened_logged"), icon=":material/fact_check:")
            _identity(opened["data"]["cadet"])
            _cadet_details(cfg, user, opened["data"], today)
        elif _name_button(m["name"], key=f"chief_open_{m['id']}", is_open=False):
            data = _guard(lambda: chief.get_cadet_results(cfg.db_path, user["id"], m["id"]))      # the named, audited read
            if data is not None:
                st.session_state[_OPEN] = {"cadet": m["id"], "data": data}
                st.rerun()


def _platoon_tools(cfg: Config, user: dict, pid: int) -> None:
    """Add a registered cadet to the chosen platoon, and the join link of it (not for the 'no platoon' group)."""
    if cadet_admin is None:
        return
    if st.button(t("btn_add_cadet"), key="chief_add_cadet", icon=":material/person_add:", width="stretch"):
        cadet_admin.add_dialog(cfg, user, pid)
    with st.expander(t("staff_join_title")):
        cadet_admin.join_link_box(cfg, user, pid)


def _platoons(cfg: Config, user: dict, ov: dict) -> None:
    st.subheader(t("chief_results_title"))
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
        st.session_state.pop(_MEMBERS, None)
        st.session_state.pop(_OPEN, None)
        st.caption(t("chief_pick_hint"))
        return
    held = st.session_state.get(_MEMBERS)
    if not _fresh(held, sel):                                # one platoon-level audit row per read, not one per rerun
        rows = _guard(lambda: chief.list_platoon_members(cfg.db_path, user["id"], None if sel == _UNASSIGNED else sel))
        if rows is None:
            return
        if held is None or held["platoon"] != sel:
            st.session_state.pop(_OPEN, None)
        held = {"platoon": sel, "rows": rows, "at": time.monotonic()}
        st.session_state[_MEMBERS] = held
    if sel != _UNASSIGNED:
        _platoon_tools(cfg, user, sel)
    if not held["rows"]:
        st.write(t("empty_platoon"))
        return
    st.caption(_cadets(len(held["rows"])))
    today = accom.today_of(cfg)
    with st.container(key="chief_cards"):
        for m in held["rows"]:
            _cadet_row(cfg, user, m, today)


def _accounts(cfg: Config, user: dict) -> None:
    """Instructors and permissions: the shared accounts panel of the admin-screen workstream (it draws its own heading)."""
    if accounts_panel is not None:
        accounts_panel.render(cfg, user)


def chief_page(cfg: Config, user: dict) -> None:
    with st.container(key="staff_page"):
        _chief_body(cfg, user)


def _chief_body(cfg: Config, user: dict) -> None:
    """Page one: dashboard, then the results and the platoon picker. (Requests: page two; reports and accounts: page three.)"""
    st.title(t("chief_title"))
    _banner(t("chief_banner"))
    accom.take_notice()
    ov = _guard(lambda: chief.overview(cfg.db_path, user["id"], grade_stored_value))
    if ov is None:
        return
    _dashboard(ov)
    _platoons(cfg, user, ov)


def requests_page(cfg: Config, user: dict) -> None:
    """The second page: the waiting requests (the bell leads here). Reports and accounts are on the third page."""
    with st.container(key="staff_page"):
        accom.take_notice()
        _requests(cfg, user)
