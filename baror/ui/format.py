"""Turning graded values into Hebrew sentences. Pure functions: no streamlit, easy to test."""
from __future__ import annotations

from baror.domain.entry import Graded
from baror.domain.scoring import format_deciseconds
from baror.hebrew import count_phrase, duration_phrase
from baror.messages_he import t

_TEST_KEY = {"run": "test_run", "walk": "test_walk", "pushups": "test_pushups"}
_LEVEL_KEY = {"excellent": "status_excellent", "pass": "status_pass", "fail": "status_fail"}


def level_text(level: str | None) -> str:
    return t(_LEVEL_KEY[level]) if level in _LEVEL_KEY else t("status_ungraded")


def _gap(g: Graded) -> str:
    return count_phrase("rep", g.gap) if g.test == "pushups" else duration_phrase(g.gap)


def graded_lines(graded: list[Graded]) -> list[str]:
    """One line per test that was entered. A test is never combined with another into an overall verdict."""
    out = []
    for g in graded:
        test = t(_TEST_KEY[g.test])
        one = g.gap == 1
        if g.level is None:
            out.append(t("status_line", test=test, status=t("status_ungraded")))
        elif g.level == "fail" and g.gap:
            out.append(t("status_line", test=test, status=t("fail_with_gap_one" if one else "fail_with_gap", gap=_gap(g))))
        elif g.level == "pass" and g.gap:
            out.append(t("status_line", test=test, status=t("status_pass")) + ". "
                       + t("gap_to_excellent_one" if one else "gap_to_excellent", gap=_gap(g)))
        else:
            out.append(t("status_line", test=test, status=level_text(g.level)))
    return out


def time_text(tenths) -> str:
    return "" if tenths is None else format_deciseconds(tenths)
