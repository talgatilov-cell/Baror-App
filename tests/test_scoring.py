import json
import re
import unittest
from datetime import date

from baror.domain import scoring
from baror.domain.scoring import (
    Attempt, AttemptKind, Direction, DomainError, InvalidMeasurement, Metric, RuleNotDefined, RuleSet, Sex, Status,
    Threshold, evaluate_with, format_deciseconds, personal_best, ruleset_from_dict, ruleset_to_dict, select_ruleset,
)

R = scoring.current_ruleset(date(2026, 11, 3))


def P(text):
    """Test helper only: 'mm:ss' or 'mm:ss.t' -> deciseconds. (The app's parser is baror.domain.duration.)"""
    m = re.fullmatch(r"(\d+):(\d\d)(?:\.(\d))?", text)
    return (int(m.group(1)) * 60 + int(m.group(2))) * 10 + int(m.group(3) or 0)


def run(sex, secs_tenths):
    return evaluate_with(R, Metric.RUN_3KM, sex, secs_tenths)


class TestFormat(unittest.TestCase):
    def test_format_roundtrip(self):
        for t in ["12:30", "15:40.3", "00:00", "66:00"]:
            self.assertEqual(format_deciseconds(P(t)), t)
        with self.assertRaises(InvalidMeasurement):
            format_deciseconds(-1)


class TestRunMale(unittest.TestCase):
    def test_exactly_excellent(self):
        self.assertEqual(run(Sex.MALE, P("12:30")).status, Status.EXCELLENT)

    def test_one_second_over_excellent_is_pass(self):
        e = run(Sex.MALE, P("12:31"))
        self.assertEqual((e.status, e.next_status, e.gap_to_next), (Status.PASS, Status.EXCELLENT, 10))

    def test_one_tenth_over_excellent_is_pass_no_rounding(self):
        e = run(Sex.MALE, P("12:30.1"))
        self.assertEqual((e.status, e.gap_to_next), (Status.PASS, 1))

    def test_exactly_pass(self):
        e = run(Sex.MALE, P("15:40"))
        self.assertEqual(e.status, Status.PASS)
        self.assertEqual(e.gap_to_next, (9400 - 7500))

    def test_one_second_over_pass_is_fail(self):
        e = run(Sex.MALE, P("15:41"))
        self.assertEqual((e.status, e.next_status, e.gap_to_next), (Status.FAIL, Status.PASS, 10))

    def test_one_tenth_over_pass_fails(self):
        self.assertEqual(run(Sex.MALE, P("15:40.1")).status, Status.FAIL)


class TestRunFemale(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(run(Sex.FEMALE, P("14:40")).status, Status.EXCELLENT)
        self.assertEqual(run(Sex.FEMALE, P("14:41")).status, Status.PASS)
        self.assertEqual(run(Sex.FEMALE, P("19:40")).status, Status.PASS)
        self.assertEqual(run(Sex.FEMALE, P("19:41")).status, Status.FAIL)


class TestWalk(unittest.TestCase):
    def test_boundaries_and_no_excellent(self):
        for sex in (Sex.MALE, Sex.FEMALE):
            ok = evaluate_with(R, Metric.WALK_6KM, sex, P("66:00"))
            self.assertEqual((ok.status, ok.next_status, ok.gap_to_next), (Status.PASS, None, None))
            self.assertEqual(evaluate_with(R, Metric.WALK_6KM, sex, P("66:01")).status, Status.FAIL)
            self.assertEqual(evaluate_with(R, Metric.WALK_6KM, sex, P("40:00")).status, Status.PASS)


class TestPushups(unittest.TestCase):
    def test_male(self):
        f = lambda n: evaluate_with(R, Metric.PUSHUPS, Sex.MALE, n)
        self.assertEqual(f(0).status, Status.FAIL)
        self.assertEqual(f(21).status, Status.FAIL)
        self.assertEqual(f(21).gap_to_next, 1)
        self.assertEqual(f(22).status, Status.PASS)
        self.assertEqual(f(35).status, Status.PASS)
        self.assertEqual(f(35).gap_to_next, 1)
        self.assertEqual(f(36).status, Status.EXCELLENT)
        self.assertIsNone(f(36).gap_to_next)

    def test_female(self):
        f = lambda n: evaluate_with(R, Metric.PUSHUPS, Sex.FEMALE, n)
        self.assertEqual([f(n).status for n in (0, 10, 11, 29, 30)],
                         [Status.FAIL, Status.FAIL, Status.PASS, Status.PASS, Status.EXCELLENT])

    def test_zero_is_valid_fail(self):
        e = evaluate_with(R, Metric.PUSHUPS, Sex.MALE, 0)
        self.assertEqual((e.status, e.gap_to_next), (Status.FAIL, 22))


class TestInvalidAndExtreme(unittest.TestCase):
    def test_negative(self):
        with self.assertRaises(InvalidMeasurement):
            evaluate_with(R, Metric.PUSHUPS, Sex.MALE, -1)
        with self.assertRaises(InvalidMeasurement):
            run(Sex.MALE, -10)

    def test_zero_run_time_is_rejected_not_excellent(self):
        # The current app accepts "0:00" and grades it EXCELLENT.
        with self.assertRaises(InvalidMeasurement):
            run(Sex.MALE, 0)

    def test_impossibly_fast_run(self):
        with self.assertRaises(InvalidMeasurement):
            run(Sex.MALE, P("5:00"))

    def test_hard_bounds_run(self):
        run(Sex.MALE, 4200)          # 7:00 accepted (hard_min)
        with self.assertRaises(InvalidMeasurement):
            run(Sex.MALE, 4199)
        run(Sex.MALE, 36000)         # 60:00 accepted
        with self.assertRaises(InvalidMeasurement):
            run(Sex.MALE, 36001)

    def test_soft_warning(self):
        e = run(Sex.MALE, P("9:00"))
        self.assertEqual(e.status, Status.EXCELLENT)
        self.assertTrue(e.warnings)
        self.assertFalse(run(Sex.MALE, P("13:00")).warnings)

    def test_extreme_pushups(self):
        with self.assertRaises(InvalidMeasurement):
            evaluate_with(R, Metric.PUSHUPS, Sex.MALE, 201)
        e = evaluate_with(R, Metric.PUSHUPS, Sex.MALE, 150)
        self.assertEqual(e.status, Status.EXCELLENT)
        self.assertTrue(e.warnings)

    def test_types(self):
        for bad in (True, 12.5, "20", None):
            with self.assertRaises(InvalidMeasurement):
                evaluate_with(R, Metric.PUSHUPS, Sex.MALE, bad)

    def test_unspecified_sex_never_defaults(self):
        with self.assertRaises(RuleNotDefined):
            evaluate_with(R, Metric.PUSHUPS, Sex.UNSPECIFIED, 30)

    def test_missing_row(self):
        empty = RuleSet("x", date(2030, 1, 1), "n/a", "n/a")
        with self.assertRaises(RuleNotDefined):
            evaluate_with(empty, Metric.PUSHUPS, Sex.MALE, 30)


class TestThresholdValidation(unittest.TestCase):
    def test_inconsistent_thresholds_rejected(self):
        with self.assertRaises(DomainError):
            Threshold(Metric.RUN_3KM, Direction.LOWER_IS_BETTER, 9000, 9500)
        with self.assertRaises(DomainError):
            Threshold(Metric.PUSHUPS, Direction.HIGHER_IS_BETTER, 30, 20)


class TestVersioning(unittest.TestCase):
    def setUp(self):
        d = ruleset_to_dict(R)
        d["version"], d["effective_from"], d["confirmation"] = "v2", "2026-01-01", "test"
        for row in d["thresholds"]:
            if row["metric"] == "pushups" and row["sex"] == "male":
                row["pass_limit"], row["excellent_limit"] = 25, 40
        self.v2 = ruleset_from_dict(json.loads(json.dumps(d)))

    def test_select_by_date(self):
        rs = [R, self.v2]
        self.assertEqual(select_ruleset(rs, date(2025, 12, 31)).version, R.version)
        self.assertEqual(select_ruleset(rs, date(2026, 1, 1)).version, "v2")
        with self.assertRaises(RuleNotDefined):
            select_ruleset(rs, date(1999, 1, 1))

    def test_old_result_graded_by_old_rules(self):
        old = evaluate_with(select_ruleset([R, self.v2], date(2025, 6, 1)), Metric.PUSHUPS, Sex.MALE, 23)
        new = evaluate_with(select_ruleset([R, self.v2], date(2026, 6, 1)), Metric.PUSHUPS, Sex.MALE, 23)
        self.assertEqual((old.status, new.status), (Status.PASS, Status.FAIL))

    def test_roundtrip(self):
        self.assertEqual(ruleset_from_dict(ruleset_to_dict(R)), R)



class TestShippedThresholds(unittest.TestCase):
    """Guards the owner-confirmed numbers: changing them must be a deliberate, reviewed act."""

    def test_numbers_are_exactly_the_confirmed_ones(self):
        got = {(t.metric.value, sex.value): (t.pass_limit, t.excellent_limit) for (_m, sex), t in R.thresholds.items()}
        self.assertEqual(got, {
            ("run_3km", "male"): (9400, 7500), ("run_3km", "female"): (11800, 8800),           # 15:40/12:30, 19:40/14:40
            ("walk_6km", "male"): (39600, None), ("walk_6km", "female"): (39600, None),        # 66:00, pass only
            ("pushups", "male"): (22, 36), ("pushups", "female"): (11, 30),
        })

    def test_the_file_records_the_owner_confirmation_and_no_verified_flag(self):
        raw = json.loads(scoring.THRESHOLDS_FILE.read_text(encoding="utf-8"))
        rs = raw["rulesets"][0]
        self.assertEqual(rs["confirmation"], "confirmed by owner, 2026-11-02")
        self.assertNotIn("verified", rs)
        self.assertFalse(hasattr(R, "verified"))

    def test_the_shipped_file_is_parsed_once_not_per_grade(self):
        from unittest import mock
        scoring._shipped_rulesets.cache_clear()
        with mock.patch.object(scoring.json, "loads", wraps=scoring.json.loads) as loads:
            for _ in range(500):
                scoring.current_ruleset(date(2026, 11, 3))
        self.assertEqual(loads.call_count, 1)

    def test_there_is_no_overall_verdict(self):
        # Owner has not yet explained "כ.מ" or the overall pass rule: nothing may combine the tests.
        self.assertFalse(hasattr(scoring, "overall_status"))
        self.assertFalse([n for n in dir(scoring) if "overall" in n.lower()])

    def test_storage_codes_map_to_sex_and_unknown_never_defaults(self):
        self.assertEqual((Sex.from_code("M"), Sex.from_code("F")), (Sex.MALE, Sex.FEMALE))
        for bad in (None, "", "m", "זכר", "X"):
            self.assertEqual(Sex.from_code(bad), Sex.UNSPECIFIED)
        self.assertEqual((Metric.aerobic_for(True), Metric.aerobic_for(False)), (Metric.WALK_6KM, Metric.RUN_3KM))


class TestPersonalBest(unittest.TestCase):
    def A(self, d, m, v, k=AttemptKind.PRACTICE):
        return Attempt(date(2026, 1, d), k, m, v)

    def test_run_lower_is_better(self):
        xs = [self.A(1, Metric.RUN_3KM, 9400), self.A(2, Metric.RUN_3KM, 8800), self.A(3, Metric.RUN_3KM, 9000)]
        self.assertEqual(personal_best(xs, Metric.RUN_3KM).value, 8800)

    def test_pushups_higher_is_better(self):
        xs = [self.A(1, Metric.PUSHUPS, 20), self.A(2, Metric.PUSHUPS, 31), self.A(3, Metric.PUSHUPS, 25)]
        self.assertEqual(personal_best(xs, Metric.PUSHUPS).value, 31)

    def test_tie_earliest(self):
        xs = [self.A(5, Metric.PUSHUPS, 30), self.A(2, Metric.PUSHUPS, 30)]
        self.assertEqual(personal_best(xs, Metric.PUSHUPS).on, date(2026, 1, 2))

    def test_metrics_not_mixed(self):
        xs = [self.A(1, Metric.WALK_6KM, 30000), self.A(2, Metric.RUN_3KM, 9000)]
        self.assertEqual(personal_best(xs, Metric.RUN_3KM).value, 9000)
        self.assertIsNone(personal_best(xs, Metric.PUSHUPS))

    def test_official_filter_and_empty(self):
        xs = [self.A(1, Metric.RUN_3KM, 8000), self.A(2, Metric.RUN_3KM, 9000, AttemptKind.OFFICIAL)]
        self.assertEqual(personal_best(xs, Metric.RUN_3KM, AttemptKind.OFFICIAL).value, 9000)
        self.assertIsNone(personal_best([], Metric.RUN_3KM))


if __name__ == "__main__":
    unittest.main()
