"""baror.domain.reports: which reports are due on a date and which dates they cover (Thursday weekly, month-end monthly)."""
import calendar
import datetime as dt
import unittest

from baror.domain import reports as r

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


if __name__ == "__main__":
    unittest.main()
