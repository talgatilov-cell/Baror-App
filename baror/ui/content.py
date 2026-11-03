"""Tips and videos as cards (T7). CONTRACT used by baror/ui/more.py:
    render_tips(rows, user)   rows = repo.list_results(...) of the signed-in user (newest first); user = session user dict
    render_videos()

* Tips: one card per tip (Material icon, one-line headline, bullets), run and push-up sections, then a table that sums
  the tips up per week. Only the ORDER of the two sections is personalised: push-ups first when the latest push-up
  result sits lower on its scale than the latest run result. No alert components (those are for status).
* Videos: a card per video; the player (and the request to the video host) only starts after a tap. A plain link opens
  the video in a new window, which also works when the embed is blocked.
* All copy lives in baror.messages_he. Latin words in the copy ("Zone 2") are wrapped in Unicode isolates so they do not
  scramble the neighbouring Hebrew (the plain-text twin of <bdi>)."""
from __future__ import annotations

import re

import streamlit as st

from baror.domain.entry import grade_value
from baror.messages_he import TIPS_PUSHUPS, TIPS_RUN, TIPS_WEEK, VIDEOS, t
from baror.ui.components import FSI, PDI

_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9 ]*[A-Za-z0-9]")
_RANK = {"fail": 0, "pass": 1, "excellent": 2}


def isolate_latin(text: str) -> str:
    """Wrap every run of Latin words in FSI ... PDI (bidi isolates)."""
    return _LATIN.sub(lambda m: f"{FSI}{m.group(0)}{PDI}", text)


def _latest(rows: list[dict], key: str):
    return next((r for r in rows if r.get(key) is not None), None)


def _rank(test: str, gender: str, row: dict | None, key: str) -> int | None:
    """0 = below the pass level, 1 = passed, 2 = top of the scale (excellent; on the walk track the pass level is the
    top). None when there is no result or it cannot be graded."""
    if row is None:
        return None
    g = grade_value(test, gender, row["test_date"], row[key])
    if g.level is None:
        return None
    return 2 if g.level != "fail" and g.next_level is None else _RANK[g.level]


def pushups_first(rows: list[dict], user: dict) -> bool:
    """True when the latest push-up result is lower on its scale than the latest run/walk result."""
    is_km = bool(user["is_km"])
    push = _rank("pushups", user["gender"], _latest(rows, "pushups"), "pushups")
    aero = _rank("walk" if is_km else "run", user["gender"], _latest(rows, "run_tenths"), "run_tenths")
    return push is not None and aero is not None and push < aero


def _heading(icon: str, head: str) -> str:
    return f"**:material/{icon}: {isolate_latin(head)}**"        # bold, not a #### heading: the section title stays the larger


def _tip_card(card: dict) -> None:
    with st.container(border=True):
        st.markdown(_heading(card["icon"], card["head"]))
        st.markdown("\n".join(f"- {isolate_latin(b)}" for b in card["bullets"]))


def _section(section: dict) -> None:
    st.header(section["title"])
    st.caption(section["intro"])
    for card in section["cards"]:
        _tip_card(card)


def _week_card() -> None:
    table = TIPS_WEEK["table"]
    with st.container(border=True, key="week_card"):
        st.markdown(_heading(TIPS_WEEK["icon"], TIPS_WEEK["head"]))
        st.table([dict(zip(table["columns"], row)) for row in table["rows"]])
        st.caption(TIPS_WEEK["note"])


def render_tips(rows: list[dict], user: dict) -> None:
    first = pushups_first(rows, user)
    sections = (TIPS_PUSHUPS, TIPS_RUN) if first else (TIPS_RUN, TIPS_PUSHUPS)
    if _latest(rows, "pushups") is not None and _latest(rows, "run_tenths") is not None:
        st.caption(t("tips_order_note"))
    for section in sections:
        _section(section)
    _week_card()


def _open(i: int) -> None:
    st.session_state[f"_video_open_{i}"] = True      # plain session key (not a widget key): survives tab switches


def render_videos() -> None:
    st.header(VIDEOS["title"])
    st.caption(VIDEOS["intro"])
    st.caption(isolate_latin(VIDEOS["privacy"]))
    for i, card in enumerate(VIDEOS["cards"]):
        with st.container(border=True, key=f"video_card_{i}"):
            st.markdown(_heading(card["icon"], card["head"]))
            if st.session_state.get(f"_video_open_{i}"):
                st.video(card["url"])
            else:
                st.button(t("btn_play_video"), key=f"video_play_{i}", icon=":material/play_arrow:", width="stretch",
                          on_click=_open, args=(i,))
            st.link_button(t("btn_open_video"), card["url"], icon=":material/open_in_new:", width="stretch",
                           type="tertiary")
