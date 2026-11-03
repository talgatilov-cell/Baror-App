"""Adapter between stored values and the scoring rules, for code that must not import the domain itself.

``baror.storage.chief`` aggregates per level but stays free of the domain package; it receives ``grade_stored_value``
as a function. Standard library only; never imports streamlit.
"""
from __future__ import annotations

from datetime import date

from baror.domain import scoring
from baror.domain.scoring import DomainError, Metric, Sex, Status

_LEVEL = {Status.EXCELLENT: "excellent", Status.PASS: "pass", Status.FAIL: "fail"}
_METRIC = {"run": Metric.RUN_3KM, "walk": Metric.WALK_6KM, "pushups": Metric.PUSHUPS}


def grade_stored_value(metric: str, gender: str, test_date: str, value: int) -> str | None:
    """'run' | 'walk' | 'pushups' + 'M'/'F' + ISO date + stored value (tenths of a second, or repetitions) ->
    'excellent' | 'pass' | 'fail', or None if the value cannot be graded (implausible, unknown sex, no rule for the date).
    Each test is graded on its own; there is no overall verdict."""
    try:
        rules = scoring.current_ruleset(date.fromisoformat(test_date))
        ev = scoring.evaluate_with(rules, _METRIC[metric], Sex.from_code(gender), value)
    except (DomainError, ValueError, KeyError):
        return None
    return _LEVEL[ev.status]
