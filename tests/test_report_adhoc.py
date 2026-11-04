"""On-demand reports (the last 30 days and a chosen date range): chief only, nothing stored, nothing mailed, one audit row
per generation, the same privacy rules as the stored reports, and the PDF titles and footers of the new kinds."""
import datetime as dt
import unittest

from baror import export_report_pdf, messages_he
from baror.domain import reports as schedule
from baror.errors import AuthError, ValidationError
from baror.export_pdf import visual_order
from baror.storage import db, repo, reports
from tests.test_export_pdf import parse
from tests.test_period_report import FAKE, G, ReportCase

D = dt.date
TODAY = D(2026, 11, 4)
NOW = dt.datetime(2026, 11, 4, 8, 0, tzinfo=dt.timezone.utc)
NO_ACTOR = object()                                    # "use the chief" (None and 0 must stay testable as actors)
TRAILING = schedule.trailing_period(TODAY)            # 2026-10-05 .. 2026-11-04, previous 2026-09-04 .. 2026-10-04


class AdhocBase(ReportCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.platoon("A"), self.platoon("B")
        self.a_ids = self.cadets(self.a, 6, [("2026-10-20", 9000, 30)])
        for uid in self.a_ids:                                          # previous 30 days
            repo.add_result(self.path, uid, test_date="2026-09-20", run_tenths=9500, pushups=20)
        self.cadets(self.b, 3, [("2026-10-20", 8000, 30)])             # B is hidden (3 cadets)
        self.ins = self.staff("ins@example.com", "instructor", [self.a], name="Ins")
        self.adm = self.staff("adm@example.com", "admin", name="Adm")
        self.cad = self.cadet("plain@example.com", self.a, name="Plain")

    def trailing(self, actor=NO_ACTOR, render=FAKE, **kw):
        return reports.adhoc_report(self.path, self.boss if actor is NO_ACTOR else actor, *TRAILING, kind="trailing", grade=G, render=render,
                                    today=TODAY, now=NOW, **kw)

    def ranged(self, start, end, actor=NO_ACTOR, render=FAKE, **kw):
        return reports.adhoc_report(self.path, self.boss if actor is NO_ACTOR else actor, start, end, kind="range", grade=G, render=render,
                                    today=TODAY, now=NOW, **kw)

    def table_counts(self):
        with db.reader(self.path) as c:
            return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    for t in ("reports", "report_deliveries", "results", "users")}


class Generation(AdhocBase):
    def test_returns_the_bytes_the_renderer_made(self):
        self.assertEqual(self.trailing(), b"%PDF-fake")
        self.assertEqual(self.ranged(D(2026, 9, 1), D(2026, 11, 4)), b"%PDF-fake")
        self.assertEqual(self.trailing(render=lambda data: bytearray(b"x")), b"x")
        self.assertEqual(self.trailing(render=lambda data: b""), b"", "an empty file is not 'not found'")

    def test_nothing_is_stored_and_nothing_is_queued_for_mail(self):
        before = self.table_counts()
        self.trailing()
        self.ranged(D(2026, 9, 1), D(2026, 11, 4))
        self.assertEqual(self.table_counts(), before)
        self.assertEqual(before["reports"], 0)
        self.assertEqual(reports.pending_deliveries(self.path, now=NOW), [])

    def test_exactly_one_audit_row_per_generation_with_ids_only(self):
        self.trailing()
        self.assertEqual(self.audit_rows(action="chief.report_range"), [(self.boss, "chief.report_range", None, None, "ok")])
        self.ranged(D(2026, 9, 1), D(2026, 11, 4))
        self.assertEqual(len(self.audit_rows(action="chief.report_range")), 2)
        self.assertEqual(len(self.audit_rows(action="report.generate")), 0)
        self.assertEqual(len(self.audit_rows(action="chief.report")), 0)

    def test_the_render_function_gets_the_same_data_as_compute(self):
        seen = []
        self.trailing(render=lambda d: seen.append(d) or b"x")
        self.assertEqual(seen[0], self.compute("trailing", *TRAILING, today=TODAY))
        self.assertEqual(seen[0]["period"], {"start": "2026-10-05", "end": "2026-11-04",
                                             "previous_start": "2026-09-04", "previous_end": "2026-10-04"})

    def test_range_kind_uses_the_chosen_dates_and_the_previous_period_of_the_same_length(self):
        seen = []
        self.ranged(D(2026, 10, 1), D(2026, 10, 31), render=lambda d: seen.append(d) or b"x")
        self.assertEqual((seen[0]["kind"], seen[0]["period"]["start"], seen[0]["period"]["end"]),
                         ("range", "2026-10-01", "2026-10-31"))
        self.assertEqual((seen[0]["period"]["previous_start"], seen[0]["period"]["previous_end"]),
                         ("2026-08-31", "2026-09-30"))


class Privacy(AdhocBase):
    def data(self, **kw):
        seen = []
        self.trailing(render=lambda d: seen.append(d) or b"x", **kw)
        return seen[0]

    def test_small_groups_are_hidden_and_there_is_no_everyone_total(self):
        d = self.data()
        b = self.platoon_row(d, "B")
        self.assertEqual((b["suppressed"], b["cadets"], b["tested"], b["results"]), (True, None, None, None))
        self.assertEqual(d["total"]["cadets"], 7, "A has six testers and one cadet who did not test; B is not in the total")
        self.assertEqual(d["total"]["tested"], 6)
        a = self.platoon_row(d, "A")
        self.assertEqual((a["tested"], a["tests"]["run"]["n"], a["tests"]["run"]["avg"]), (6, 6, 9000))
        self.assertEqual(a["tests"]["run"]["change"], -500, "the previous 30 days are compared")

    def test_latest_result_per_cadet_in_the_period(self):
        for uid in self.a_ids:
            repo.add_result(self.path, uid, test_date="2026-10-28", run_tenths=8500, pushups=35)
        a = self.platoon_row(self.data(), "A")
        self.assertEqual((a["tests"]["run"]["n"], a["tests"]["run"]["avg"], a["results"]), (6, 8500, 12))

    def test_dates_outside_the_period_do_not_count(self):
        # the trailing period starts on 5 October: a result on 4 October is in the previous period only
        for uid in self.a_ids:
            repo.add_result(self.path, uid, test_date="2026-10-04", run_tenths=5000, pushups=60)
        a = self.platoon_row(self.data(), "A")
        self.assertEqual(a["tests"]["run"]["avg"], 9000)

    def test_the_data_holds_no_name_or_address(self):
        text = repr(self.data())
        self.assertNotIn("Cadet", text)
        self.assertNotIn("@", text)

    def test_the_pdf_has_no_names(self):
        pdf = self.trailing(render=lambda d: export_report_pdf.build_report_pdf(d, generated=TODAY))
        self.assertNotRegex("".join(parse(pdf)[1]), r"Cadet|Plain")
        self.assertNotIn(b"@example.com", pdf)


class Authorisation(AdhocBase):
    def calls(self, actor):
        return [lambda: self.trailing(actor), lambda: self.ranged(D(2026, 9, 1), D(2026, 11, 4), actor)]

    def test_instructor_admin_and_cadet_are_refused_with_a_denied_row_and_nothing_is_rendered(self):
        rendered = []
        for who in (self.ins, self.adm, self.cad):
            for kind_call in (lambda: self.trailing(who, render=lambda d: rendered.append(d) or b"x"),
                              lambda: self.ranged(D(2026, 9, 1), D(2026, 11, 4), who,
                                                  render=lambda d: rendered.append(d) or b"x")):
                with self.assertRaises(AuthError):
                    kind_call()
        self.assertEqual(rendered, [])
        self.assertEqual(len(self.audit_rows(action="chief.report_range", outcome="denied")), 6)
        self.assertEqual(self.audit_rows(action="chief.report_range", outcome="ok"), [])

    def test_unknown_actors_are_refused_and_write_nothing(self):
        before = len(self.audit_rows())
        for who in (0, -1, 99999, "1", True):
            for call in self.calls(who):
                with self.assertRaises(AuthError):
                    call()
        self.assertEqual(len(self.audit_rows()), before)

    def test_a_deactivated_chief_is_refused(self):
        from baror.storage import ops
        old = self.staff("old@example.com", "chief", name="Old")
        ops.deactivate_user(self.path, old)
        for call in self.calls(old):
            with self.assertRaises(AuthError):
                call()
        self.assertEqual(len(self.audit_rows(action="chief.report_range", outcome="denied")), 2)

    def test_a_refusal_does_not_depend_on_the_input(self):
        with self.assertRaises(AuthError):
            self.ranged(D(2030, 1, 1), D(2020, 1, 1), self.ins)       # invalid range, but authorisation comes first


class Validation(AdhocBase):
    def test_bad_ranges_raise_the_message_keys_and_leave_no_row(self):
        before = self.table_counts()
        for start, end, key in ((D(2026, 11, 3), D(2026, 11, 2), "REPORT_RANGE_ORDER"),
                                (D(2026, 11, 1), D(2026, 11, 5), "REPORT_RANGE_FUTURE"),
                                (D(2026, 5, 1), D(2026, 11, 4), "REPORT_RANGE_TOO_LONG")):
            with self.assertRaises(ValidationError) as cm:
                self.ranged(start, end)
            self.assertEqual(cm.exception.message_key, key)
            self.assertIn(key, messages_he.ERRORS)
        self.assertEqual(self.audit_rows(action="chief.report_range"), [])
        self.assertEqual(self.table_counts(), before)

    def test_the_longest_allowed_range_works(self):
        self.assertEqual(self.ranged(D(2026, 6, 5), D(2026, 11, 4)), b"%PDF-fake")
        with self.assertRaises(ValidationError):
            self.ranged(D(2026, 6, 4), D(2026, 11, 4))

    def test_a_trailing_report_covers_exactly_the_last_30_days(self):
        with self.assertRaises(ValueError):
            reports.adhoc_report(self.path, self.boss, D(2026, 10, 1), D(2026, 11, 4), kind="trailing", grade=G,
                                 render=FAKE, today=TODAY, now=NOW)
        self.assertEqual(self.audit_rows(action="chief.report_range"), [])

    def test_unknown_kinds_are_refused_including_the_stored_ones(self):
        for kind in ("weekly", "monthly", "daily", None):
            with self.assertRaises(ValueError):
                reports.adhoc_report(self.path, self.boss, *TRAILING, kind=kind, grade=G, render=FAKE, today=TODAY, now=NOW)
        self.assertEqual(self.table_counts()["reports"], 0)

    def test_a_failing_renderer_leaves_no_audit_row(self):
        def boom(data):
            raise RuntimeError("render")
        with self.assertRaises(RuntimeError):
            self.trailing(render=boom)
        self.assertEqual(self.audit_rows(action="chief.report_range"), [])

    def test_the_stored_kinds_still_work_and_are_not_changed(self):
        rid = reports.generate(self.path, "weekly", *schedule.weekly_period(D(2026, 11, 5)), grade=G, render=FAKE,
                               today=D(2026, 11, 5), now=NOW)
        self.assertIsNotNone(rid)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT period_type FROM reports").fetchall()[0][0], "weekly")
        for kind in ("trailing", "range"):
            with self.assertRaises(ValueError):
                reports.generate(self.path, kind, *TRAILING, grade=G, render=FAKE, today=TODAY, now=NOW)


class Pdf(AdhocBase):
    def lines(self, kind="trailing"):
        seen = []
        if kind == "trailing":
            self.trailing(render=lambda d: seen.append(d) or b"x")
        else:
            self.ranged(D(2026, 9, 1), D(2026, 11, 4), render=lambda d: seen.append(d) or b"x")
        pdf = export_report_pdf.build_report_pdf(seen[0], generated=TODAY)
        return set(parse(pdf)[1]), pdf

    def test_titles_and_data_until_footer_for_both_kinds(self):
        for kind in ("trailing", "range"):
            lines, _ = self.lines(kind)
            self.assertIn(visual_order(messages_he.t("report_pdf_title_" + kind)), lines, kind)
            self.assertIn(visual_order(messages_he.t("report_pdf_data_until", date="04/11/2026")), lines, kind)
            self.assertIn(visual_order(messages_he.t("report_pdf_section_course")), lines)
            self.assertNotIn(visual_order(messages_he.t("report_pdf_title_weekly")), lines)

    def test_period_line_and_notes(self):
        lines, _ = self.lines("trailing")
        self.assertIn(visual_order(messages_he.t("report_pdf_period", start="05/10/2026", end="04/11/2026")), lines)
        self.assertIn(visual_order(messages_he.t("report_platoon_hidden", platoon="B")), lines)
        for key in ("report_note_trailing", "report_adhoc_note", "report_note_small", "report_note_grading"):
            first = messages_he.t(key).split(" ")[0]
            self.assertTrue(any(visual_order(first) in x for x in lines), key)

    def test_the_stored_kinds_do_not_get_the_on_demand_note(self):
        d = self.compute("weekly")
        lines = parse(export_report_pdf.build_report_pdf(d, generated=D(2026, 11, 5)))[1]
        self.assertFalse(any(visual_order("בקשה") in x for x in lines))

    def test_deterministic_and_valid(self):
        _, a = self.lines("range")
        _, b = self.lines("range")
        self.assertEqual(a, b)
        objects, _, pages = parse(a)
        self.assertGreaterEqual(pages, 1)

    def test_unknown_kind_is_refused(self):
        d = self.compute("weekly")
        with self.assertRaises(ValueError):
            export_report_pdf.build_report_pdf({**d, "kind": "daily"}, generated=TODAY)


class Messages(unittest.TestCase):
    def test_every_new_key_exists(self):
        for key in ("report_type_trailing", "report_type_range", "report_title_trailing", "report_title_range",
                    "report_row_trailing", "report_row_range", "report_range_start", "report_range_end",
                    "report_range_help", "report_range_btn_make", "report_range_ready", "report_adhoc_note",
                    "report_pdf_title_trailing", "report_pdf_title_range", "report_note_trailing", "report_note_range"):
            self.assertTrue(messages_he.t(key, start="01/01/2026", end="02/01/2026"), key)
        for key in ("REPORT_RANGE_ORDER", "REPORT_RANGE_FUTURE", "REPORT_RANGE_TOO_LONG"):
            m = messages_he.ERRORS[key]
            self.assertTrue(m.title and m.why and m.action, key)


if __name__ == "__main__":
    unittest.main()
