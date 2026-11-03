"""Static pages: running tips, push-up tips, videos. Content lives in baror.messages_he."""
from __future__ import annotations

import streamlit as st

from baror.messages_he import TIPS_PUSHUPS, TIPS_RUN, VIDEOS


def tips_page(content: dict, style: str) -> None:
    st.title(content["title"])
    st.write(content["intro"])
    show = st.info if style == "info" else st.success
    for heading, body in content["items"]:
        st.subheader(heading)
        show(body)


def run_tips() -> None:
    tips_page(TIPS_RUN, "info")


def pushup_tips() -> None:
    tips_page(TIPS_PUSHUPS, "success")


def videos() -> None:
    st.title(VIDEOS["title"])
    st.write(VIDEOS["intro"])
    for i, (heading, url) in enumerate(VIDEOS["items"]):
        if i:
            st.divider()
        st.subheader(heading)
        st.video(url)
