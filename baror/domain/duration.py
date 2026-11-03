"""Parsing of run/walk durations typed by a cadet. Standard library only; never imports streamlit.

Two entry points, both return INTEGER TENTHS OF A SECOND (the unit stored in ``results.run_tenths``) and raise
``ValidationError`` (an expected condition with a Hebrew message key), never a bare ``ValueError``:

* ``from_fields(minutes, seconds)``  -- the two numeric inputs of the form.
* ``from_text("15:40")``             -- a single ``mm:ss`` text field.

Valid: minutes 0..120, seconds 0..59, total at least one second. In text form the seconds must have TWO digits
("15:5" is ambiguous and is refused). The error detail never echoes the input.
"""
from __future__ import annotations

import re

from baror.errors import ValidationError

MAX_MINUTES = 120
_MAX_LEN = 7
_TEXT_RE = re.compile(r"^(\d{1,3}):(\d{2})$", re.ASCII)       # ASCII digits only
_ARABIC_INDIC = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}


def from_fields(minutes, seconds, *, field: str = "run_time") -> int:
    for name, v in (("minutes", minutes), ("seconds", seconds)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValidationError("INVALID_TIME_FORMAT", field=field, detail=f"{name} is not an integer")
    if seconds < 0 or seconds > 59:
        raise ValidationError("TIME_SECONDS_RANGE", field=field, detail="seconds out of range")
    if minutes < 0 or minutes > MAX_MINUTES:
        raise ValidationError("TIME_OUT_OF_RANGE", field=field, params={"lo": 0, "hi": MAX_MINUTES},
                              detail="minutes out of range")
    total = minutes * 60 + seconds
    if total < 1:
        raise ValidationError("TIME_ZERO", field=field, detail="zero duration")
    return total * 10


def from_text(text, *, field: str = "run_time") -> int:
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("INVALID_TIME_FORMAT", field=field, detail="empty or not text")
    raw = text.strip().translate(_ARABIC_INDIC)
    if len(raw) > _MAX_LEN:                    # cheap guard before the regex
        raise ValidationError("INVALID_TIME_FORMAT", field=field, detail="too long")
    m = _TEXT_RE.match(raw)
    if not m:
        raise ValidationError("INVALID_TIME_FORMAT", field=field, detail="does not match mm:ss")
    return from_fields(int(m.group(1)), int(m.group(2)), field=field)
