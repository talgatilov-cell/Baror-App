"""The admin screen: permissions and cadet management ONLY.

There is deliberately no chief dashboard, no platoon (cohort) screen and no accommodation tool here: an admin manages who
holds which role and which cadet sits in which platoon, and nothing else. Order: accounts and roles, cadets by platoon
(with the join link of the chosen platoon), the cadet-change requests, a new platoon.

Authorisation is decided by ``baror.storage.people`` / ``baror.storage.admin`` on every call (the actor is re-read there);
the shell only offers this page to an admin. Reads are lazy and kept under ``_`` keys: the cadets of a platoon are read
once when the platoon is chosen (one audited search per read), not once per rerun.

Safety: names and e-mails are typed by people: ``st.text`` only, never Markdown or HTML."""
from __future__ import annotations

import streamlit as st

from baror.config import Config
from baror.errors import BarorError
from baror.logging_setup import log_event
from baror.messages_he import render_exception, t
from baror.storage import admin, people
from baror.ui import accom, accounts_panel, cadet_admin

try:                                                     # the requests screens are built by another workstream
    from baror.ui import requests_section
except ImportError:                                      # pragma: no cover
    requests_section = None

_CADETS = "_adm_cadets"          # {"platoon": id | 0, "rows": [...], "cut": bool}   (read once per chosen platoon)
_REQ_OPEN = "_adm_req_open"      # whether the change-requests card is open
_REQ_FOCUS = "_req_focus"        # set by the top-bar bell: "changes" opens the card
_PAGE = 100                      # people.search_accounts never returns more than this per call
_SCAN_PAGES = 10                 # the unassigned cadets are found by scanning at most this many pages


def _read_cadets(cfg: Config, user: dict, platoon_id: int) -> dict | None:
    """Active cadets of one platoon (or, for UNASSIGNED, the cadets without a platoon), newest account first."""
    try:
        if platoon_id != cadet_admin.UNASSIGNED:
            res = people.search_accounts(cfg.db_path, user["id"], role="cadet", platoon_id=platoon_id, limit=_PAGE)
            rows, cut = res["rows"], res["total"] > len(res["rows"])
        else:                                            # the search has no 'without platoon' filter: scan the cadets
            rows, offset, cut = [], 0, False
            for page in range(_SCAN_PAGES):
                res = people.search_accounts(cfg.db_path, user["id"], role="cadet", limit=_PAGE, offset=offset)
                rows += [r for r in res["rows"] if r["platoon_id"] is None]
                offset += _PAGE
                if offset >= res["total"]:
                    break
            else:
                cut = True
    except BarorError as exc:
        st.error(render_exception(exc), icon=":material/error:")
        return None
    return {"platoon": platoon_id, "rows": [r for r in rows if r["active"]], "cut": cut}


def _cadet_card(cfg: Config, user: dict, r: dict) -> None:
    cadet = {k: r[k] for k in ("id", "name", "gender", "is_km", "platoon_id")}
    with st.container(border=True, key=f"adm_cadet_{r['id']}"):
        st.text(r["name"])                               # first line = the heading (stylesheet)
        st.text(r["email"])                              # typed by a person: never Markdown
        cols = st.columns(3, gap="xsmall", wrap=False)
        for col, (label, fn, icon) in zip(cols, (("btn_adm_cadet_edit", cadet_admin.edit_dialog, ":material/edit:"),
                                                 ("btn_adm_cadet_move", cadet_admin.move_dialog, ":material/swap_horiz:"),
                                                 ("btn_adm_cadet_remove", cadet_admin.remove_dialog, ":material/person_remove:"))):
            with col:
                if st.button(t(label), key=f"{label}_{r['id']}", width="stretch", icon=icon):
                    fn(cfg, user, cadet)


def _cadets_section(cfg: Config, user: dict) -> None:
    with st.container(key="adm_cadets"):
        st.subheader(t("adm_cadets_title"))
        st.caption(t("adm_cadets_intro"))
        try:
            platoons = people.list_platoons(cfg.db_path, user["id"])
        except BarorError as exc:
            st.error(render_exception(exc), icon=":material/error:")
            return
        names = {p["id"]: p["name"] for p in platoons}
        names[cadet_admin.UNASSIGNED] = t("unassigned")
        pid = st.selectbox(t("pick_platoon"), list(names), index=None, key="adm_platoon", format_func=names.get,
                           placeholder=t("choose_placeholder"), filter_mode=None)
        if pid is None:
            st.session_state.pop(_CADETS, None)
            return
        held = st.session_state.get(_CADETS)
        if held is None or held["platoon"] != pid:
            held = _read_cadets(cfg, user, pid)
            if held is None:
                return
            st.session_state[_CADETS] = held
        if pid != cadet_admin.UNASSIGNED:
            cadet_admin.join_link_box(cfg, user, pid)
            if st.button(t("btn_adm_cadet_add"), key=f"adm_add_{pid}", width="stretch", icon=":material/person_add:"):
                cadet_admin.add_dialog(cfg, user, pid)
        if not held["rows"]:
            st.write(t("adm_cadets_empty"))
            return
        st.caption(_count_text(len(held["rows"])))
        if held["cut"]:
            st.caption(t("adm_cadets_truncated"))
        with st.container(key="adm_card_list"):
            for r in held["rows"]:
                _cadet_card(cfg, user, r)


def _count_text(n: int) -> str:
    return t("cadets_count_one") if n == 1 else t("cadets_count_two") if n == 2 else t("cadets_count", n=n)


def _requests_section(cfg: Config, user: dict) -> None:
    """One collapsed card with the cadet-change requests (the admin has no accommodation requests). Read only while open;
    the bell opens it."""
    if requests_section is None or not hasattr(requests_section, "render_changes"):
        return
    st.subheader(t("adm_requests_title"))
    st.session_state.pop(_REQ_FOCUS, None)
    # this page holds only this one card: it opens by itself on the first visit and stays as the admin leaves it
    is_open = bool(st.session_state.setdefault(_REQ_OPEN, True))
    with st.container(border=True, key="adm_reqcard"):
        if st.button(t("req_card_changes"), key="adm_reqcard_btn", width="stretch",
                     icon=":material/expand_less:" if is_open else ":material/expand_more:"):
            st.session_state.pop("_changes_pending", None)                     # opening reads fresh, once
            st.session_state[_REQ_OPEN] = not is_open
            st.rerun()
        if is_open:
            requests_section.render_changes(cfg, user)


def _create_platoon(cfg: Config, user: dict) -> None:
    with st.container(key="adm_new_platoon"):
        st.subheader(t("adm_platoon_new_title"))
        with st.form("adm_platoon_form", border=False, clear_on_submit=True):
            name = st.text_input(t("adm_platoon_name"), key="adm_platoon_name", max_chars=60)
            go = st.form_submit_button(t("btn_adm_platoon_create"), width="stretch", icon=":material/add:")
        if not go:
            return
        try:
            admin.create_platoon(cfg.db_path, user["id"], name.strip())
        except BarorError as exc:
            st.error(render_exception(exc), icon=":material/error:")
            return
        log_event("platoon_created", user_id=user["id"])
        st.session_state["_notice"] = t("adm_platoon_created")
        cadet_admin.invalidate()
        st.rerun()


def admin_page(cfg: Config, user: dict) -> None:
    with st.container(key="admin_page"):
        st.title(t("admin_title"))
        with st.container(key="admin_banner"):
            st.write(t("admin_banner"))
        accom.take_notice()
        accounts_panel.render(cfg, user)
        _cadets_section(cfg, user)
        with st.expander(t("adm_new_platoon_expander")):
            _create_platoon(cfg, user)


def requests_page(cfg: Config, user: dict) -> None:
    """The admin's second page: the cadet-change requests (the bell leads here)."""
    with st.container(key="admin_page"):
        accom.take_notice()
        _requests_section(cfg, user)
