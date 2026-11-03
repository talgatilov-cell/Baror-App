"""The signed-in frame: sidebar navigation (pages depend on the role), account line, sign-out."""
from __future__ import annotations

import streamlit as st

from baror.config import Config
from baror.messages_he import t
from baror.ui import content, personal, session, staff

ROOT_LOGO = "logo.png"


def pages_for(cfg: Config, user: dict) -> dict:
    """Page label -> callable. Staff pages exist only behind the switch AND with the role; the storage layer enforces
    the role again on every call, this only decides what is offered."""
    pages = {
        t("nav_personal"): lambda: personal.personal_page(cfg, user),
        t("nav_tips_run"): content.run_tips,
        t("nav_tips_pushups"): content.pushup_tips,
        t("nav_videos"): content.videos,
    }
    if cfg.cohort_views:
        if user["role"] in ("instructor", "admin"):
            pages[t("nav_platoon")] = lambda: staff.platoon_page(cfg, user)
        if user["role"] == "chief":
            pages[t("nav_chief")] = lambda: staff.chief_page(cfg, user)
    return pages


def render(cfg: Config, user: dict) -> None:
    from baror.ui.theme import ROOT
    pages = pages_for(cfg, user)
    with st.sidebar:
        logo = ROOT / ROOT_LOGO
        if logo.exists():
            st.image(str(logo), width="stretch")
        st.header(t("nav_title"))
        page = st.radio(t("nav_label"), list(pages), label_visibility="collapsed", key="nav_page")
        st.divider()
        st.text(f"{t('account_label')}: {user['name']}")            # st.text: no Markdown interpretation of the name
        if st.button(t("btn_logout"), key="logout"):
            session.end(cfg.db_path)
            session.clear_cookie()
            st.text(t("logged_out"))
            st.stop()
    pages.get(page, next(iter(pages.values())))()   # an unknown (forged) value falls back to the first page
