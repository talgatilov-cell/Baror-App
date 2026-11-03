"""Turning what a cadet typed into a stored measurement, and grading it. Standard library only; no streamlit.

``parse_entry`` validates (future date, time format and plausibility, push-up range, at least one value) and raises
``ValidationError`` with a Hebrew message key. ``grade_entry`` grades each test SEPARATELY; there is no overall verdict.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from baror.domain import duration, scoring
from baror.domain.scoring import DomainError, Metric, Sex, Status
from baror.errors import ValidationError

MAX_PUSHUPS = 200   # scoring.PLAUSIBILITY hard bound
_LEVEL = {Status.EXCELLENT: "excellent", Status.PASS: "pass", Status.FAIL: "fail"}


@dataclass(frozen=True)
class Entry:
    test_date: str
    run_tenths: int | None
    pushups: int | None


@dataclass(frozen=True)
class Graded:
    test: str                   # 'run' | 'walk' | 'pushups'
    level: str | None           # 'excellent' | 'pass' | 'fail' | None (could not be graded)
    next_level: str | None      # the level the gap refers to
    gap: int | None             # seconds (run, walk) or repetitions (pushups) still missing; >= 1
    warn: bool = False          # inside the hard bounds but unusual


def parse_entry(*, run_text, pushups, test_date: date, today: date, is_km: bool) -> Entry:
    if not isinstance(test_date, date) or test_date > today:
        raise ValidationError("FUTURE_DATE", field="test_date", detail="date missing or in the future")
    run = None
    if run_text is not None and str(run_text).strip():
        run = duration.from_text(run_text)
        p = scoring.PLAUSIBILITY[Metric.aerobic_for(is_km)]
        if not p.hard_min <= run <= p.hard_max:
            raise ValidationError("TIME_OUT_OF_RANGE", field="run_time", detail="outside plausibility bounds",
                                  params={"lo": p.hard_min // 600, "hi": p.hard_max // 600})
    if pushups is not None:
        if isinstance(pushups, bool) or not isinstance(pushups, int) or not 0 <= pushups <= MAX_PUSHUPS:
            raise ValidationError("INVALID_RESULT", field="pushups", detail="push-ups outside 0..200")
    if run is None and pushups is None:
        raise ValidationError("EMPTY_RESULT", detail="nothing entered")
    return Entry(test_date.isoformat(), run, pushups)


def grade_value(test: str, sex_code: str, test_date: str, value: int) -> Graded:
    metric = {"run": Metric.RUN_3KM, "walk": Metric.WALK_6KM, "pushups": Metric.PUSHUPS}[test]
    try:
        rules = scoring.current_ruleset(date.fromisoformat(test_date))
        ev = scoring.evaluate_with(rules, metric, Sex.from_code(sex_code), value)
    except (DomainError, ValueError):
        return Graded(test, None, None, None)
    gap = None
    if ev.gap_to_next is not None:
        gap = max(1, math.ceil(ev.gap_to_next / 10)) if metric is not Metric.PUSHUPS else ev.gap_to_next
    return Graded(test, _LEVEL[ev.status], _LEVEL.get(ev.next_status), gap, bool(ev.warnings))


def grade_entry(entry: Entry, sex_code: str, is_km: bool) -> list[Graded]:
    out = []
    if entry.run_tenths is not None:
        out.append(grade_value("walk" if is_km else "run", sex_code, entry.test_date, entry.run_tenths))
    if entry.pushups is not None:
        out.append(grade_value("pushups", sex_code, entry.test_date, entry.pushups))
    return out
