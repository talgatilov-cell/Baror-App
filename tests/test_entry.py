import unittest
from datetime import date

from baror.domain import entry as E
from baror.errors import ValidationError

TODAY = date(2026, 11, 3)


def parse(run="15:40", pushups=30, d=TODAY, km=False):
    return E.parse_entry(run_text=run, pushups=pushups, test_date=d, today=TODAY, is_km=km)


class Parse(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse(), E.Entry("2026-11-03", 9400, 30))
        self.assertEqual(parse(run="", pushups=0), E.Entry("2026-11-03", None, 0), "0 push-ups is a real value")
        self.assertEqual(parse(run="  15:40 ", pushups=None).pushups, None)
        self.assertEqual(parse(run="66:00", km=True).run_tenths, 39600)

    def test_each_failure_has_its_message_key_and_field(self):
        cases = [(dict(d=date(2026, 11, 4)), "FUTURE_DATE", "test_date"), (dict(d=None), "FUTURE_DATE", "test_date"),
                 (dict(run="", pushups=None), "EMPTY_RESULT", None), (dict(run=None, pushups=None), "EMPTY_RESULT", None),
                 (dict(run="0:00"), "TIME_ZERO", "run_time"), (dict(run="15:60"), "TIME_SECONDS_RANGE", "run_time"),
                 (dict(run="abc"), "INVALID_TIME_FORMAT", "run_time"), (dict(run="15:5"), "INVALID_TIME_FORMAT", "run_time"),
                 (dict(run="5:00"), "TIME_OUT_OF_RANGE", "run_time"), (dict(run="61:00"), "TIME_OUT_OF_RANGE", "run_time"),
                 (dict(run="20:00", km=True), "TIME_OUT_OF_RANGE", "run_time"),
                 (dict(pushups=201), "INVALID_RESULT", "pushups"), (dict(pushups=-1), "INVALID_RESULT", "pushups"),
                 (dict(pushups=2.5), "INVALID_RESULT", "pushups"), (dict(pushups=True), "INVALID_RESULT", "pushups")]
        for kw, key, field in cases:
            with self.assertRaises(ValidationError, msg=str(kw)) as cm:
                parse(**kw)
            self.assertEqual((cm.exception.message_key, cm.exception.field), (key, field), str(kw))

    def test_plausibility_bounds_are_inclusive_and_reported_in_minutes(self):
        self.assertEqual(parse(run="7:00").run_tenths, 4200)
        self.assertEqual(parse(run="60:00").run_tenths, 36000)
        with self.assertRaises(ValidationError) as cm:
            parse(run="6:59")
        self.assertEqual(cm.exception.params, {"lo": 7, "hi": 60})
        with self.assertRaises(ValidationError) as cm:
            parse(run="10:00", km=True)
        self.assertEqual(cm.exception.params, {"lo": 25, "hi": 180})


class Grade(unittest.TestCase):
    def grade(self, run="15:40", pushups=30, sex="M", km=False):
        return {g.test: g for g in E.grade_entry(parse(run=run, pushups=pushups, km=km), sex, km)}

    def test_each_test_is_graded_on_its_own_and_there_is_no_overall_field(self):
        g = self.grade(run="12:30", pushups=10)
        self.assertEqual((g["run"].level, g["pushups"].level), ("excellent", "fail"))
        self.assertFalse([f for f in E.Graded.__dataclass_fields__ if "overall" in f])

    def test_gaps_in_seconds_rounded_up_and_in_reps(self):
        g = self.grade(run="15:41", pushups=20)
        self.assertEqual((g["run"].level, g["run"].next_level, g["run"].gap), ("fail", "pass", 1))
        self.assertEqual((g["pushups"].level, g["pushups"].next_level, g["pushups"].gap), ("fail", "pass", 2))
        g = self.grade(run="15:40", pushups=22)
        self.assertEqual((g["run"].level, g["run"].next_level, g["run"].gap), ("pass", "excellent", 190))
        self.assertEqual((g["pushups"].level, g["pushups"].gap), ("pass", 14))
        g = self.grade(run="12:30", pushups=36)
        self.assertEqual((g["run"].level, g["run"].gap, g["pushups"].gap), ("excellent", None, None))

    def test_female_scale_and_walk_track(self):
        g = self.grade(run="19:40", pushups=11, sex="F")
        self.assertEqual((g["run"].level, g["pushups"].level), ("pass", "pass"))
        g = self.grade(run="66:00", pushups=None, km=True)
        self.assertEqual((list(g), g["walk"].level, g["walk"].next_level), (["walk"], "pass", None))
        self.assertEqual(self.grade(run="66:01", pushups=None, km=True)["walk"].level, "fail")

    def test_unknown_sex_is_not_graded_instead_of_defaulting(self):
        g = E.grade_entry(parse(), "X", False)
        self.assertEqual([x.level for x in g], [None, None])

    def test_only_entered_tests_are_returned(self):
        self.assertEqual([g.test for g in E.grade_entry(parse(run="", pushups=30), "M", False)], ["pushups"])


if __name__ == "__main__":
    unittest.main()
