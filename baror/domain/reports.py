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
"""
from __future__ import annotations

import calendar
import datetime as dt

WEEKLY, MONTHLY = "weekly", "monthly"
KINDS = (WEEKLY, MONTHLY)
THURSDAY, FRIDAY, SATURDAY = 3, 4, 5             # datetime.date.weekday(): Monday is 0

Period = tuple[dt.date, dt.date]                 # first and last day, both included


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
