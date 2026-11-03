"""The look of the original app, kept as it was until the design phase (T5) replaces it: a camouflage background with a
dark box on the sign-in page and a light box inside. Static CSS only: no user input ever reaches this module.

Fixes over the original: assets are resolved from the repository root (not the working directory), a missing
background image leaves a flat colour instead of ``base64,None``, and the image is encoded once per process."""
from __future__ import annotations

import base64
import functools
import logging
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("baror.ui")


@functools.lru_cache(maxsize=1)
def _background_css() -> str:
    """Static asset, identical for every session (no user data), encoded once."""
    path = ROOT / "image_bea22f.jpg"
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        log.warning("background image missing: %s", path.name)
        return "background-color: #4a5c40;"
    return (f'background-image: url("data:image/jpg;base64,{data}"); background-size: cover; '
            "background-position: center; background-attachment: fixed;")


# The `!important` flags are the original app's, kept on purpose: Streamlit's own stylesheet is more specific and the
# look cannot be verified here without a browser. The design phase (T5) replaces all of this with keyed containers.
#
# One static stylesheet per run. The two looks are scoped by the presence of the sign-in container (key="signin" gives
# it the class .st-key-signin): while it is on the page the dark sign-in look applies, the moment it is gone (same run
# as a successful sign-in) the light look applies. Removing a <style> element is NOT relied on: Streamlit keeps it.
_IN = ".stApp:has(.st-key-signin)"
_OUT = ".stApp:not(:has(.st-key-signin))"
_CSS = """
.stApp {{ direction: ltr !important; {bg} }}
.stButton>button {{ background-color: #4a5c40; color: white !important; border-radius: 8px; width: 100%; border: none; font-weight: bold; }}
.stButton>button:hover {{ background-color: #3b4a33; }}

.st-key-hp_login, .st-key-hp_register {{ position: absolute !important; inset-inline-start: -10000px !important;
    width: 1px !important; height: 1px !important; overflow: hidden !important; }}

{i} .main .block-container, {i} [data-testid="stMainBlockContainer"] {{
    background-color: rgba(0, 0, 0, 0.75) !important; border-radius: 15px !important; padding: 2rem !important;
    margin-top: 1rem; box-shadow: 0 4px 15px rgba(0,0,0,0.5) !important; direction: rtl !important; }}
{i} p, {i} h1, {i} h2, {i} h3, {i} h4, {i} h5, {i} h6, {i} span, {i} label, {i} li, {i} ul, {i} ol, {i} .stMarkdown, {i} .stText {{
    direction: rtl !important; text-align: right !important; font-family: 'Segoe UI', sans-serif; color: white !important; }}
{i} input {{ direction: rtl !important; text-align: right !important; color: black !important;
    background-color: rgba(255,255,255,0.9) !important; }}

{o} .main .block-container, {o} [data-testid="stMainBlockContainer"] {{
    background-color: rgba(255, 255, 255, 0.90) !important; border-radius: 15px !important; padding: 2rem !important;
    margin-top: 1rem; box-shadow: 0 4px 20px rgba(0,0,0,0.3) !important; direction: rtl !important; }}
{o} p, {o} h1, {o} h2, {o} h3, {o} h4, {o} h5, {o} h6, {o} span, {o} label, {o} li, {o} ul, {o} ol, {o} .stMarkdown,
{o} .stText, {o} input, {o} .stAlert div, {o} .stRadio div {{
    direction: rtl !important; text-align: right !important; font-family: 'Segoe UI', sans-serif; }}
{o} [data-testid="stSidebar"] {{ background-color: rgba(240, 242, 246, 1) !important; direction: ltr !important; }}
{o} [data-testid="stSidebarUserContent"] {{ direction: rtl !important; }}
@media (max-width: 768px) {{
    {o} .main .block-container {{ padding: 1rem !important; width: 100% !important; }}
    {o} [data-testid="stSidebar"] {{ min-width: 280px !important; }}
}}
"""


def apply() -> None:
    st.html(f"<style>{_CSS.format(bg=_background_css(), i=_IN, o=_OUT)}</style>")
