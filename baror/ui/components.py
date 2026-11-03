"""Small shared building blocks of the design system. Everything here takes TRUSTED values only (copy from
``baror.messages_he``, numbers and times produced by the domain code); user-typed text (names) is shown with ``st.text``
or ``baror.ui.text.esc`` and never passes through these helpers' HTML.

Components (see static/app.css for the look):
  status_badge(level)   result card / list status: icon + word, colour never the only cue
  target_chips(...)     "what counts" chips under an input
  field_error(msg)      error beside the field (role=alert), icon drawn by CSS
  bdi(text)             <bdi> wrapper for digits / Latin next to Hebrew in HTML contexts
  iso(text)             the same isolation for plain-text contexts (st.text, st.caption, metric): Unicode FSI ... PDI
"""
from __future__ import annotations

import html

import streamlit as st

from baror.messages_he import t

FSI, PDI = "\u2068", "\u2069"            # first-strong isolate / pop directional isolate: the plain-text twin of <bdi>

# level -> (colour badge name, Material icon, message key). Excellent and pass share a colour but not icon and word.
_BADGE = {"excellent": ("green", "military_tech", "status_excellent"), "pass": ("green", "check_circle", "status_pass"),
          "fail": ("orange", "flag", "status_fail")}


def bdi(text) -> str:
    return f"<bdi>{html.escape(str(text))}</bdi>"


def html_msg(key: str, **params) -> str:
    """A catalogue message as safe HTML: the sentence is escaped, each (trusted) parameter is wrapped in <bdi>."""
    marks = {k: f"\x00{k}\x00" for k in params}
    text = html.escape(t(key, **marks))
    for k, v in params.items():
        text = text.replace(f"\x00{k}\x00", bdi(v))
    return text


def iso(text) -> str:
    return f"{FSI}{text}{PDI}"


def status_badge(level: str | None) -> str:
    """Markdown for a native badge (``st.markdown``). Unknown level -> grey 'not graded'."""
    colour, icon, key = _BADGE.get(level, ("gray", "help", "status_ungraded"))
    return f":{colour}-badge[:material/{icon}: {t(key)}]"


def target_chips(items: list[tuple[str, str, bool]]) -> None:
    """items = [(label, value, is_excellent)] -> chips. Label and value come from messages / thresholds only."""
    spans = "".join(f'<span class="chip{" chip-exc" if exc else ""}">{html.escape(label)}: {bdi(value)}</span>'
                    for label, value, exc in items)
    st.html(f'<div class="chips">{spans}</div>')


def field_error(msg: str | None) -> None:
    if msg:
        st.html(f'<p class="field-error" role="alert">{html.escape(msg)}</p>')
