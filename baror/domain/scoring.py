"""Fitness thresholds and per-test grading. Standard library only; never imports streamlit.

The numbers live in ``thresholds.json`` (data, versioned by ``effective_from``), not in code. They were confirmed by
the owner on 2026-11-02; no official document is cited. Each test is graded SEPARATELY: there is deliberately no
overall pass/fail verdict, because the owner has not yet explained "כ.מ" or the overall rule.

Conventions
-----------
* Run/walk times are integer DECISECONDS (tenths of a second); no float comparison is ever needed. Thresholds are
  inclusive ("<=" for time, ">=" for reps): a value exactly at a limit earns that level.
* Times are NEVER rounded for grading: 15:40.1 is one tenth over a 15:40.0 limit and therefore fails.
* Lower-is-better for time metrics, higher-is-better for rep metrics.
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class Status(Enum):
    FAIL = 0
    PASS = 1
    EXCELLENT = 2


class Metric(Enum):
    RUN_3KM = "run_3km"
    WALK_6KM = "walk_6km"          # the alternative track of users flagged ``is_km`` in the app
    PUSHUPS = "pushups"

    @classmethod
    def aerobic_for(cls, is_km: bool) -> "Metric":
        return cls.WALK_6KM if is_km else cls.RUN_3KM


class Direction(Enum):
    LOWER_IS_BETTER = "lower"
    HIGHER_IS_BETTER = "higher"


class Sex(Enum):
    """Sex/gender key used to SELECT a threshold row.

    UNSPECIFIED exists so the app never silently defaults to a row: grading
    with it raises ``RuleNotDefined``.
    """
    MALE = "male"
    FEMALE = "female"
    UNSPECIFIED = "unspecified"

    @classmethod
    def from_code(cls, code) -> "Sex":
        """Storage codes 'M'/'F' only. Anything else is UNSPECIFIED, which grading refuses (never a silent default)."""
        return {"M": cls.MALE, "F": cls.FEMALE}.get(code, cls.UNSPECIFIED)


class AttemptKind(Enum):
    PRACTICE = "practice"
    OFFICIAL = "official"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class DomainError(ValueError):
    pass


class InvalidMeasurement(DomainError):
    pass


class RuleNotDefined(DomainError):
    pass


# --------------------------------------------------------------------------
# Plausibility limits (hard = reject as typo, soft = accept but warn)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Plausibility:
    hard_min: int
    hard_max: int
    soft_min: int
    soft_max: int


# Run/walk in deciseconds; reps as plain ints.  Owner-adjustable guesses,
# NOT sourced from the IDF.
PLAUSIBILITY: Dict[Metric, Plausibility] = {
    Metric.RUN_3KM:  Plausibility(hard_min=4200,  hard_max=36000, soft_min=6000,  soft_max=24000),   # 7:00..60:00 hard, 10:00..40:00 soft
    Metric.WALK_6KM: Plausibility(hard_min=15000, hard_max=108000, soft_min=24000, soft_max=72000),  # 25:00..180:00 hard, 40:00..120:00 soft
    Metric.PUSHUPS:  Plausibility(hard_min=0,     hard_max=200,   soft_min=0,      soft_max=100),
}


# --------------------------------------------------------------------------
# Formatting and plausibility
# --------------------------------------------------------------------------
def format_deciseconds(ds: int) -> str:
    if ds < 0:
        raise InvalidMeasurement("negative time")
    total_s, tenths = divmod(ds, 10)
    mm, ss = divmod(total_s, 60)
    return f"{mm:02d}:{ss:02d}" + (f".{tenths}" if tenths else "")


def validate_measurement(metric: Metric, value: int) -> List[str]:
    """Return a list of warnings; raise InvalidMeasurement for impossible values."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidMeasurement(f"{metric.value}: value must be int, got {type(value).__name__}")
    p = PLAUSIBILITY[metric]
    if value < 0:
        raise InvalidMeasurement(f"{metric.value}: negative value")
    if value < p.hard_min or value > p.hard_max:
        raise InvalidMeasurement(f"{metric.value}: {value} outside hard bounds [{p.hard_min}, {p.hard_max}]")
    warnings: List[str] = []
    if value < p.soft_min or value > p.soft_max:
        warnings.append(f"{metric.value}: {value} outside typical range [{p.soft_min}, {p.soft_max}] - please double check")
    return warnings


# --------------------------------------------------------------------------
# Thresholds and versioned rule sets (DATA, not code)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Threshold:
    metric: Metric
    direction: Direction
    pass_limit: int                       # inclusive; deciseconds for time metrics
    excellent_limit: Optional[int] = None  # None => metric has no excellent level

    def __post_init__(self):
        if self.excellent_limit is not None:
            if self.direction is Direction.LOWER_IS_BETTER and self.excellent_limit > self.pass_limit:
                raise DomainError("excellent limit must be <= pass limit for lower-is-better metrics")
            if self.direction is Direction.HIGHER_IS_BETTER and self.excellent_limit < self.pass_limit:
                raise DomainError("excellent limit must be >= pass limit for higher-is-better metrics")


@dataclass(frozen=True)
class RuleSet:
    version: str
    effective_from: date
    source_ref: str                        # where the numbers came from
    confirmation: str                      # who confirmed them and when, e.g. "confirmed by owner, 2026-11-02"
    thresholds: Dict[Tuple[Metric, Sex], Threshold] = field(default_factory=dict)
    note: str = ""

    def get(self, metric: Metric, sex: Sex) -> Threshold:
        try:
            return self.thresholds[(metric, sex)]
        except KeyError:
            raise RuleNotDefined(f"no threshold for {metric.value}/{sex.value} in rule set {self.version}") from None


def select_ruleset(rulesets: Iterable[RuleSet], on: date) -> RuleSet:
    """Latest rule set whose effective_from <= ``on`` (a test is graded by the
    rules in force on ITS date, never by today's rules)."""
    candidates = [r for r in rulesets if r.effective_from <= on]
    if not candidates:
        raise RuleNotDefined(f"no rule set effective on {on.isoformat()}")
    return max(candidates, key=lambda r: r.effective_from)


L, H = Direction.LOWER_IS_BETTER, Direction.HIGHER_IS_BETTER

def ruleset_from_dict(d: dict) -> RuleSet:
    """Load a rule set from plain JSON-compatible data (owner-editable)."""
    th: Dict[Tuple[Metric, Sex], Threshold] = {}
    for row in d["thresholds"]:
        metric, sex = Metric(row["metric"]), Sex(row["sex"])
        direction = Direction(row["direction"])
        th[(metric, sex)] = Threshold(metric, direction, int(row["pass_limit"]),
                                      None if row.get("excellent_limit") is None else int(row["excellent_limit"]))
    return RuleSet(version=d["version"], effective_from=date.fromisoformat(d["effective_from"]),
                   source_ref=d["source_ref"], confirmation=str(d["confirmation"]),
                   thresholds=th, note=d.get("note", ""))


def ruleset_to_dict(r: RuleSet) -> dict:
    return {
        "version": r.version, "effective_from": r.effective_from.isoformat(),
        "source_ref": r.source_ref, "confirmation": r.confirmation, "note": r.note,
        "thresholds": [
            {"metric": m.value, "sex": s.value, "direction": t.direction.value,
             "pass_limit": t.pass_limit, "excellent_limit": t.excellent_limit}
            for (m, s), t in sorted(r.thresholds.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value))
        ],
    }


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Evaluation:
    metric: Metric
    value: int
    status: Status
    next_status: Optional[Status]          # None when already EXCELLENT (or PASS on a pass-only metric)
    gap_to_next: Optional[int]             # >0 in metric units still to gain (ds faster / reps more)
    warnings: Tuple[str, ...] = ()


def _meets(direction: Direction, value: int, limit: int) -> bool:
    return value <= limit if direction is Direction.LOWER_IS_BETTER else value >= limit


def evaluate(threshold: Threshold, value: int) -> Evaluation:
    warnings = validate_measurement(threshold.metric, value)
    d = threshold.direction
    if threshold.excellent_limit is not None and _meets(d, value, threshold.excellent_limit):
        return Evaluation(threshold.metric, value, Status.EXCELLENT, None, None, tuple(warnings))
    if _meets(d, value, threshold.pass_limit):
        if threshold.excellent_limit is None:
            return Evaluation(threshold.metric, value, Status.PASS, None, None, tuple(warnings))
        gap = abs(value - threshold.excellent_limit)
        return Evaluation(threshold.metric, value, Status.PASS, Status.EXCELLENT, gap, tuple(warnings))
    gap = abs(value - threshold.pass_limit)
    return Evaluation(threshold.metric, value, Status.FAIL, Status.PASS, gap, tuple(warnings))


def evaluate_with(ruleset: RuleSet, metric: Metric, sex: Sex, value: int) -> Evaluation:
    if sex is Sex.UNSPECIFIED:
        raise RuleNotDefined("sex must be male or female to choose a threshold row")
    return evaluate(ruleset.get(metric, sex), value)


# --------------------------------------------------------------------------
# Attempts and personal best
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Attempt:
    on: date
    kind: AttemptKind
    metric: Metric
    value: int
    ruleset_version: Optional[str] = None   # snapshot of rules used at grading time


def personal_best(attempts: Iterable[Attempt], metric: Metric,
                  kind: Optional[AttemptKind] = None) -> Optional[Attempt]:
    """Best attempt for ONE metric.  Metrics are never mixed (3 km run and
    6 km walk are different metrics).  Ties resolve to the earliest date."""
    direction = L if metric in (Metric.RUN_3KM, Metric.WALK_6KM) else H
    pool = [a for a in attempts if a.metric is metric and (kind is None or a.kind is kind)]
    if not pool:
        return None
    key = (lambda a: (a.value, a.on)) if direction is L else (lambda a: (-a.value, a.on))
    return min(pool, key=key)


# --------------------------------------------------------------------------
# The rule set shipped with the app
# --------------------------------------------------------------------------
THRESHOLDS_FILE = Path(__file__).with_name("thresholds.json")


def load_rulesets(path: Path = THRESHOLDS_FILE) -> List[RuleSet]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [ruleset_from_dict(d) for d in data["rulesets"]]


@functools.lru_cache(maxsize=1)
def _shipped_rulesets() -> Tuple[RuleSet, ...]:
    """Static data shipped with the app (no user data), parsed once per process."""
    return tuple(load_rulesets())


def current_ruleset(on: date) -> RuleSet:
    """The shipped rule set in force on ``on`` (a test is graded by the rules of ITS date)."""
    return select_ruleset(_shipped_rulesets(), on)
