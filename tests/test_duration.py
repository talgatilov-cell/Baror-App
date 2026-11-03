import unittest

from baror.domain import duration as D
from baror.errors import ValidationError


class FromText(unittest.TestCase):
    def test_valid(self):
        for text, tenths in [("15:40", 9400), ("0:01", 10), ("1:00", 600), (" 15:40 ", 9400), ("66:00", 39600),
                             ("120:00", 72000), ("٠٩:٣٠", 5700), ("007:05", 4250)]:
            self.assertEqual(D.from_text(text), tenths, text)

    def test_brief_boundary_cases_are_rejected_with_a_message_key(self):
        cases = {"0:00": "TIME_ZERO", "-5:00": "INVALID_TIME_FORMAT", "99:99": "TIME_SECONDS_RANGE",
                 "15:60": "TIME_SECONDS_RANGE", "15:5": "INVALID_TIME_FORMAT", "abc": "INVALID_TIME_FORMAT",
                 "121:00": "TIME_OUT_OF_RANGE"}
        for text, key in cases.items():
            with self.assertRaises(ValidationError, msg=text) as cm:
                D.from_text(text)
            self.assertEqual((cm.exception.message_key, cm.exception.field), (key, "run_time"), text)

    def test_junk_never_raises_valueerror_or_echoes_input(self):
        for bad in ["", "   ", None, 940, "14", "14:", ":30", "14.30", "1:2:3", "14:30:00", "5:-1", "٤:३०", "14 :30",
                    "1e3:00", "１４:３０", "99999999:00", "\n", "14:30\n14:30", "NaN", "x" * 50, "15:40.3"]:
            with self.assertRaises(ValidationError, msg=repr(bad)) as cm:
                D.from_text(bad)
            self.assertNotIn("xxxx", str(cm.exception))


class FromFields(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(D.from_fields(15, 40), 9400)
        self.assertEqual(D.from_fields(0, 1), 10)
        self.assertEqual(D.from_fields(120, 0), 72000)

    def test_invalid(self):
        for args, key in [((0, 0), "TIME_ZERO"), ((15, 60), "TIME_SECONDS_RANGE"), ((15, -1), "TIME_SECONDS_RANGE"),
                          ((-1, 30), "TIME_OUT_OF_RANGE"), ((121, 0), "TIME_OUT_OF_RANGE"),
                          ((None, 30), "INVALID_TIME_FORMAT"), ((15, None), "INVALID_TIME_FORMAT"),
                          ((15.5, 3), "INVALID_TIME_FORMAT"), ((True, 3), "INVALID_TIME_FORMAT"),
                          (("15", "40"), "INVALID_TIME_FORMAT")]:
            with self.assertRaises(ValidationError, msg=str(args)) as cm:
                D.from_fields(*args)
            self.assertEqual(cm.exception.message_key, key, args)

    def test_result_fits_the_storage_range(self):
        from baror.storage import repo  # run_tenths CHECK is 1..72000
        for m, s in [(0, 1), (120, 0), (59, 59)]:
            self.assertTrue(1 <= D.from_fields(m, s) <= 72000)
        self.assertTrue(repo)


if __name__ == "__main__":
    unittest.main()
