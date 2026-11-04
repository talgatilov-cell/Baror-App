"""The look: one static stylesheet (``static/app.css``) plus the page language and direction. Static input only: no user
input ever reaches this module. Design tokens live in ``.streamlit/config.toml`` and at the top of ``app.css``.

* ``<html lang="he" dir="rtl">`` is set by a fixed script (``st.html(..., unsafe_allow_javascript=True)``); the CSS
  ``html{direction:rtl}`` is the safety net if scripts are blocked. The script sits in a keyed container that the
  stylesheet hides, because a script-only ``st.html`` otherwise leaves a flex gap above the first element.
* The stylesheet is read once per process (it is the same for everybody) and injected on every run: Streamlit drops
  elements that are not re-rendered, so skipping a run would lose the styles.
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
CSS_PATH = ROOT / "static" / "app.css"
ADMIN_CSS_PATH = ROOT / "static" / "admin.css"      # admin screen, accounts panel, bell: loaded after app.css
log = logging.getLogger("baror.ui")

_BOOT = "<script>const d=document.documentElement; d.lang='he'; d.dir='rtl';</script>"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        log.warning("stylesheet missing: %s", path.name)           # the app stays usable with Streamlit's own theme
        return ""


@functools.lru_cache(maxsize=1)
def css_text() -> str:
    """app.css followed by admin.css (later rules win by source order, as before)."""
    return "\n".join(part for part in (_read(CSS_PATH), _read(ADMIN_CSS_PATH)) if part)


def apply() -> None:
    with st.container(key="rtl_boot"):
        st.html(_BOOT, unsafe_allow_javascript=True)
    css = css_text()
    if css:
        st.html(f"<style>{css}</style>")
