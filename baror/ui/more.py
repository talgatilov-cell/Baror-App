"""The 'more' destination: tips, videos and the profile, one at a time (a segmented control, never a long page)."""
from __future__ import annotations

import streamlit as st

from baror.config import Config
from baror.messages_he import t
from baror.storage import repo
from baror.ui import accom, content, profile

_TABS = ("tips", "videos", "accommodations", "profile")      # staff accounts have no personal accommodations tab


def more_page(cfg: Config, user: dict) -> None:
    st.title(t("more_title"))
    tabs = _TABS if user["role"] == "cadet" else tuple(x for x in _TABS if x != "accommodations")
    if st.session_state.get("more_tab") not in tabs:
        st.session_state["more_tab"] = "tips"
    tab = st.segmented_control(t("more_title"), tabs, key="more_tab", required=True,
                               label_visibility="collapsed", width="stretch", format_func=lambda k: t("more_tab_" + k))
    if tab == "tips":
        content.render_tips(repo.list_results(cfg.db_path, user["id"]), user)
    elif tab == "videos":
        content.render_videos()
    elif tab == "accommodations":
        accom.cadet_tab(cfg, user)
    else:
        profile.render(cfg, user)
