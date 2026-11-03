"""The signed-in frame: the page for the current destination, and the bottom navigation bar.

``st.navigation(position="top")`` becomes a sidebar on phones (checked on Streamlit 1.66.1), so the bar is a segmented
control in a fixed, keyed container (``.st-key-bottom_nav``). The destination lives in ``st.session_state["nav"]``; it
is a widget key, so it is only ever used to choose among pages the account is offered (a forged value falls back to the
first page). Authorisation never reads it: the storage layer checks the role again on every call.

Every page of every account starts with a slim top bar: the app name on the start side and the sign-out button ("יציאה")
on the end side, so leaving is never more than one tap away. Sign-out clears the cookie in the SAME run and does not
rerun (a rerun would drop the script that clears it).

Destinations: cadet  = summary, add result, history, more.
              staff  = their screen + more (only with BAROR_COHORT_VIEWS=1 and the role; otherwise they get the cadet bar).
"""
from __future__ import annotations

import html

import streamlit as st

from baror.config import Config
from baror.logging_setup import log_event
from baror.messages_he import t
from baror.ui import more, personal, session, staff
from baror.ui.nav import NAV_KEY, go  # noqa: F401  (go is re-exported)

ICONS = {"summary": ":material/home:", "add": ":material/add_circle:", "history": ":material/history:",
         "more": ":material/menu:", "platoon": ":material/groups:", "chief": ":material/monitoring:"}


def pages_for(cfg: Config, user: dict) -> dict:
    """Destination key -> callable, in bar order. Staff screens exist only behind the switch AND with the role."""
    if cfg.cohort_views and user["role"] in ("instructor", "admin"):
        return {"platoon": lambda: staff.platoon_page(cfg, user), "more": lambda: more.more_page(cfg, user)}
    if cfg.cohort_views and user["role"] == "chief":
        return {"chief": lambda: staff.chief_page(cfg, user), "more": lambda: more.more_page(cfg, user)}
    return {"summary": lambda: personal.summary_page(cfg, user), "add": lambda: personal.add_page(cfg, user),
            "history": lambda: personal.history_page(cfg, user), "more": lambda: more.more_page(cfg, user)}


def sign_out(cfg: Config, user: dict, message_key: str = "logged_out") -> None:
    """Revoke this session, clear the cookie in this run, say so, and stop (no rerun)."""
    log_event("logout", user_id=user["id"])
    session.end(cfg.db_path)
    st.html("<style>.st-key-top_bar{display:none}</style>")        # static: the signed-out view has no top bar
    session.sign_out_view(message_key)


def _top_bar(cfg: Config, user: dict) -> None:
    with st.container(key="top_bar"):
        brand, leave = st.columns([5, 3], vertical_alignment="center", wrap=False, gap="small")
        with brand:
            st.html('<div class="brandbar"><img src="app/static/logo.png" alt="" width="40" height="40">'
                    f'<span>{html.escape(t("app_title"))}</span></div>')
        with leave:
            if st.button(t("btn_logout"), icon=":material/logout:", width="stretch", key="logout_top"):
                sign_out(cfg, user)


def render(cfg: Config, user: dict) -> None:
    pages = pages_for(cfg, user)
    _top_bar(cfg, user)
    if st.session_state.get(NAV_KEY) not in pages:
        st.session_state[NAV_KEY] = next(iter(pages))
    pages[st.session_state[NAV_KEY]]()
    with st.container(key="bottom_nav"):
        st.segmented_control(t("nav_label"), list(pages), key=NAV_KEY, required=True, label_visibility="collapsed",
                             width="stretch", format_func=lambda k: f"{ICONS[k]} {t('nav_' + k)}")
