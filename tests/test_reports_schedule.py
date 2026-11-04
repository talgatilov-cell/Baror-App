"""baror.domain.reports: which reports are due on a date and which dates they cover (Thursday weekly, month-end monthly)."""
import calendar
import datetime as dt
import unittest

from baror.domain import reports as r
from baror.errors import ValidationError

D = dt.date


class Weekly(unittest.TestCase):
    def test_a_normal_thursday_makes_the_weekly_report_friday_to_thursday(self):
        self.assertEqual(r.reports_due(D(2026, 11, 5)), [("weekly", D(2026, 10, 30), D(2026, 11, 5))])
        self.assertEqual(D(2026, 10, 30).strftime("%A"), "Friday")

    def test_consecutive_weekly_periods_touch_with_no_gap_and_no_overlap(self):
        thursdays = [D(2026, 11, 5) + dt.timedelta(days=7 * i) for i in range(60)]
        periods = [r.weekly_period(x) for x in thursdays]
        for (s1, e1), (s2, e2) in zip(periods, periods[1:]):
            self.assertEqual(s2 - e1, dt.timedelta(days=1))
            self.assertEqual((e1 - s1).days, 6)

    def test_every_other_weekday_makes_no_weekly_report(self):
        days = [D(2026, 11, 1) + dt.timedelta(days=i) for i in range(7)]            # Sunday .. Saturday
        due = {d.strftime("%a"): [k for k, *_ in r.reports_due(d)] for d in days}
        self.assertEqual({k: v for k, v in due.items() if "weekly" in v}, {"Thu": ["weekly"]})

    def test_a_weekly_period_must_end_on_a_thursday(self):
        with self.assertRaises(ValueError):
            r.weekly_period(D(2026, 11, 4))


class Monthly(unittest.TestCase):
    def test_month_end_sunday_to_thursday_is_made_on_the_last_day(self):
        for day, name in ((D(2026, 5, 31), "Sunday"), (D(2026, 8, 31), "Monday"), (D(2026, 3, 31), "Tuesday"),
                          (D(2026, 9, 30), "Wednesday")):
            self.assertEqual(day.strftime("%A"), name)
            self.assertEqual(r.reports_due(day), [("monthly", day.replace(day=1), day)], name)

    def test_month_end_on_friday_is_made_on_the_thursday_before(self):
        self.assertEqual(D(2026, 7, 31).strftime("%A"), "Friday")
        self.assertEqual(r.monthly_generation_date(2026, 7), D(2026, 7, 30))
        self.assertIn(("monthly", D(2026, 7, 1), D(2026, 7, 30)), r.reports_due(D(2026, 7, 30)))
        self.assertEqual([k for k, *_ in r.reports_due(D(2026, 7, 31))], [], "Friday: nothing is due")

    def test_month_end_on_saturday_is_made_on_the_thursday_two_days_before(self):
        self.assertEqual(D(2026, 10, 31).strftime("%A"), "Saturday")
        self.assertEqual(r.monthly_generation_date(2026, 10), D(2026, 10, 29))
        self.assertIn(("monthly", D(2026, 10, 1), D(2026, 10, 29)), r.reports_due(D(2026, 10, 29)))
        self.assertEqual(r.reports_due(D(2026, 10, 31)), [])
        self.assertEqual(r.reports_due(D(2026, 10, 30)), [])

    def test_a_thursday_that_is_also_the_month_end_makes_both_reports(self):
        self.assertEqual(D(2026, 4, 30).strftime("%A"), "Thursday")
        self.assertEqual(r.reports_due(D(2026, 4, 30)),
                         [("weekly", D(2026, 4, 24), D(2026, 4, 30)), ("monthly", D(2026, 4, 1), D(2026, 4, 30))])
        self.assertEqual(len(r.reports_due(D(2026, 12, 31))), 2)

    def test_february_in_leap_and_common_years(self):
        self.assertEqual(r.monthly_generation_date(2028, 2), D(2028, 2, 29))          # leap year, Tuesday
        self.assertEqual(len(r.reports_due(D(2024, 2, 29))), 2)                      # leap year, Thursday: both
        self.assertEqual(r.monthly_generation_date(2025, 2), D(2025, 2, 27))          # 28 Feb 2025 is a Friday
        self.assertEqual(r.monthly_generation_date(2026, 2), D(2026, 2, 26))          # 28 Feb 2026 is a Saturday
        self.assertEqual(r.monthly_generation_date(2027, 2), D(2027, 2, 28))          # a Sunday

    def test_exactly_one_generation_day_per_month_for_ten_years(self):
        for year in range(2024, 2034):
            for month in range(1, 13):
                gen = r.monthly_generation_date(year, month)
                self.assertEqual((gen.year, gen.month), (year, month))
                self.assertNotIn(gen.weekday(), (4, 5))
                days = [dt.date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]
                made = [d for d in days if any(k == "monthly" for k, *_ in r.reports_due(d))]
                self.assertEqual(made, [gen])
                self.assertGreaterEqual((dt.date(year, month, calendar.monthrange(year, month)[1]) - gen).days, 0)
                self.assertLessEqual((dt.date(year, month, calendar.monthrange(year, month)[1]) - gen).days, 2)

    def test_friday_and_saturday_never_make_anything(self):
        d = D(2026, 1, 1)
        while d < D(2028, 1, 1):
            if d.weekday() in (4, 5):
                self.assertEqual(r.reports_due(d), [], d)
            d += dt.timedelta(days=1)

    def test_previous_periods(self):
        self.assertEqual(r.previous_period("weekly", D(2026, 10, 30), D(2026, 11, 5)), (D(2026, 10, 23), D(2026, 10, 29)))
        self.assertEqual(r.previous_period("monthly", D(2026, 11, 1), D(2026, 11, 30)), (D(2026, 10, 1), D(2026, 10, 29)))
        self.assertEqual(r.previous_period("monthly", D(2027, 1, 1), D(2027, 1, 31)), (D(2026, 12, 1), D(2026, 12, 31)))
        with self.assertRaises(ValueError):
            r.previous_period("daily", D(2026, 1, 1), D(2026, 1, 1))

    def test_days_after_the_generation_date_are_in_no_monthly_report_but_in_a_weekly_one(self):
        """Documented gap: 30 and 31 October 2026 (Friday, Saturday) are after the October report and before November."""
        oct_end = r.monthly_period(2026, 10)[1]
        nov_start = r.monthly_period(2026, 11)[0]
        gap = [oct_end + dt.timedelta(days=i) for i in range(1, (nov_start - oct_end).days)]
        self.assertEqual(gap, [D(2026, 10, 30), D(2026, 10, 31)])
        weekly = r.reports_due(D(2026, 11, 5))[0]
        self.assertTrue(all(weekly[1] <= d <= weekly[2] for d in gap))

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            r.reports_due(dt.datetime(2026, 11, 5, 8, 0))
        with self.assertRaises(ValueError):
            r.reports_due("2026-11-05")


class Trailing(unittest.TestCase):
    def test_last_30_days_ends_today_and_starts_30_days_back(self):
        self.assertEqual(r.trailing_period(D(2026, 11, 4)), (D(2026, 10, 5), D(2026, 11, 4)))
        start, end = r.trailing_period(D(2026, 3, 1))
        self.assertEqual((start, end), (D(2026, 1, 30), D(2026, 3, 1)))       # across February
        self.assertEqual(r.trailing_period(D(2028, 3, 1))[0], D(2028, 1, 31))  # leap year
        self.assertEqual((end - start).days, 30)

    def test_previous_is_the_same_length_immediately_before(self):
        for kind in ("trailing", "range"):
            self.assertEqual(r.previous_period(kind, D(2026, 10, 5), D(2026, 11, 4)), (D(2026, 9, 4), D(2026, 10, 4)))
            ps, pe = r.previous_period(kind, D(2026, 2, 1), D(2026, 2, 28))
            self.assertEqual((ps, pe), (D(2026, 1, 4), D(2026, 1, 31)))
            self.assertEqual(r.previous_period(kind, D(2026, 5, 7), D(2026, 5, 7)), (D(2026, 5, 6), D(2026, 5, 6)))
        with self.assertRaises(ValueError):
            r.previous_period("range", D(2026, 5, 7), D(2026, 5, 6))

    def test_the_stored_kinds_are_unchanged(self):
        self.assertEqual(r.KINDS, ("weekly", "monthly"))
        self.assertEqual(r.ADHOC_KINDS, ("trailing", "range"))

    def test_bad_input(self):
        for bad in (dt.datetime(2026, 11, 4, 8), "2026-11-04", None):
            with self.assertRaises(ValidationError) as cm:
                r.trailing_period(bad)
            self.assertEqual(cm.exception.message_key, "INVALID_DATE")


class RangeValidation(unittest.TestCase):
    TODAY = D(2026, 11, 4)

    def key(self, start, end, today=None):
        with self.assertRaises(ValidationError) as cm:
            r.validate_range(start, end, today or self.TODAY)
        return cm.exception.message_key

    def test_a_good_range_is_returned(self):
        self.assertEqual(r.validate_range(D(2026, 9, 1), D(2026, 11, 4), self.TODAY), (D(2026, 9, 1), D(2026, 11, 4)))
        self.assertEqual(r.validate_range(D(2026, 11, 4), D(2026, 11, 4), self.TODAY), (D(2026, 11, 4), D(2026, 11, 4)))

    def test_start_after_end(self):
        self.assertEqual(self.key(D(2026, 11, 3), D(2026, 11, 2)), "REPORT_RANGE_ORDER")

    def test_future_end(self):
        self.assertEqual(self.key(D(2026, 11, 1), D(2026, 11, 5)), "REPORT_RANGE_FUTURE")
        self.assertEqual(self.key(D(2026, 11, 9), D(2026, 11, 5)), "REPORT_RANGE_ORDER", "order is checked first")

    def test_five_calendar_months_exactly_is_allowed_and_one_day_more_is_not(self):
        today = D(2030, 1, 1)
        for start, last in ((D(2026, 1, 1), D(2026, 5, 31)), (D(2026, 3, 1), D(2026, 7, 31)),
                            (D(2026, 1, 31), D(2026, 6, 29)),          # 31 Jan + 5 months clamps to 30 Jun, less a day
                            (D(2026, 8, 31), D(2027, 1, 30)),          # 31 Aug + 5 months clamps to 31 Jan, less a day
                            (D(2026, 10, 15), D(2027, 3, 14)),         # across the year
                            (D(2028, 2, 29), D(2028, 7, 28)),          # leap day
                            (D(2026, 9, 30), D(2027, 2, 27)),          # 30 Sep + 5 months clamps to 28 Feb, less a day
                            (D(2026, 12, 31), D(2027, 5, 30))):
            self.assertEqual(r.last_allowed_end(start), last, start)
            self.assertEqual(r.validate_range(start, last, today), (start, last))
            self.assertEqual(self.key(start, last + dt.timedelta(days=1), today), "REPORT_RANGE_TOO_LONG", start)

    def test_boundaries_hold_for_every_start_day_of_two_years(self):
        today = D(2040, 1, 1)
        day = D(2027, 1, 1)
        while day < D(2029, 1, 1):
            last = r.last_allowed_end(day)
            self.assertEqual(r.validate_range(day, last, today), (day, last))
            with self.assertRaises(ValidationError):
                r.validate_range(day, last + dt.timedelta(days=1), today)
            self.assertGreaterEqual((last - day).days, 150 - 1 - 3)         # between 5*28-1 and 5*31 days
            self.assertLessEqual((last - day).days, 5 * 31 - 1)
            day += dt.timedelta(days=1)

    def test_too_long_is_reported_before_future_only_when_the_end_is_not_future(self):
        self.assertEqual(self.key(D(2026, 1, 1), D(2026, 11, 4)), "REPORT_RANGE_TOO_LONG")
        self.assertEqual(self.key(D(2026, 1, 1), D(2026, 11, 5)), "REPORT_RANGE_FUTURE")

    def test_not_dates(self):
        for bad in ("2026-11-01", None, dt.datetime(2026, 11, 1, 1)):
            with self.assertRaises(ValidationError) as cm:
                r.validate_range(bad, D(2026, 11, 2), self.TODAY)
            self.assertEqual(cm.exception.message_key, "INVALID_DATE")

    def test_add_months_clamps(self):
        self.assertEqual(r.add_months(D(2026, 1, 31), 1), D(2026, 2, 28))
        self.assertEqual(r.add_months(D(2028, 1, 31), 1), D(2028, 2, 29))
        self.assertEqual(r.add_months(D(2026, 11, 15), 3), D(2027, 2, 15))
        self.assertEqual(r.add_months(D(2026, 12, 1), 1), D(2027, 1, 1))


if __name__ == "__main__":
    unittest.main()
