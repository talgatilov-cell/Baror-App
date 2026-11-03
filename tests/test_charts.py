"""T6: the pure chart code (no Streamlit, no browser): data selection, thresholds per sex and track, axis direction,
mm:ss ticks, 0/1/2/12 points, the summary sentence, the table, and the contrast of every colour the chart adds."""
import datetime as dt
import unittest

from baror.ui import charts
from baror.ui.charts import AERO, PUSHUPS, Point


def rows(*items):
    """items = (iso date, run_tenths, pushups), oldest first -> list as repo.list_results returns it (newest first)."""
    return [{"id": i + 1, "test_date": d, "run_tenths": r, "pushups": p} for i, (d, r, p) in enumerate(items)][::-1]


def pts(n, kind=AERO, start=dt.date(2026, 9, 1)):
    base = 9500 if kind == AERO else 20
    return [Point(start + dt.timedelta(days=7 * i), base - i * 40 if kind == AERO else base + i) for i in range(n)]


def lines(fig):
    return [s for s in fig.layout.shapes if s.type == "line"]


def rects(fig):
    return [s for s in fig.layout.shapes if s.type == "rect"]


def luminance(h):
    r, g, b = (int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class Data(unittest.TestCase):
    def test_points_are_sorted_by_date_and_skip_missing_values(self):
        r = rows(("2026-10-08", 9000, None), ("2026-10-01", None, 20), ("2026-10-15", 8800, 25))
        r = sorted(r, key=lambda x: x["test_date"], reverse=True)
        self.assertEqual([(p.day.isoformat(), p.value) for p in charts.points(r, AERO)], [("2026-10-08", 9000), ("2026-10-15", 8800)])
        self.assertEqual([(p.day.isoformat(), p.value) for p in charts.points(r, PUSHUPS)], [("2026-10-01", 20), ("2026-10-15", 25)])

    def test_no_rows_no_points(self):
        self.assertEqual((charts.points([], AERO), charts.points([], PUSHUPS)), ([], []))

    def test_limits_per_sex_and_track(self):
        on = dt.date(2026, 10, 1)
        self.assertEqual(charts.limits(AERO, "M", False, on), (940.0, 750.0))
        self.assertEqual(charts.limits(AERO, "F", False, on), (1180.0, 880.0))
        self.assertEqual(charts.limits(PUSHUPS, "M", False, on), (22, 36))
        self.assertEqual(charts.limits(PUSHUPS, "F", False, on), (11, 30))
        self.assertEqual(charts.limits(AERO, "F", True, on), (3960.0, None), "the walk track has no excellent level")
        self.assertIsNone(charts.limits(AERO, "?", False, on), "unknown gender: no levels, never a guessed row")


class Figure(unittest.TestCase):
    def test_run_axis_is_reversed_so_faster_is_higher(self):
        fig = charts.build_figure(pts(3), AERO, charts.limits(AERO, "M", False, dt.date(2026, 10, 1)))
        lo, hi = fig.layout.yaxis.range
        self.assertGreater(lo, hi, "range[0] is the BOTTOM: the larger (slower) time is at the bottom")

    def test_pushups_axis_is_natural_and_starts_at_or_above_zero(self):
        fig = charts.build_figure(pts(3, PUSHUPS), PUSHUPS, charts.limits(PUSHUPS, "M", False, dt.date(2026, 10, 1)))
        lo, hi = fig.layout.yaxis.range
        self.assertLess(lo, hi)
        self.assertGreaterEqual(lo, 0)

    def test_run_ticks_are_mm_ss_and_cover_the_levels(self):
        fig = charts.build_figure(pts(3), AERO, (940.0, 750.0))
        vals, text = list(fig.layout.yaxis.tickvals), list(fig.layout.yaxis.ticktext)
        self.assertEqual(text, [charts.fmt_seconds(v) for v in vals])
        for label in text:
            self.assertRegex(label, r"^\d{2}:\d{2}$")
        self.assertLessEqual(len(vals), 8)
        self.assertGreaterEqual(max(vals), 940)
        self.assertLessEqual(min(vals), 750)

    def test_levels_drawn_for_a_run_pass_dashed_excellent_dotted_and_a_band(self):
        fig = charts.build_figure(pts(3), AERO, (940.0, 750.0))
        by_dash = {s.line.dash: s.y0 for s in lines(fig)}
        self.assertEqual(by_dash, {"dash": 940.0, "dot": 750.0})
        self.assertEqual(len(rects(fig)), 1)
        texts = " ".join(a.text for a in fig.layout.annotations)
        self.assertIn("רף מעבר 15:40", texts)
        self.assertIn("רף הצטיינות 12:30", texts)

    def test_walk_track_has_only_the_pass_line_and_no_band(self):
        fig = charts.build_figure(pts(3), AERO, (3960.0, None))
        self.assertEqual([s.line.dash for s in lines(fig)], ["dash"])
        self.assertEqual(rects(fig), [])
        self.assertIn("66:00", fig.layout.annotations[0].text)

    def test_no_rule_means_no_levels_but_still_a_chart(self):
        fig = charts.build_figure(pts(2), AERO, None)
        self.assertEqual((lines(fig), rects(fig), len(fig.data)), ([], [], 1))

    def test_pushup_band_is_above_the_excellent_line(self):
        fig = charts.build_figure(pts(3, PUSHUPS), PUSHUPS, (22, 36))
        band = rects(fig)[0]
        self.assertEqual(band.y0, 36)
        self.assertGreaterEqual(band.y1, 36)

    def test_modebar_and_zoom_are_off(self):
        self.assertFalse(charts.PLOTLY_CONFIG["displayModeBar"])
        self.assertFalse(charts.PLOTLY_CONFIG["scrollZoom"])
        fig = charts.build_figure(pts(3), AERO, (940.0, 750.0))
        self.assertTrue(fig.layout.xaxis.fixedrange and fig.layout.yaxis.fixedrange)

    def test_markers_best_is_a_diamond_latest_is_the_largest_circle(self):
        p = [Point(dt.date(2026, 9, 1), 9500), Point(dt.date(2026, 9, 8), 9000), Point(dt.date(2026, 9, 15), 9300)]
        tr = charts.build_figure(p, AERO, (940.0, 750.0)).data[0]
        self.assertEqual(list(tr.marker.symbol), ["circle", "diamond", "circle"])
        self.assertEqual(max(tr.marker.size), tr.marker.size[2])
        self.assertEqual(list(tr.text), ["", "שיא", ""])

    def test_best_and_latest_can_be_the_same_point(self):
        tr = charts.build_figure(pts(4), AERO, (940.0, 750.0)).data[0]      # times fall: the last is the best
        self.assertEqual(tr.marker.symbol[-1], "diamond")
        self.assertEqual(max(tr.marker.size), tr.marker.size[-1])

    def test_ties_resolve_to_the_earliest_best(self):
        p = [Point(dt.date(2026, 9, 1), 9000), Point(dt.date(2026, 9, 8), 9000)]
        self.assertEqual(charts.best_index(AERO, p), 0)


class PointCounts(unittest.TestCase):
    def test_one_two_and_twelve_points(self):
        for n in (1, 2, 12):
            fig = charts.build_figure(pts(n), AERO, (940.0, 750.0))
            self.assertEqual(len(fig.data[0].x), n)
            x0, x1 = fig.layout.xaxis.range
            for d in fig.data[0].x:
                self.assertLess(x0, d)
                self.assertLess(d, x1)
            self.assertLessEqual(len(fig.layout.xaxis.tickvals), charts.MAX_X_TICKS)
            self.assertEqual(len(fig.layout.xaxis.tickvals), len(fig.layout.xaxis.ticktext))

    def test_a_single_point_is_centred_in_a_padded_range(self):
        fig = charts.build_figure(pts(1), AERO, (940.0, 750.0))
        (x0, x1), d = fig.layout.xaxis.range, fig.data[0].x[0]
        self.assertEqual((d - x0, x1 - d), (dt.timedelta(days=5), dt.timedelta(days=5)))
        self.assertEqual(list(fig.data[0].text), [""], "no 'best' label when there is nothing to compare")

    def test_dates_ticks_are_dd_mm_on_real_test_dates_first_and_last_kept(self):
        p = pts(12)
        fig = charts.build_figure(p, AERO, (940.0, 750.0))
        ticks = list(fig.layout.xaxis.tickvals)
        self.assertEqual((ticks[0], ticks[-1]), (p[0].day, p[-1].day))
        self.assertTrue(set(ticks) <= {x.day for x in p})
        self.assertEqual(list(fig.layout.xaxis.ticktext), [d.strftime("%d/%m") for d in ticks])

    def test_twelve_pushup_points(self):
        fig = charts.build_figure(pts(12, PUSHUPS), PUSHUPS, (22, 36))
        self.assertEqual(len(fig.data[0].x), 12)

    def test_two_results_on_the_same_day_do_not_duplicate_ticks(self):
        p = [Point(dt.date(2026, 9, 1), 9500), Point(dt.date(2026, 9, 1), 9400), Point(dt.date(2026, 9, 8), 9300)]
        fig = charts.build_figure(p, AERO, (940.0, 750.0))
        self.assertEqual(len(set(fig.layout.xaxis.tickvals)), len(fig.layout.xaxis.tickvals))


class Text(unittest.TestCase):
    def test_summary_sentences(self):
        p = [Point(dt.date(2026, 9, 1), 9500), Point(dt.date(2026, 9, 8), 9000), Point(dt.date(2026, 9, 15), 9300)]
        self.assertEqual(charts.summary_text(p, AERO), "ב-3 בחנים: מ-15:50 ל-15:30. השיא האישי: 15:00.")
        self.assertEqual(charts.summary_text(p[:1], AERO), "בבוחן אחד: 15:50.")
        q = pts(3, PUSHUPS)
        self.assertEqual(charts.summary_text(q, PUSHUPS), "ב-3 בחנים: מ-20 ל-22 חזרות. השיא האישי: 22.")
        self.assertEqual(charts.summary_text(q[:1], PUSHUPS), "בבוחן אחד: 20 חזרות.")

    def test_table_is_newest_first_with_a_status_word_for_every_row(self):
        p = [Point(dt.date(2026, 9, 1), 9500), Point(dt.date(2026, 9, 8), 7400)]
        t = charts.table_rows(p, AERO, "M", False)
        self.assertEqual([r["תאריך"] for r in t], ["08/09/2026", "01/09/2026"])
        self.assertEqual([r["זמן ריצה"] for r in t], ["12:20", "15:50"])
        self.assertEqual([r["סטטוס"] for r in t], ["הצטיינות", "טרם הושג הרף"])
        w = charts.table_rows([Point(dt.date(2026, 9, 1), 39000)], AERO, "F", True)
        self.assertIn("זמן הליכה", w[0])
        self.assertEqual(w[0]["סטטוס"], "הושג הרף")


class Contrast(unittest.TestCase):
    """Every colour the chart adds (WCAG relative luminance). Lines and markers need 3:1, text 4.5:1."""
    def test_pairs(self):
        W = charts.PLOT_BG
        for name, fg, bg, need in (("data line", charts.LINE, W, 3), ("best marker", charts.BEST, W, 3),
                                   ("pass line", charts.PASS_LINE, W, 3), ("excellent line", charts.EXC_LINE, W, 3),
                                   ("axis line", charts.AXIS, W, 3), ("tick text", charts.INK, W, 4.5),
                                   ("best label", charts.BEST, W, 4.5), ("pass label", charts.PASS_LINE, W, 4.5),
                                   ("excellent label", charts.EXC_LINE, W, 4.5)):
            self.assertGreaterEqual(ratio(fg, bg), need, name)

    def test_excellent_label_on_the_band(self):
        # band rgba(46,107,52,.16) over white = this opaque colour; the label has its own white background anyway
        r, g, b = (round(255 - (255 - c) * 0.16) for c in (46, 107, 52))
        self.assertGreaterEqual(ratio(charts.EXC_LINE, f"#{r:02x}{g:02x}{b:02x}"), 4.5)


if __name__ == "__main__":
    unittest.main()
