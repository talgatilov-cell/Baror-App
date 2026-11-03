"""Navigation state shared by the shell and the screens (kept apart so the screens need not import the shell)."""
from __future__ import annotations

import streamlit as st

NAV_KEY = "nav"          # the bottom bar's widget key: a destination chosen among the pages the account is offered


def go(destination: str) -> None:
    """Button callback: runs before the next script run, so the bar's own widget state may still be set here."""
    st.session_state[NAV_KEY] = destination
