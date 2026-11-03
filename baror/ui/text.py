"""Rendering user-controlled text safely (BR-30). Streamlit Markdown also interprets :emoji: codes, $math$ and
:color[text] directives, so EVERY ASCII punctuation character is backslash-escaped."""
from __future__ import annotations

import re

_PUNCT = re.compile(r"([!-/:-@\[-`{-~])")


def esc(text) -> str:
    return _PUNCT.sub(r"\\\1", str(text).replace("\r", " ").replace("\n", " "))
