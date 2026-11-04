"""Requests about cadets: the list the chief and the admin decide on (``render_changes``) and the list an instructor sees of
the requests they made (``render_mine``).

Kinds (``baror.storage.people``): ``add_conflict`` (an instructor added a cadet who belongs to another platoon), ``edit``
(name / gender / walk track), ``delete`` (remove the cadet from the platoon) and ``nominate_cmdr`` (a מ"מ proposes a
registered cadet as מ"כ). Who may decide, what a decision does and every re-validation is storage's job
(``people.approve_change`` / ``reject_change`` check the role again inside their own transaction); this module only draws
and forwards.

Reads and audit: ``people.pending_changes`` is not audited on success and is read when the list is drawn, which the
callers do only while the card is open; the result is kept under a '_' key and dropped after every decision, so a rerun is
not another read. Nothing here writes an audit row itself.

Grant-time safeguard for ``nominate_cmdr``: the dialog shows name, e-mail and registration time, needs the box "הזהות
אומתה מחוץ למערכת" ticked, and passes ``expected_name`` / ``expected_created_at`` (exactly what was shown) to storage, which
refuses with ACCOUNT_CHANGED when the account no longer matches.

Safety: names and e-mail addresses are typed by people. They are drawn with ``st.text`` only, and platoon names that go
into Markdown captions pass through ``esc``. Everything else is fixed copy from ``baror.messages_he``."""
from __future__ import annotations

import datetime as dt

import streamlit as st

from baror.config import Config
from baror.errors import BarorError
from baror.logging_setup import log_event
from baror.messages_he import render_exception, t
from baror.storage import people
from baror.ui.text import esc

_PENDING = "_changes_pending"          # open requests (read when the list is drawn, dropped after a decision)
_NOTICE = "_changes_notice"
_ERROR = "_changes_error"
_BADGE = {"pending": ("orange", "hourglass_empty"), "approved": ("green", "check_circle"), "rejected": ("gray", "cancel")}
# Snapshots elsewhere on the chief page that a decision may have made stale (a cadet moved, a role granted).
_STALE = ("_chief_rows", "_chief_open", "_staff_open")


def _when(cfg: Config, stamp: str | None) -> str:
    """'2026-11-04T08:00:00Z' -> '04/11/2026 10:00' in the course's time zone (the raw text if it cannot be read)."""
    if not stamp:
        return "—"
    try:
        moment = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(cfg.tz)
    except ValueError:
        return str(stamp)
    return moment.strftime("%d/%m/%Y %H:%M")


def _platoon(name: str | None) -> str:
    return name if name else t("unassigned")


def _gender(value: str) -> str:
    return t("gender_female" if value == "F" else "gender_male")


def _yes_no(value) -> str:
    return t("change_yes" if value else "change_no")


def _lines(r: dict) -> None:
    """What would change, as 'old ← new' lines (the arrow points the way the Hebrew line is read). Fixed copy and
    platoon names (escaped in Markdown); names typed by people go through ``st.text`` only."""
    kind = r["kind"]
    if kind == "add_conflict":
        st.caption(t("chg_platoon", old=esc(_platoon(r.get("target_platoon"))), new=esc(_platoon(r.get("platoon")))))
    elif kind == "nominate_cmdr":
        st.caption(t("chg_nominee_platoon", platoon=esc(_platoon(r.get("platoon")))))
    elif kind == "delete":
        st.caption(t("chg_platoon", old=esc(_platoon(r.get("target_platoon"))), new=esc(_platoon(None))))
    else:                                                               # edit
        if r.get("new_name") is not None:
            st.text(t("chg_name", old=r["target_name"], new=r["new_name"]))
        if r.get("new_gender") is not None:
            st.caption(t("chg_gender", old=_gender(r.get("target_gender") or "M"), new=_gender(r["new_gender"])))
        if r.get("new_is_km") is not None:
            st.caption(t("chg_km", old=_yes_no(r.get("target_is_km")), new=_yes_no(r["new_is_km"])))
        if r.get("new_platoon_id") is not None:
            st.caption(t("chg_platoon", old=esc(_platoon(r.get("target_platoon"))), new=esc(_platoon(r.get("new_platoon")))))


def _requester(r: dict) -> None:
    """'הוגשה על ידי <name> (מ״מ)'. The name is typed by a person: plain text. The title only when it is one of the two
    instructor titles."""
    name = r.get("requester_name")
    if not name:
        return
    title = r.get("requester_title")
    if title in ("mm", "cmdr"):
        st.text(t("change_by_titled", name=name, title=t("title_" + title)))
    else:
        st.text(f"{t('change_by')} {name}")


def _forget() -> None:
    for key in (_PENDING, *_STALE):
        st.session_state.pop(key, None)


def _done(problem: str | None, success_key: str) -> None:
    _forget()
    st.session_state[_ERROR if problem else _NOTICE] = problem or t(success_key)


def _approve(cfg: Config, user: dict, r: dict, **expected) -> str | None:
    try:
        people.approve_change(cfg.db_path, user["id"], r["id"], **expected)
    except BarorError as exc:
        return render_exception(exc)
    log_event("change_request_approved", user_id=user["id"])
    return None


def _reject(cfg: Config, user: dict, r: dict) -> str | None:
    try:
        people.reject_change(cfg.db_path, user["id"], r["id"])
    except BarorError as exc:
        return render_exception(exc)
    log_event("change_request_rejected", user_id=user["id"])
    return None


@st.dialog(t("grant_title"))
def grant_dialog(cfg: Config, user: dict, r: dict) -> None:
    """Approve a מ"כ nomination: the identity check. Name, e-mail and registration time are shown as read; the box must be
    ticked; the same name and time are passed to storage."""
    st.write(t("grant_intro"))
    st.text(t("grant_name", name=r["target_name"]))
    st.text(t("grant_email", email=r.get("target_email") or "—"))
    st.text(t("grant_registered", date=_when(cfg, r.get("target_created_at"))))
    st.caption(t("grant_platoon", platoon=esc(_platoon(r.get("platoon")))))
    verified = st.checkbox(t("grant_verified"), key=f"grant_verified_{r['id']}")
    if not verified:
        st.caption(t("grant_needs_check"))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        go = st.button(t("btn_grant_confirm"), type="primary", width="stretch", key=f"grant_ok_{r['id']}",
                       icon=":material/check:", disabled=not verified)
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=f"grant_cancel_{r['id']}"):
            st.rerun()
    if go and verified:
        problem = _approve(cfg, user, r, expected_name=r["target_name"], expected_created_at=r.get("target_created_at"))
        _done(problem, "change_approved")
        st.rerun()


def _card(cfg: Config, user: dict, r: dict) -> None:
    with st.container(border=True, key=f"change_{r['id']}"):
        st.markdown(f"**{t('req_kind_' + r['kind'])}**")
        st.caption(t("req_ask_" + r["kind"]))
        st.text(r["target_name"])
        _lines(r)
        _requester(r)
        st.caption(t("req_if_approved_" + r["kind"]) + " " + t("req_if_rejected"))
        if st.button(t("btn_approve_request"), key=f"change_ok_{r['id']}", type="primary", width="stretch",
                     icon=":material/check:"):
            if r["kind"] == "nominate_cmdr":
                grant_dialog(cfg, user, r)
            else:
                _done(_approve(cfg, user, r), "change_approved")
                st.rerun()
        if st.button(t("btn_reject_request"), key=f"change_no_{r['id']}", width="stretch", icon=":material/close:"):
            _done(_reject(cfg, user, r), "change_rejected")
            st.rerun()


def render_changes(cfg: Config, user: dict) -> None:
    """The open requests about cadets, with approve and reject. For the chief and the admin; the caller draws the card or
    section that holds it and calls this only while it is open (that is the only read)."""
    if st.session_state.get(_NOTICE):
        st.success(st.session_state.pop(_NOTICE), icon=":material/check_circle:")
    if st.session_state.get(_ERROR):
        st.error(st.session_state.pop(_ERROR), icon=":material/error:")
    held = st.session_state.get(_PENDING)
    if held is None:
        try:
            held = people.pending_changes(cfg.db_path, user["id"])
        except BarorError as exc:
            st.error(render_exception(exc), icon=":material/error:")
            return
        st.session_state[_PENDING] = held
    if not held:
        st.caption(t("change_none"))
        return
    for r in held:
        _card(cfg, user, r)


def render_mine(cfg: Config, user: dict) -> None:
    """The requests this instructor made (every status, newest first). Not audited on success; read on every run, which is
    one small query, so that a decision made by the chief shows without any action here."""
    try:
        rows = people.my_changes(cfg.db_path, user["id"])
    except BarorError as exc:
        st.error(render_exception(exc), icon=":material/error:")
        return
    if not rows:
        st.caption(t("ins_requests_none"))
        return
    for r in rows:
        colour, icon = _BADGE.get(r["status"], _BADGE["pending"])
        with st.container(border=True, key=f"mine_{r['id']}"):
            st.markdown(f"**{t('req_kind_' + r['kind'])}**")
            st.text(r["target_name"])
            _lines(r)
            st.markdown(f":{colour}-badge[:material/{icon}: {t('req_status_' + r['status'])}]")
