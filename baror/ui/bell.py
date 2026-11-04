"""The notification bell in the top bar (chief and admin).

One popover; each kind of waiting request that has a count above zero gets a line and a button "מעבר לבקשה". The button
leaves the wanted card in ``st.session_state["_req_focus"]`` (one of "approve", "delete", "changes") and navigates to the
account's own screen; the requests section there opens that card and clears the key.

* chief: accommodation requests to approve, accommodation deletion requests, cadet-change requests;
* admin: cadet-change requests only (storage returns 0 for the others).

The numbers come from ``notifications.counts`` (numbers only, not audited on success; the storage layer checks the role
on every call). Nothing here is cached. The popover body is drawn by Streamlit in a floating layer outside the fixed bar,
so the bar's own box does not clip it (the stylesheet limits its width to the viewport)."""
from __future__ import annotations

import streamlit as st

from baror.config import Config
from baror.errors import BarorError
from baror.messages_he import t
from baror.storage import notifications
from baror.ui.nav import go

REQ_FOCUS = "_req_focus"
# count key -> (focus value, message key)
CATEGORIES = (("accommodation_requests", "approve", "bell_approve"),
              ("deletion_requests", "delete", "bell_delete"),
              ("change_requests", "changes", "bell_changes"))
HOME = {"chief": "requests", "admin": "requests"}        # role -> the page that holds the requests


def _open(destination: str, focus: str) -> None:
    """Button callback: runs before the next script run."""
    st.session_state[REQ_FOCUS] = focus
    for stale in ("_chief_requests", "_chief_pending", "_changes_pending"):    # the lists are read again when drawn
        st.session_state.pop(stale, None)
    go(destination)


def read_counts(cfg: Config, user: dict) -> dict | None:
    try:
        return notifications.counts(cfg.db_path, user["id"])
    except BarorError:
        return None


def render(cfg: Config, user: dict) -> None:
    destination = HOME.get(user["role"])
    counts = read_counts(cfg, user)
    if destination is None or counts is None:
        return
    total = counts["total"]
    label = t("bell_label_n", n=total) if total else t("bell_label")
    icon = ":material/notifications_active:" if total else ":material/notifications:"
    with st.popover(label, icon=icon, key="bell", width="stretch", wrap=True):
        st.markdown(f"**{t('bell_title')}**")
        shown = [(focus, text, counts[key]) for key, focus, text in CATEGORIES if counts.get(key)]
        if not shown:
            st.caption(t("bell_none"))
        for focus, text, n in shown:
            st.write(t(text, n=n))
            st.button(t("btn_bell_go"), key=f"bell_go_{focus}", width="stretch", icon=":material/arrow_back:",
                      on_click=_open, args=(destination, focus))
