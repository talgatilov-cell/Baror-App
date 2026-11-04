"""When the aggregated reports are due, and which dates each one covers. Pure functions: standard library only, no I/O,
never imports streamlit. Israel's week is Sunday to Thursday, the weekend is Friday and Saturday (owner, 2026-11-03).

* WEEKLY report: made every THURSDAY. Its period runs from the Friday before up to and including that Thursday, so
  consecutive weekly periods touch with no gap and no overlap.
* MONTHLY report: made on the LAST DAY of the month when that day is Sunday to Thursday; when the last day is a Friday or
  a Saturday it is made on the THURSDAY before it. Its period is the calendar month from the 1st up to the day it is made
  ("data up to <date>"). Results dated on the weekend days left over (the Friday and Saturday at the end of that month) are
  NOT in the monthly report of that month and NOT in the next one (it starts on the 1st); they are in the weekly report of
  the following Thursday. This is the owner's rule as given; see docs/runbook.md section 9.
* When a Thursday is also the last day of the month both reports are due that day.

``reports_due(today)`` answers for one calendar date only; a missed day is caught up by asking for each missed date in turn
(``scripts/generate_reports.py --since``). Generation stores at most one report per type and period start, so asking twice
is harmless.

ON-DEMAND reports (never stored, see ``baror.storage.reports.adhoc_report``):
* TRAILING: the last 30 days, ``today - 30 days`` up to and including ``today`` (31 calendar days).
* RANGE: a start and an end chosen by the chief. ``validate_range`` refuses an end before the start (REPORT_RANGE_ORDER),
  an end after today (REPORT_RANGE_FUTURE) and a range longer than 5 calendar months (REPORT_RANGE_TOO_LONG): the last
  allowed end is ``start + 5 calendar months - 1 day`` (the day-of-month is clamped to the length of the target month, so
  31 Jan + 5 months is 30 Jun and the last end is 29 Jun).
* The PREVIOUS period of both is the same number of days immediately before the start.
"""
from __future__ import annotations

import calendar
import datetime as dt

from baror.errors import ValidationError

WEEKLY, MONTHLY = "weekly", "monthly"
KINDS = (WEEKLY, MONTHLY)                        # the STORED kinds (reports.period_type CHECK allows only these)
TRAILING, RANGE = "trailing", "range"
ADHOC_KINDS = (TRAILING, RANGE)                  # generated on demand, never stored
ALL_KINDS = KINDS + ADHOC_KINDS
TRAILING_DAYS = 30
MAX_RANGE_MONTHS = 5
THURSDAY, FRIDAY, SATURDAY = 3, 4, 5             # datetime.date.weekday(): Monday is 0

Period = tuple[dt.date, dt.date]                 # first and last day, both included


def _check_date(value) -> dt.date:
    if isinstance(value, dt.datetime) or not isinstance(value, dt.date):
        raise ValidationError("INVALID_DATE", detail="a plain date is required")
    return value


def add_months(day: dt.date, months: int) -> dt.date:
    """``day`` moved forward by whole calendar months; the day-of-month is clamped to the target month's length."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def trailing_period(today: dt.date) -> Period:
    """The last 30 days: ``today - 30 days`` .. ``today``."""
    today = _check_date(today)
    return today - dt.timedelta(days=TRAILING_DAYS), today


def last_allowed_end(start: dt.date) -> dt.date:
    """The latest end a range starting on ``start`` may have (5 calendar months, less one day)."""
    return add_months(_check_date(start), MAX_RANGE_MONTHS) - dt.timedelta(days=1)


def validate_range(start: dt.date, end: dt.date, today: dt.date) -> Period:
    """Check a chosen range and return ``(start, end)``; otherwise raise ``ValidationError`` with the key
    REPORT_RANGE_ORDER (start after end), REPORT_RANGE_FUTURE (end after today) or REPORT_RANGE_TOO_LONG (more than 5
    calendar months). A value that is not a plain date raises INVALID_DATE. Checked in that order."""
    start, end, today = _check_date(start), _check_date(end), _check_date(today)
    if start > end:
        raise ValidationError("REPORT_RANGE_ORDER", detail="start is after end")
    if end > today:
        raise ValidationError("REPORT_RANGE_FUTURE", detail="end is in the future")
    if end > last_allowed_end(start):
        raise ValidationError("REPORT_RANGE_TOO_LONG", detail="range is longer than 5 calendar months")
    return start, end


def weekly_period(thursday: dt.date) -> Period:
    """The seven days Friday .. Thursday that end on ``thursday``."""
    if thursday.weekday() != THURSDAY:
        raise ValueError("a weekly period ends on a Thursday")
    return thursday - dt.timedelta(days=6), thursday


def monthly_generation_date(year: int, month: int) -> dt.date:
    """The day the monthly report of that month is made: the last day, or the Thursday before when it is a Fri/Sat."""
    last = dt.date(year, month, calendar.monthrange(year, month)[1])
    back = {FRIDAY: 1, SATURDAY: 2}.get(last.weekday(), 0)
    return last - dt.timedelta(days=back)


def monthly_period(year: int, month: int) -> Period:
    """The 1st of the month up to the generation date."""
    return dt.date(year, month, 1), monthly_generation_date(year, month)


def previous_period(kind: str, start: dt.date, end: dt.date) -> Period:
    """The period of the same type just before this one (the basis of 'change since last time')."""
    if kind == WEEKLY:
        return start - dt.timedelta(days=7), end - dt.timedelta(days=7)
    if kind == MONTHLY:
        before = start - dt.timedelta(days=1)
        return monthly_period(before.year, before.month)
    if kind in ADHOC_KINDS:                          # the same number of days, immediately before the start
        if end < start:
            raise ValueError("empty period")
        days = (end - start).days + 1
        before_end = start - dt.timedelta(days=1)
        return before_end - dt.timedelta(days=days - 1), before_end
    raise ValueError(f"unknown report type {kind!r}")


def reports_due(today: dt.date) -> list[tuple[str, dt.date, dt.date]]:
    """[(kind, period_start, period_end)] to be made on ``today``: empty on most days, two entries when a Thursday is
    also the day the monthly report is made."""
    if isinstance(today, dt.datetime) or not isinstance(today, dt.date):
        raise ValueError("today must be a date")
    due = []
    if today.weekday() == THURSDAY:
        due.append((WEEKLY, *weekly_period(today)))
    if today == monthly_generation_date(today.year, today.month):
        due.append((MONTHLY, *monthly_period(today.year, today.month)))
    return due
