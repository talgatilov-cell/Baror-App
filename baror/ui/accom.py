"""Accommodations (הקלות ופטורים) on screen: one record row, the add dialog, the cadet's own tab, deletion requests.

Who may do what is decided by ``baror.storage.accommodations`` on every call (a cadet adds only for themselves and can
never edit or delete; an instructor requests deletion; the chief approves). This module only draws and forwards; the
SOURCE of a record is set by storage, never by a widget.

Safety: the note is typed by a person. It is shown with ``st.text`` only. Everything that goes into Markdown here is
fixed copy from ``baror.messages_he`` (kind, scope, source, status) or a date written by this module."""
from __future__ import annotations

import datetime as dt

import streamlit as st

from baror.config import Config
from baror.domain import accommodation as rules
from baror.errors import BarorError
from baror.logging_setup import log_event
from baror.messages_he import render_exception, t
from baror.storage import accommodations as store
from baror.storage import chief as chief_store
from baror.ui.components import iso

KIND_ICON = {"relief": "tune", "exemption": "do_not_disturb_on"}
STATUS_BADGE = {"active": ("green", "check_circle"), "upcoming": ("orange", "schedule"), "expired": ("gray", "history"),
                "pending": ("orange", "hourglass_empty"), "rejected": ("red", "cancel")}
_APPROVAL_STATES = ("pending", "rejected")         # statuses that come from the approval step: their words are acc_approval_*
NOTE_MAX = rules.NOTE_MAX


def dmy(iso_date: str) -> str:
    y, m, d = iso_date.split("-")
    return f"{d}/{m}/{y}"


def today_of(cfg: Config) -> dt.date:
    return dt.datetime.now(cfg.tz).date()


def kind_badge(rec: dict) -> str:
    return f":gray-badge[:material/{KIND_ICON[rec['kind']]}: {t('acc_kind_' + rec['kind'])}]"


def status_badge(rec: dict, today: dt.date) -> str:
    status = rules.status(rec, today)
    colour, icon = STATUS_BADGE[status]
    word = t(("acc_approval_" if status in _APPROVAL_STATES else "acc_status_") + status)
    return f":{colour}-badge[:material/{icon}: {word}]"


def source_badge(rec: dict) -> str:
    """A record recorded by the cadet is drawn differently (orange, person icon) so staff can check it."""
    if rec["source"] == "cadet":
        return f":orange-badge[:material/person: {t('acc_source_cadet')}]"
    return f":gray-badge[:material/verified_user: {t('acc_source_' + rec['source'])}]"


def active_count(records: list[dict], today: dt.date) -> int:
    return sum(1 for r in records if rules.status(r, today) == "active")


def record_row(rec: dict, today: dt.date, *, key: str, show_status: bool = True) -> None:
    """One record: kind and scope, dates, status (or approval state), source, pending deletion request, and the note as
    plain text. ``show_status=False`` leaves the status badge out (a deletion request's list does not carry the approval,
    so it must not guess one)."""
    with st.container(border=True, key=key):
        st.markdown(f"{kind_badge(rec)} **{t('acc_scope_' + rec['scope'])}**")
        dates = iso(t('acc_dates', start=dmy(rec['start_date']), end=dmy(rec['end_date'])))
        st.markdown(f"{dates} {status_badge(rec, today)}" if show_status else dates)
        if rec.get("note"):
            st.text(rec["note"])                                   # typed by a person: never Markdown or HTML
        badges = source_badge(rec)
        if rec.get("delete_requested"):
            badges += f" :orange-badge[:material/delete_sweep: {t('acc_pending_chief')}]"      # deletion, not approval
        st.markdown(badges)


# ----------------------------------------------------------------------------------------------- forwarding
def apply_add(cfg: Config, actor_id: int, cadet_id: int, *, kind, scope, start, end, note) -> str | None:
    """Record an accommodation through storage. Returns None on success, else the message to show (nothing stored)."""
    if kind is None or scope is None or start is None or end is None:
        return render_exception(BarorError("INVALID_ACCOMMODATION"))
    try:
        store.add(cfg.db_path, actor_id, cadet_id, kind=kind, scope=scope, start_date=start.isoformat(),
                  end_date=end.isoformat(), note=(note or "").strip() or None, today=today_of(cfg))
    except BarorError as exc:
        return render_exception(exc)
    log_event("accommodation_added", user_id=actor_id)
    return None


def apply_request_delete(cfg: Config, actor_id: int, rec_id: int) -> str | None:
    try:
        store.request_delete(cfg.db_path, actor_id, rec_id)
    except BarorError as exc:
        return render_exception(exc)
    log_event("accommodation_delete_requested", user_id=actor_id)
    return None


def apply_approve(cfg: Config, actor_id: int, rec_id: int) -> str | None:
    try:
        store.approve_delete(cfg.db_path, actor_id, rec_id)
    except BarorError as exc:
        return render_exception(exc)
    log_event("accommodation_deleted", user_id=actor_id)
    return None


def apply_reject(cfg: Config, actor_id: int, rec_id: int) -> str | None:
    try:
        store.reject_delete(cfg.db_path, actor_id, rec_id)
    except BarorError as exc:
        return render_exception(exc)
    log_event("accommodation_delete_rejected", user_id=actor_id)
    return None


def apply_approve_request(cfg: Config, actor_id: int, rec_id: int) -> str | None:
    """Chief: approve a pending accommodation REQUEST (the record comes into effect). Not a deletion."""
    try:
        store.approve_request(cfg.db_path, actor_id, rec_id)
    except BarorError as exc:
        return render_exception(exc)
    log_event("accommodation_request_approved", user_id=actor_id)
    return None


def apply_reject_request(cfg: Config, actor_id: int, rec_id: int) -> str | None:
    """Chief: reject a pending accommodation REQUEST (the row stays, marked rejected). Not a deletion."""
    try:
        store.reject_request(cfg.db_path, actor_id, rec_id)
    except BarorError as exc:
        return render_exception(exc)
    log_event("accommodation_request_rejected", user_id=actor_id)
    return None


# ----------------------------------------------------------------------------------------------- the form
def form_fields(cfg: Config, prefix: str):
    """Kind, scope, dates and note as widgets (native, 48 px). Keys start with ``prefix`` so the same form can sit in
    the cadet tab and in a dialog. Returns the chosen values (None while not chosen)."""
    today = today_of(cfg)
    kind = st.segmented_control(t("field_acc_kind"), ["relief", "exemption"], key=f"{prefix}_kind", width="stretch",
                                format_func=lambda k: t("acc_kind_" + k))
    scope = st.segmented_control(t("field_acc_scope"), ["run", "pushups", "both"], key=f"{prefix}_scope", width="stretch",
                                 format_func=lambda k: t("acc_scope_" + k))
    c1, c2 = st.columns(2, wrap=False, gap="small")
    with c1:
        start = st.date_input(t("field_acc_start"), value=today, format="DD/MM/YYYY", key=f"{prefix}_start")
    with c2:
        end = st.date_input(t("field_acc_end"), value=today, format="DD/MM/YYYY", key=f"{prefix}_end")
    note = st.text_input(t("field_acc_note"), max_chars=NOTE_MAX, key=f"{prefix}_note")
    return kind, scope, start, end, note


@st.dialog(t("acc_add_title"))
def add_dialog(cfg: Config, actor: dict, cadet_id: int) -> None:
    """The same fields for an instructor, the chief or the admin; storage decides whether the actor may add for this
    cadet and stamps the source."""
    kind, scope, start, end, note = form_fields(cfg, f"accd_{cadet_id}")
    problem = st.session_state.get(f"_accd_error_{cadet_id}")
    if problem:
        st.error(problem, icon=":material/error:")
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        save = st.button(t("btn_acc_save"), type="primary", width="stretch", key=f"accd_save_{cadet_id}")
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=f"accd_cancel_{cadet_id}"):
            st.session_state.pop(f"_accd_error_{cadet_id}", None)
            st.rerun()
    if save:
        problem = apply_add(cfg, actor["id"], cadet_id, kind=kind, scope=scope, start=start, end=end, note=note)
        if problem:
            st.session_state[f"_accd_error_{cadet_id}"] = problem
            st.rerun(scope="fragment")
        st.session_state.pop(f"_accd_error_{cadet_id}", None)
        st.session_state["_notice"] = t("acc_added")
        _refresh_chief_open(cfg, actor["id"], cadet_id)
        st.rerun()


def _refresh_chief_open(cfg: Config, actor_id: int, cadet_id: int) -> None:
    """The chief's opened-cadet view is a snapshot in session state. After an add it would still be the old one (the
    record is stored but not on screen, which looks like 'not saved'): re-read it (the same named, audited read as
    opening), or drop it if that read is refused."""
    held = st.session_state.get("_chief_open")
    if not held or held.get("cadet") != cadet_id:
        return
    try:
        held["data"] = chief_store.get_cadet_results(cfg.db_path, actor_id, cadet_id)
    except BarorError:
        st.session_state.pop("_chief_open", None)


@st.dialog(t("acc_request_title"))
def request_delete_dialog(cfg: Config, actor: dict, rec: dict) -> None:
    st.write(t("acc_request_body"))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        send = st.button(t("btn_send_request"), type="primary", width="stretch", key=f"req_ok_{rec['id']}")
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=f"req_cancel_{rec['id']}"):
            st.rerun()
    if send:
        problem = apply_request_delete(cfg, actor["id"], rec["id"])
        if problem:
            st.error(problem, icon=":material/error:")
            return
        st.session_state["_notice"] = t("acc_requested")
        st.session_state.pop("_chief_open", None)            # a stale copy of the record would still show no request
        st.rerun()


@st.dialog(t("acc_approve_title"))
def approve_dialog(cfg: Config, actor: dict, rec_id: int) -> None:
    st.write(t("acc_approve_body"))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        go = st.button(t("btn_approve_delete"), type="primary", width="stretch", icon=":material/delete:",
                       key=f"appr_ok_{rec_id}")
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=f"appr_cancel_{rec_id}"):
            st.rerun()
    if go:
        problem = apply_approve(cfg, actor["id"], rec_id)
        if problem:
            st.error(problem, icon=":material/error:")
            return
        st.session_state["_notice"] = t("acc_deleted")
        st.session_state.pop("_chief_open", None)
        st.session_state.pop("_chief_pending", None)
        st.session_state.pop("_chief_requests", None)         # the deleted record may have been waiting for approval
        st.rerun()


def decide_request(cfg: Config, actor_id: int, rec_id: int, *, approve: bool, on_done=None) -> None:
    """Approve or reject one accommodation request, leave the confirmation (or the refusal) as the next notice, and drop
    the pending-request list so the next run re-reads it (one audited read per action, not one per rerun).
    ``on_done(ok)`` lets the caller fix its own snapshots (it is told whether storage accepted the decision)."""
    fn, done = (apply_approve_request, "acc_request_approved") if approve else (apply_reject_request, "acc_request_rejected")
    problem = fn(cfg, actor_id, rec_id)
    st.session_state.pop("_chief_requests", None)
    st.session_state["_notice_error" if problem else "_notice"] = problem or t(done)
    if on_done:
        on_done(problem is None)


@st.dialog(t("acc_reject_request_title"))
def reject_request_dialog(cfg: Config, actor: dict, rec_id: int, on_done=None) -> None:
    """The one light confirmation of the request flow: a rejection cannot be undone (the decision is final and the
    cadet would have to send a new request), while an approval can still be removed by the chief."""
    st.write(t("acc_reject_request_body"))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        go = st.button(t("btn_reject_request"), type="primary", width="stretch", key=f"rej_ok_{rec_id}")
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=f"rej_cancel_{rec_id}"):
            st.rerun()
    if go:
        decide_request(cfg, actor["id"], rec_id, approve=False, on_done=on_done)
        st.rerun()


def take_notice() -> None:
    """Show (once) the confirmation, or the refusal, a dialog or button left behind."""
    note = st.session_state.pop("_notice", None)
    if note:
        st.success(note, icon=":material/check_circle:")
    problem = st.session_state.pop("_notice_error", None)
    if problem:
        st.error(problem, icon=":material/error:")


# ---------------------------------------------------------------------------------------- the cadet's own tab
def _on_save(cfg: Config, user: dict) -> None:
    s = st.session_state
    problem = apply_add(cfg, user["id"], user["id"], kind=s.get("acc_kind"), scope=s.get("acc_scope"),
                        start=s.get("acc_start"), end=s.get("acc_end"), note=s.get("acc_note"))
    s["_acc_error"] = problem
    if problem is None:
        s["_notice"] = t("acc_sent_for_approval")             # a cadet's own record waits for the chief
        s["_acc_reset"] = True


def cadet_tab(cfg: Config, user: dict) -> None:
    """More -> הקלות: the warning FIRST, then the form, then the cadet's own records (read-only)."""
    s = st.session_state
    st.subheader(t("acc_title"))
    with st.container(key="acc_warning"):
        st.write(t("acc_cadet_warning"))
    take_notice()
    if s.pop("_acc_reset", False):
        # assigned, not popped: a value set from the server reaches the browser; a popped key can keep the old text
        s.update({"acc_kind": None, "acc_scope": None, "acc_start": today_of(cfg), "acc_end": today_of(cfg), "acc_note": ""})
    form_fields(cfg, "acc")
    if s.get("_acc_error"):
        st.error(s["_acc_error"], icon=":material/error:")
    st.button(t("btn_acc_save"), type="primary", width="stretch", icon=":material/save:", on_click=_on_save,
              args=(cfg, user), key="acc_save")
    today = today_of(cfg)
    records = store.list_own(cfg.db_path, user["id"])
    if not records:
        st.caption(t("acc_none"))
        return
    for rec in records:
        record_row(rec, today, key=f"acc_own_{rec['id']}")
