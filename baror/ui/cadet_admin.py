"""Cadet management dialogs, shared by the admin screen, the chief page and the instructor page.

Public entry points (the signatures other screens rely on):

    edit_dialog(cfg, user, cadet)     name, gender, כ.מ, platoon; ``cadet`` = {"id", "name", "gender", "is_km", "platoon_id"}
    move_dialog(cfg, user, cadet)     put the cadet into another platoon (admin / chief: at once; instructor: a request)
    remove_dialog(cfg, user, cadet)   REMOVE FROM THE PLATOON (the account and the results stay)
    add_dialog(cfg, user, platoon_id) add an already registered cadet by e-mail
    join_link_box(cfg, user, platoon_id)   the signed join link of one platoon (needs BAROR_JOIN_LINK_SECRET)

``cadet`` may also be a bare id: the dialog then cannot pre-fill and sends only what was filled in.

Who may do what, and whether a change is applied at once or becomes a request, is decided by ``baror.storage.people`` on
every call (the actor is re-read there). This module only draws and forwards; every refusal is shown through the Hebrew
error catalogue. A dialog's work sits in a ``*_body`` function so a test can draw it without the dialog container
(AppTest cannot rerun a dialog fragment).

Safety: names and e-mail addresses are typed by people: ``st.text`` or widget values only, never Markdown or HTML. The
join link holds digits, dots and hex only (signed by ``baror.domain.joinlink``) and is shown with ``st.code``."""
from __future__ import annotations

import datetime as dt

import streamlit as st

from baror.config import Config
from baror.errors import BarorError
from baror.logging_setup import log_event
from baror.messages_he import render_exception, t
from baror.storage import people, signup
from baror.ui.components import iso

UNASSIGNED = 0                       # a selectbox returns None for "nothing chosen", so "no platoon" needs its own value
JOIN_DAYS = (7, 14, 30)
# session snapshots that become stale after a change; popping a key nobody set is harmless
STALE = ("_acct_rows", "_adm_cadets", "_chief_rows", "_chief_open", "_staff_open", "_changes_pending")


def invalidate() -> None:
    """Drop the lists that were read before the change, so the next run reads the truth (one audited read per action)."""
    for key in STALE:
        st.session_state.pop(key, None)


def finish(message_key: str) -> None:
    """Leave the confirmation as the next notice, drop the stale lists, close the dialog."""
    st.session_state["_notice"] = t(message_key)
    invalidate()
    st.rerun()


def _cadet(cadet) -> dict:
    return dict(cadet) if isinstance(cadet, dict) else {"id": cadet}


def join_secret(cfg: Config) -> str | None:
    """The signing secret, or None while join links are off (config.py names it ``join_link_secret``)."""
    return getattr(cfg, "join_link_secret", None) or getattr(cfg, "join_secret", None) or None


def platoon_choices(cfg: Config) -> dict[int, str]:
    """Every platoon: id -> name (the same list the registration form shows)."""
    return {p["id"]: p["name"] for p in signup.public_platoons(cfg.db_path)}


def _button_pair(ok_label: str, ok_key: str, cancel_key: str, *, icon: str | None = None, disabled: bool = False,
                 danger: bool = False) -> bool:
    """The confirm button (primary) and the cancel button side by side; cancel closes the dialog. Returns 'confirm pressed'.
    A key starting with ``del_ok_`` gets the destructive look from the stylesheet."""
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        go = st.button(ok_label, type="primary", width="stretch", key=ok_key, icon=icon, disabled=disabled)
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=cancel_key):
            st.rerun()
    return go


def _show(exc: BarorError) -> None:
    st.error(render_exception(exc), icon=":material/error:")


# ------------------------------------------------------------------------------------------------------------ edit
def edit_body(cfg: Config, user: dict, cadet) -> None:
    c = _cadet(cadet)
    cid = c["id"]
    k = f"cedit_{cid}"
    by_instructor = user["role"] == "instructor"
    st.caption(t("adm_edit_request" if by_instructor else "adm_edit_direct"))
    name = st.text_input(t("field_name"), value=c.get("name") or "", max_chars=80, key=f"{k}_name")
    gender = st.segmented_control(t("field_gender"), ["M", "F"], default=c.get("gender") if c.get("gender") in ("M", "F") else None,
                                  key=f"{k}_gender", width="stretch",
                                  format_func=lambda g: t("gender_male" if g == "M" else "gender_female"))
    known_km = c.get("is_km")
    km = st.segmented_control(t("adm_field_km"), [1, 0], default=None if known_km is None else int(bool(known_km)),
                              key=f"{k}_km", width="stretch", format_func=lambda v: t("change_yes" if v else "change_no"))
    platoons = platoon_choices(cfg)
    current = c.get("platoon_id")
    if current is None and by_instructor:
        current = user.get("platoon_id")
    pid = st.selectbox(t("adm_move_field"), list(platoons), index=list(platoons).index(current) if current in platoons else None,
                       key=f"{k}_platoon", format_func=lambda i: platoons[i], placeholder=t("choose_placeholder"),
                       filter_mode=None)
    changes: dict = {}
    if name.strip() and name.strip() != (c.get("name") or ""):
        changes["name"] = name.strip()
    if gender in ("M", "F") and gender != c.get("gender"):
        changes["gender"] = gender
    if km is not None and (known_km is None or int(bool(known_km)) != km):
        changes["is_km"] = km
    if pid is not None and pid != c.get("platoon_id", current):
        changes["platoon_id"] = pid
    if not _button_pair(t("btn_save_changes"), f"{k}_save", f"{k}_cancel", icon=":material/save:"):
        return
    if not changes:
        st.info(t("adm_edit_nothing"))
        return
    try:
        out = people.edit_cadet(cfg.db_path, user["id"], cid, changes)
    except BarorError as exc:
        _show(exc)
        return
    log_event("cadet_edit_requested" if out["status"] == "requested" else "cadet_edited", user_id=user["id"])
    finish("adm_edit_requested" if out["status"] == "requested" else "adm_edit_done")


@st.dialog(t("adm_edit_title"))
def edit_dialog(cfg: Config, user: dict, cadet) -> None:
    edit_body(cfg, user, cadet)


# ------------------------------------------------------------------------------------------------------------ move
def move_body(cfg: Config, user: dict, cadet) -> None:
    c = _cadet(cadet)
    cid = c["id"]
    k = f"cmove_{cid}"
    by_instructor = user["role"] == "instructor"
    platoons = platoon_choices(cfg)
    options = list(platoons) if by_instructor else list(platoons) + [UNASSIGNED]
    label = lambda i: t("unassigned") if i == UNASSIGNED else platoons[i]          # noqa: E731
    if by_instructor:
        st.caption(t("adm_edit_request"))
    target = st.selectbox(t("adm_move_field"), options, index=None, key=f"{k}_to", format_func=label,
                          placeholder=t("choose_placeholder"), filter_mode=None)
    if not _button_pair(t("btn_adm_move_ok"), f"{k}_ok", f"{k}_cancel", icon=":material/swap_horiz:"):
        return
    if target is None:
        st.warning(t("adm_move_nothing"))
        return
    try:
        if by_instructor:
            out = people.edit_cadet(cfg.db_path, user["id"], cid, {"platoon_id": target})
        else:
            out = people.move_cadet(cfg.db_path, user["id"], cid, None if target == UNASSIGNED else target)
    except BarorError as exc:
        _show(exc)
        return
    log_event("cadet_moved", user_id=user["id"])
    if out["status"] == "requested":
        finish("adm_move_requested")
    finish("adm_move_unchanged" if out["status"] == "unchanged" else "adm_move_done")


@st.dialog(t("adm_move_title"))
def move_dialog(cfg: Config, user: dict, cadet) -> None:
    move_body(cfg, user, cadet)


# ---------------------------------------------------------------------------------------------------------- remove
def remove_body(cfg: Config, user: dict, cadet) -> None:
    cid = _cadet(cadet)["id"]
    by_instructor = user["role"] == "instructor"
    st.write(t("adm_remove_body_request" if by_instructor else "adm_remove_body"))
    if not _button_pair(t("btn_send_request" if by_instructor else "btn_adm_remove_ok"), f"del_ok_cadet_{cid}",
                        f"cremove_cancel_{cid}", icon=":material/person_remove:"):
        return
    try:
        out = people.delete_cadet(cfg.db_path, user["id"], cid)
    except BarorError as exc:
        _show(exc)
        return
    log_event("cadet_removed", user_id=user["id"])
    finish({"requested": "adm_remove_requested", "unchanged": "adm_remove_unchanged"}.get(out["status"], "adm_removed"))


@st.dialog(t("adm_remove_title"))
def remove_dialog(cfg: Config, user: dict, cadet) -> None:
    remove_body(cfg, user, cadet)


# ------------------------------------------------------------------------------------------------------------- add
def add_body(cfg: Config, user: dict, platoon_id: int) -> None:
    k = f"cadd_{platoon_id}"
    st.caption(t("adm_add_help"))
    email = st.text_input(t("adm_add_email"), key=f"{k}_email", max_chars=254, autocomplete="off")
    if not _button_pair(t("btn_adm_add_ok"), f"{k}_ok", f"{k}_cancel", icon=":material/person_add:"):
        return
    try:
        out = people.add_cadet(cfg.db_path, user["id"], email, platoon_id)
    except BarorError as exc:
        _show(exc)
        return
    log_event("cadet_added", user_id=user["id"])
    status = out["status"] if out["status"] in ("assigned", "unchanged", "moved", "requested") else "assigned"
    finish("adm_add_" + status)


@st.dialog(t("adm_add_title"))
def add_dialog(cfg: Config, user: dict, platoon_id: int) -> None:
    add_body(cfg, user, platoon_id)


# ---------------------------------------------------------------------------------------------------- join links
def join_url(cfg: Config, token: str) -> str:
    """The full link when the public address is configured, else just the ``?join=`` suffix."""
    base = getattr(cfg, "public_url", None)
    return f"{base}/?join={token}" if base else f"?join={token}"


def _held_key(platoon_id: int) -> str:
    return f"_join_{platoon_id}"                 # starts with '_': removed at sign-out


def make_link(cfg: Config, user: dict, platoon_id: int, days: int) -> None:
    """Create the link and keep it for this browser session; a refusal is shown, nothing is kept."""
    try:
        token = people.join_link(cfg.db_path, user["id"], platoon_id, secret=join_secret(cfg), days=days)
    except BarorError as exc:
        _show(exc)
        return
    until = dt.datetime.now(cfg.tz).date() + dt.timedelta(days=days)
    st.session_state[_held_key(platoon_id)] = {"url": join_url(cfg, token), "until": until.strftime("%d/%m/%Y")}
    log_event("join_link_created", user_id=user["id"])


def revoke_body(cfg: Config, user: dict, platoon_id: int) -> None:
    st.write(t("adm_join_revoke_body"))
    if not _button_pair(t("btn_adm_join_revoke_ok"), f"del_ok_join_{platoon_id}", f"join_revoke_cancel_{platoon_id}"):
        return
    try:
        people.rotate_join_links(cfg.db_path, user["id"], platoon_id)
    except BarorError as exc:
        _show(exc)
        return
    st.session_state.pop(_held_key(platoon_id), None)
    log_event("join_links_revoked", user_id=user["id"])
    finish("adm_join_revoked")


@st.dialog(t("adm_join_revoke_title"))
def revoke_dialog(cfg: Config, user: dict, platoon_id: int) -> None:
    revoke_body(cfg, user, platoon_id)


def join_link_box(cfg: Config, user: dict, platoon_id: int) -> None:
    """Make, show (copyable) and revoke the join link of one platoon. Feature off (no secret) -> one explaining line."""
    with st.container(border=True, key=f"join_box_{platoon_id}"):
        st.markdown(f"**{t('adm_join_title')}**")
        if not join_secret(cfg):
            st.caption(t("adm_join_off"))
            return
        st.caption(t("adm_join_intro"))
        days = st.segmented_control(t("adm_join_days"), list(JOIN_DAYS), default=14, required=True, width="stretch",
                                    key=f"join_days_{platoon_id}", format_func=lambda d: t("adm_join_days_n", n=d))
        if st.button(t("btn_adm_join_make"), key=f"join_make_{platoon_id}", width="stretch", icon=":material/link:"):
            make_link(cfg, user, platoon_id, days or 14)
        held = st.session_state.get(_held_key(platoon_id))
        if held:
            st.code(held["url"], language=None, wrap_lines=True)
            st.caption(iso(t("adm_join_until", date=held["until"])))
            if not getattr(cfg, "public_url", None):
                st.caption(t("adm_join_no_url"))
        if st.button(t("btn_adm_join_revoke"), key=f"join_revoke_{platoon_id}", width="stretch", type="tertiary",
                     icon=":material/link_off:"):
            revoke_dialog(cfg, user, platoon_id)


__all__ = ["edit_dialog", "move_dialog", "remove_dialog", "add_dialog", "join_link_box", "invalidate", "finish",
           "platoon_choices"]
