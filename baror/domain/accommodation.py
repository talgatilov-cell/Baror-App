"""Accommodations: a הקלה (relief) or פטור (exemption) recorded for a cadet. Standard library only; never imports streamlit.

A record is a kind, what it covers (scope), a start and an end date, an optional short plain-text note and the SOURCE
(who recorded it). Whether it is upcoming, active or expired is never stored: ``status`` computes it from today's date,
which the caller supplies (Israel time), so the answer cannot go stale in the database.

APPROVAL: a record entered by a cadet is 'pending' until the chief decides ('approved' or 'rejected'); records of staff
start 'approved'. Only an 'approved' record is IN EFFECT: ``status`` reports 'pending' / 'rejected' for the others (never
'active', whatever the dates) and ``covers`` is False for them. A record dict without an ``approval`` key (only ever a
hand-made one: every dict from ``baror.storage.accommodations`` carries it) is read as approved.

``validate`` is the one place that decides what may be stored; ``baror.storage.accommodations`` calls it and the
database CHECK constraints are the backstop. Notes may hold digits (unlike names) but no markup characters.
"""
from __future__ import annotations

import calendar
import datetime as dt
import unicodedata

from baror.errors import ValidationError

KINDS = ("relief", "exemption")                      # הקלה, פטור
SCOPES = ("run", "pushups", "both")
SOURCES = ("cadet", "instructor", "chief", "admin")
APPROVALS = ("pending", "approved", "rejected")      # a cadet's own record starts 'pending'; only 'approved' is in effect
NOTE_MAX = 100
MAX_PER_CADET = 30                                   # a cap per account: one cadet cannot fill the staff screens or the PDF
MAX_YEARS = 3                                        # the end date is at most this long after the start date
SANITY_YEARS = 3                                     # a start date further than this from today is a typo, not a plan
NOTE_PUNCTUATION = " .,-()'’׳״"       # space . , - ( ) ' ’ geresh gershayim


def _iso_date(value, field: str) -> dt.date:
    """Strict YYYY-MM-DD text (what the database stores) or a plain date."""
    try:
        if isinstance(value, dt.datetime):
            raise ValueError
        if isinstance(value, dt.date):
            return value
        d = dt.date.fromisoformat(value)
        if d.isoformat() != value:
            raise ValueError
        return d
    except (TypeError, ValueError):
        raise ValidationError("ACCOMMODATION_DATES", field=field, detail=f"{field} is not a calendar date YYYY-MM-DD") from None


def _shift_years(d: dt.date, years: int) -> dt.date:
    year = d.year + years
    return dt.date(year, d.month, min(d.day, calendar.monthrange(year, d.month)[1]))


def _clean_note(note) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str):
        raise ValidationError("ACCOMMODATION_NOTE", field="note", detail="note is not text")
    text = note.strip()
    if not text:
        return None
    if len(text) > NOTE_MAX:
        raise ValidationError("ACCOMMODATION_NOTE", field="note", detail="note too long")
    for ch in text:
        if ch.isalpha() or ch.isdigit() or ch in NOTE_PUNCTUATION or unicodedata.category(ch) == "Mn":
            continue                                  # letters of any script, digits, basic punctuation, niqqud marks
        raise ValidationError("ACCOMMODATION_NOTE", field="note", detail="note holds a character that is not allowed")
    return text


def validate(kind, scope, start_date, end_date, note, today: dt.date) -> dict:
    """-> {'kind','scope','start_date','end_date','note'} normalised (ISO strings, note trimmed or None).
    Raises ValidationError: INVALID_ACCOMMODATION (kind or scope), ACCOMMODATION_DATES, ACCOMMODATION_NOTE."""
    if kind not in KINDS:
        raise ValidationError("INVALID_ACCOMMODATION", field="kind", detail="unknown kind")
    if scope not in SCOPES:
        raise ValidationError("INVALID_ACCOMMODATION", field="scope", detail="unknown scope")
    start, end = _iso_date(start_date, "start_date"), _iso_date(end_date, "end_date")
    if end < start:
        raise ValidationError("ACCOMMODATION_DATES", field="end_date", detail="end before start")
    if end > _shift_years(start, MAX_YEARS):
        raise ValidationError("ACCOMMODATION_DATES", field="end_date", detail=f"longer than {MAX_YEARS} years")
    if not _shift_years(today, -SANITY_YEARS) <= start <= _shift_years(today, SANITY_YEARS):
        raise ValidationError("ACCOMMODATION_DATES", field="start_date", detail="start date implausibly far from today")
    return {"kind": kind, "scope": scope, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "note": _clean_note(note)}


def in_effect(record: dict) -> bool:
    """Only an approved record counts; pending and rejected ones are shown but never applied."""
    return record.get("approval", "approved") == "approved"


def status(record: dict, today: dt.date) -> str:
    """'pending' / 'rejected' (not approved: the dates do not matter), else 'upcoming' (starts after today), 'expired'
    (ended before today) or 'active' (today is inside, ends included)."""
    approval = record.get("approval", "approved")
    if approval != "approved":
        return approval if approval in APPROVALS else "pending"       # an unknown value is never in effect
    if dt.date.fromisoformat(record["start_date"]) > today:
        return "upcoming"
    if dt.date.fromisoformat(record["end_date"]) < today:
        return "expired"
    return "active"


def covers(record: dict, test: str) -> bool:
    """Does the record apply to 'run' (also the walk track) or 'pushups'? Never true for a record not in effect."""
    if not in_effect(record):
        return False
    scope = record["scope"]
    return scope == "both" or scope == ("pushups" if test == "pushups" else "run")
