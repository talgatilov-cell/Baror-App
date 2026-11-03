"""Aggregated period reports: computation (small groups, complementary disclosure, boundaries, previous-period change,
latest result per cadet, accommodations), storage and the chief-only reads, and the PDF."""
import datetime as dt
import inspect
import re
import unittest

from baror import export_report_pdf, messages_he
from baror.domain.grading import grade_stored_value
from baror.domain.reports import monthly_period, previous_period, weekly_period
from baror.errors import AuthError, NotFoundError
from baror.storage import accommodations as acc
from baror.storage import chief, db, ops, repo, reports
from tests.test_export_pdf import parse
from tests.v2base import V2Case

G = grade_stored_value
D = dt.date
NOW = dt.datetime(2026, 11, 5, 8, 0, tzinfo=dt.timezone.utc)
THU = D(2026, 11, 5)                       # weekly period 2026-10-30 .. 2026-11-05, previous 10-23 .. 10-29
FAKE = lambda data: b"%PDF-fake"            # noqa: E731


class ReportCase(V2Case):
    def setUp(self):
        super().setUp()
        self.boss = self.staff("boss@example.com", "chief", name="Boss")
        self.n = 0

    def cadets(self, platoon, count, results=(), **kw):
        """``count`` cadets in a platoon, each with the same list of (date, run, pushups)."""
        ids = []
        for _ in range(count):
            self.n += 1
            uid = self.cadet(f"c{self.n}@example.com", platoon, name=f"Cadet {self.n}", **kw)
            for d, run, push in results:
                repo.add_result(self.path, uid, test_date=d, run_tenths=run, pushups=push)
            ids.append(uid)
        return ids

    def compute(self, kind="weekly", start=None, end=None, today=THU):
        if start is None:
            start, end = weekly_period(THU)
        with db.reader(self.path) as c:
            return reports.compute(c, kind, start, end, G, today)

    def platoon_row(self, data, name):
        return next(p for p in data["platoons"] if p["platoon"] == name)


class Counting(ReportCase):
    def setUp(self):
        super().setUp()
        self.a, self.b, self.c = self.platoon("A"), self.platoon("B"), self.platoon("C")
        # A: six male cadets; a0 has an earlier, worse result in the same period that must NOT count
        values = [(7000, 40), (8000, 30), (9000, 25), (9500, 10), (9000, 22), (8500, 35)]
        self.a_ids = []
        for run, push in values:
            self.a_ids += self.cadets(self.a, 1, [("2026-11-02", run, push)])
        repo.add_result(self.path, self.a_ids[0], test_date="2026-10-31", run_tenths=9900, pushups=5)
        for uid in self.a_ids:                                           # previous week: everybody 9000 / 20
            repo.add_result(self.path, uid, test_date="2026-10-25", run_tenths=9000, pushups=20)
        self.cadets(self.b, 3, [("2026-11-02", 8000, 30)])               # B: 3 cadets: hidden
        self.c_ids = self.cadets(self.c, 5, [("2026-11-03", 8000, 30)])  # C: 5 now, only 3 in the previous week
        for uid in self.c_ids[:3]:
            repo.add_result(self.path, uid, test_date="2026-10-26", run_tenths=8000, pushups=30)
        self.d = self.compute()

    def test_exact_figures_use_the_latest_result_of_each_cadet_in_the_period(self):
        a = self.platoon_row(self.d, "A")
        self.assertEqual((a["suppressed"], a["cadets"], a["tested"], a["results"]), (False, 6, 6, 7))
        run = a["tests"]["run"]
        self.assertEqual((run["n"], run["avg"], run["best"]), (6, 8500, 7000))
        push = a["tests"]["pushups"]
        self.assertEqual((push["n"], push["avg"], push["best"]), (6, 27.0, 40))
        for blk in (run, push):
            self.assertEqual(blk["excellent"] + blk["pass"] + blk["fail"], blk["n"])
        expected = {lv: 0 for lv in ("excellent", "pass", "fail")}
        for v in (7000, 8000, 9000, 9500, 9000, 8500):
            expected[G("run", "M", "2026-11-02", v)] += 1
        self.assertEqual({lv: run[lv] for lv in expected}, expected)

    def test_change_against_the_previous_period(self):
        a = self.platoon_row(self.d, "A")
        self.assertEqual(a["tests"]["run"]["change"], -500)        # 8500 now, 9000 before: 50 seconds faster
        self.assertEqual(a["tests"]["pushups"]["change"], 7.0)     # 27 now, 20 before
        self.assertEqual(self.d["period"]["previous_start"], "2026-10-23")
        self.assertEqual(self.d["period"]["previous_end"], "2026-10-29")

    def test_change_is_omitted_when_the_previous_period_has_fewer_than_five(self):
        c = self.platoon_row(self.d, "C")
        self.assertEqual(c["tests"]["run"]["n"], 5)
        self.assertIsNone(c["tests"]["run"]["change"])

    def test_change_is_omitted_when_there_is_no_previous_period_data(self):
        d = self.compute("weekly", D(2026, 10, 23), D(2026, 10, 29), today=D(2026, 10, 29))
        a = self.platoon_row(d, "A")
        self.assertEqual(a["tests"]["run"]["n"], 6)
        self.assertIsNone(a["tests"]["run"]["change"])
        self.assertIsNone(d["total"]["tests"]["run"]["change"])

    def test_groups_under_five_show_no_figure_at_all(self):
        b = self.platoon_row(self.d, "B")
        self.assertEqual(b, {"platoon_id": self.b, "platoon": "B", "suppressed": True, "cadets": None, "tested": None,
                             "results": None, "tests": {m: {"suppressed": True} for m in ("run", "walk", "pushups")},
                             "accommodations": {k: {s: None for s in ("run", "pushups", "both")}
                                                for k in ("relief", "exemption")}})
        self.assertNotIn(None, [p["platoon_id"] for p in self.d["platoons"]], "no cadet without a platoon: no such row")

    def test_complementary_disclosure_course_totals_add_up_shown_groups_only(self):
        t = self.d["total"]
        self.assertEqual(t["cadets"], 6 + 5, "B (3 cadets) is not in the total: total - A - C would be B")
        self.assertEqual(t["tested"], 6 + 5)
        self.assertEqual(t["results"], 7 + 5)
        self.assertEqual(t["tests"]["run"]["n"], 11)
        shown = [p for p in self.d["platoons"] if not p["suppressed"]]
        self.assertEqual(sum(p["cadets"] for p in shown), t["cadets"])
        for m in ("run", "pushups"):
            self.assertEqual(sum(p["tests"][m]["n"] for p in shown if not p["tests"][m]["suppressed"]), t["tests"][m]["n"])
            levels = sum(p["tests"][m][lv] for p in shown for lv in ("excellent", "pass", "fail"))
            self.assertEqual(levels, t["tests"][m]["n"])
        self.assertTrue(t["tests"]["walk"]["suppressed"])

    def test_course_change_uses_only_groups_shown_in_both_periods(self):
        self.assertEqual(self.d["total"]["tests"]["run"]["change"], -500, "only A had five in both periods; C is not mixed in")

    def test_no_name_or_id_of_a_cadet_anywhere_in_the_data(self):
        def walk(x, acc_):
            if isinstance(x, dict):
                for k, v in x.items():
                    acc_.add(str(k))
                    walk(v, acc_)
            elif isinstance(x, list):
                for v in x:
                    walk(v, acc_)
            else:
                acc_.add(str(x))
        seen = set()
        walk(self.d, seen)
        self.assertFalse([s for s in seen if "Cadet" in s or "@example.com" in s])
        self.assertEqual(set(self.d), {"kind", "min_group", "period", "empty", "platoons", "total"})

    def test_a_cadet_who_is_deactivated_is_not_counted(self):
        before = self.platoon_row(self.d, "C")["cadets"]
        ops.deactivate_user(self.path, self.a_ids[1])
        d = self.compute()
        self.assertEqual(self.platoon_row(d, "A")["cadets"], 5)
        self.assertEqual(self.platoon_row(d, "C")["cadets"], before)
        self.assertEqual(self.platoon_row(d, "A")["tests"]["run"]["n"], 5)

    def test_boundary_four_hidden_five_shown(self):
        ops.deactivate_user(self.path, self.a_ids[1])
        self.assertFalse(self.platoon_row(self.compute(), "A")["suppressed"])        # five left
        ops.deactivate_user(self.path, self.a_ids[2])
        a = self.platoon_row(self.compute(), "A")
        self.assertTrue(a["suppressed"])                                              # four left: nothing at all
        self.assertIsNone(a["cadets"])

    def test_a_platoon_with_five_cadets_but_four_testers_hides_the_numbers_but_not_the_platoon(self):
        p = self.platoon("E")
        self.cadets(p, 4, [("2026-11-02", 9000, 30)])
        self.cadets(p, 1, [])
        row = self.platoon_row(self.compute(), "E")
        self.assertEqual((row["suppressed"], row["cadets"], row["tested"], row["results"]), (False, 5, None, None))
        self.assertTrue(all(row["tests"][m]["suppressed"] for m in row["tests"]))

    def test_the_minimum_cannot_be_lowered(self):
        with db.reader(self.path) as c, self.assertRaises(ValueError):
            reports.compute(c, "weekly", *weekly_period(THU), G, THU, min_group=1)

    def test_no_overall_verdict_key_exists(self):
        text = repr(self.d).lower()
        for word in ("verdict", "overall", "passed_all", "total_pass"):
            self.assertNotIn(word, text)


class Boundaries(ReportCase):
    """40 cadets in one platoon; five have a result on each of eight dates around the week and month limits."""
    DATES = ["2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-05", "2026-11-06", "2026-11-30", "2026-12-01"]

    def setUp(self):
        super().setUp()
        self.z = self.platoon("Z")
        for d in self.DATES:
            self.cadets(self.z, 5, [(d, 9000, 30)])

    def n_tested(self, kind, start, end, today):
        return self.platoon_row(self.compute(kind, start, end, today), "Z")["tested"]

    def test_week_is_friday_to_thursday_both_ends_included(self):
        # 30 Oct (Fri) .. 5 Nov (Thu): 30 Oct, 31 Oct, 1 Nov, 5 Nov = 20; 29 Oct (Thu before) and 6 Nov (Fri after) are out
        self.assertEqual(self.n_tested("weekly", D(2026, 10, 30), D(2026, 11, 5), THU), 20)
        self.assertEqual(self.n_tested("weekly", D(2026, 11, 6), D(2026, 11, 12), D(2026, 11, 12)), 5)
        self.assertEqual(self.n_tested("weekly", D(2026, 10, 23), D(2026, 10, 29), D(2026, 10, 29)), 5)

    def test_month_is_first_to_generation_date_both_ends_included(self):
        nov = monthly_period(2026, 11)
        self.assertEqual(nov, (D(2026, 11, 1), D(2026, 11, 30)))
        self.assertEqual(self.n_tested("monthly", *nov, D(2026, 11, 30)), 20)      # 1 Nov, 5 Nov, 6 Nov, 30 Nov
        octo = monthly_period(2026, 10)
        self.assertEqual(octo, (D(2026, 10, 1), D(2026, 10, 29)))
        self.assertEqual(self.n_tested("monthly", *octo, D(2026, 10, 29)), 5)        # only 29 Oct: 30 and 31 Oct are in no monthly

    def test_the_two_weekend_days_are_in_the_weekly_report_and_in_no_monthly_one(self):
        weekly = self.platoon_row(self.compute("weekly", D(2026, 10, 30), D(2026, 11, 5)), "Z")["results"]
        self.assertEqual(weekly, 20)
        in_months = sum(self.platoon_row(self.compute("monthly", *monthly_period(2026, m), today=D(2026, 12, 31)), "Z")["results"]
                        or 0 for m in (10, 11))
        self.assertEqual(in_months, 5 + 20, "30 and 31 October (10 results) are in neither monthly report")

    def test_previous_period_of_the_month_is_the_previous_month_with_its_own_end(self):
        d = self.compute("monthly", *monthly_period(2026, 11), today=D(2026, 11, 30))
        self.assertEqual((d["period"]["previous_start"], d["period"]["previous_end"]), ("2026-10-01", "2026-10-29"))


class EmptyAndAccommodations(ReportCase):
    def test_an_empty_period_has_no_figures_and_no_error(self):
        p = self.platoon("A")
        self.cadets(p, 6, [("2026-05-01", 9000, 30)])
        d = self.compute()
        self.assertTrue(d["empty"])
        a = self.platoon_row(d, "A")
        self.assertEqual((a["cadets"], a["tested"], a["results"]), (6, None, None))
        self.assertIsNone(d["total"]["tested"])
        from baror.export_pdf import visual_order
        lines = parse(export_report_pdf.build_report_pdf(d, generated=THU))[1]
        self.assertIn(visual_order(messages_he.t("report_empty")), lines)

    def test_no_data_at_all(self):
        d = self.compute()
        self.assertEqual(d["platoons"], [])
        self.assertTrue(d["empty"])
        self.assertTrue(export_report_pdf.build_report_pdf(d, generated=THU).startswith(b"%PDF"))

    def add_acc(self, cadet, kind, scope, start, end, who=None, today=THU):
        acc.add(self.path, who or self.boss, cadet, kind=kind, scope=scope, start_date=start, end_date=end, now=NOW, today=today)

    def test_accommodation_cells_count_cadets_and_hide_under_five(self):
        a, b = self.platoon("A"), self.platoon("B")
        ia = self.cadets(a, 6, [("2026-11-02", 9000, 30)])
        ib = self.cadets(b, 6, [("2026-11-02", 9000, 30)])
        for uid in ia[:5]:
            self.add_acc(uid, "relief", "run", "2026-10-01", "2026-12-31")
        self.add_acc(ia[5], "exemption", "pushups", "2026-10-01", "2026-12-31")                 # 1 cadet: hidden
        for uid in ib[:3]:
            self.add_acc(uid, "relief", "run", "2026-11-05", "2026-11-30")                      # starts on the last day: counts
        self.add_acc(ib[3], "relief", "run", "2026-06-01", "2026-10-29")                          # ended before the period
        self.add_acc(ib[4], "relief", "run", "2026-11-06", "2026-12-31", today=THU)               # starts after the period
        pending = self.cadet("pend@example.com", b, name="Pend")
        acc.add(self.path, pending, pending, kind="relief", scope="run", start_date="2026-10-01", end_date="2026-12-31",
                now=NOW, today=THU)                                                               # a cadet's own: pending
        d = self.compute()
        ra, rb = self.platoon_row(d, "A")["accommodations"], self.platoon_row(d, "B")["accommodations"]
        self.assertEqual(ra["relief"], {"run": 5, "pushups": None, "both": None})
        self.assertEqual(ra["exemption"]["pushups"], None)
        self.assertEqual(rb["relief"]["run"], None, "3 counted records (pending, expired and future ones do not count)")
        self.assertEqual(d["total"]["accommodations"]["relief"]["run"], 5, "hidden cells are not in the course cell")
        self.assertIsNone(d["total"]["accommodations"]["exemption"]["pushups"])
        for k in ("relief", "exemption"):
            self.assertEqual(set(d["total"]["accommodations"][k]), {"run", "pushups", "both"}, "no row total")

    def test_accommodation_status_matches_the_domain_rule(self):
        from baror.domain import accommodation as rules
        a = self.platoon("A")
        ids = self.cadets(a, 5, [("2026-11-02", 9000, 30)])
        for uid in ids:
            self.add_acc(uid, "relief", "both", "2026-11-05", "2026-11-05")
        with db.reader(self.path) as c:
            rec = {k: v for k, v in dict(c.execute("SELECT * FROM accommodations LIMIT 1").fetchone()).items()}
        self.assertEqual(rules.status(rec, THU), "active")
        self.assertEqual(self.platoon_row(self.compute(), "A")["accommodations"]["relief"]["both"], 5)

    def test_a_period_in_the_future_counts_no_accommodation(self):
        a = self.platoon("A")
        for uid in self.cadets(a, 5, [("2026-11-02", 9000, 30)]):
            self.add_acc(uid, "relief", "run", "2026-12-01", "2026-12-31")
        d = self.compute("weekly", D(2026, 11, 6), D(2026, 11, 12), today=THU)
        self.assertEqual(self.platoon_row(d, "A")["accommodations"]["relief"]["run"], None)


class StoredBase(ReportCase):
    def setUp(self):
        super().setUp()
        self.p = self.platoon("A")
        self.cadets(self.p, 6, [("2026-11-02", 9000, 30)])
        self.deputy = self.staff("deputy@example.com", "chief", name="Deputy")
        self.inactive = self.staff("old@example.com", "chief", name="Old")
        ops.deactivate_user(self.path, self.inactive)

    def make(self, today=THU, now=NOW):
        return reports.generate_due(self.path, today, grade=G, render=FAKE, now=now)


class Stored(StoredBase):

    def test_generate_stores_one_report_and_is_idempotent(self):
        first = self.make()
        self.assertEqual([(r["kind"], r["id"] is not None) for r in first], [("weekly", True)])
        second = self.make()
        self.assertEqual([(r["kind"], r["id"]) for r in second], [("weekly", None)])
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM reports").fetchone()[0], 1)
        self.assertEqual(len(self.audit_rows(action="report.generate")), 1)

    def test_nothing_due_on_a_friday(self):
        self.assertEqual(self.make(D(2026, 11, 6)), [])
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM reports").fetchone()[0], 0)

    def test_thursday_that_is_month_end_makes_both(self):
        made = self.make(D(2026, 4, 30))
        self.assertEqual(sorted(r["kind"] for r in made), ["monthly", "weekly"])

    def test_delivery_rows_exist_for_active_chiefs_only(self):
        self.make()
        with db.reader(self.path) as c:
            users = [r[0] for r in c.execute("SELECT user_id FROM report_deliveries ORDER BY user_id")]
        self.assertEqual(users, sorted([self.boss, self.deputy]))

    def test_a_failing_render_stores_nothing(self):
        def boom(_):
            raise RuntimeError("no")
        with self.assertRaises(RuntimeError):
            reports.generate_due(self.path, THU, grade=G, render=boom, now=NOW)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM reports").fetchone()[0], 0)

    def test_the_stored_pdf_is_the_rendered_bytes(self):
        self.make()
        rid = chief.list_reports(self.path, self.boss)[0]["id"]
        self.assertEqual(chief.get_report(self.path, self.boss, rid)["pdf"], b"%PDF-fake")


class ChiefBase(StoredBase):
    def setUp(self):
        super().setUp()
        self.make()
        self.rid = chief.list_reports(self.path, self.boss)[0]["id"]
        self.ins = self.staff("ins@example.com", "instructor", [self.p], name="Ins")
        self.adm = self.staff("adm@example.com", "admin", [self.p], name="Adm")
        self.cad = self.cadet("plain@example.com", self.p, name="Plain")


class ChiefOnly(ChiefBase):
    def calls(self, actor):
        return [lambda: chief.list_reports(self.path, actor), lambda: chief.get_report(self.path, actor, self.rid),
                lambda: chief.report_mail_state(self.path, actor), lambda: reports.set_mail_enabled(self.path, actor, True)]

    def test_instructor_admin_cadet_and_deactivated_chief_are_refused_everywhere(self):
        for who in (self.ins, self.adm, self.cad, self.inactive):
            for call in self.calls(who):
                with self.assertRaises(AuthError):
                    call()
        self.assertFalse(reports.mail_state(self.path)["enabled"])

    def test_unknown_and_invalid_actors_are_refused_and_write_nothing(self):
        before = len(self.audit_rows())
        for who in (None, 0, -1, 99999, "1", True):
            for call in self.calls(who):
                with self.assertRaises(AuthError):
                    call()
        self.assertEqual(len(self.audit_rows()), before)

    def test_every_refusal_by_an_existing_account_leaves_a_denied_row(self):
        for call in self.calls(self.ins):
            with self.assertRaises(AuthError):
                call()
        denied = sorted(r[1] for r in self.audit_rows(actor_id=self.ins, outcome="denied"))
        self.assertEqual(denied, ["chief.mail_state", "chief.mail_switch", "chief.report", "chief.reports"])

    def test_unknown_report_id_is_not_found_and_denied_is_logged(self):
        for bad in (0, -5, 99999, None, "x", True):
            with self.assertRaises(NotFoundError):
                chief.get_report(self.path, self.boss, bad)
        self.assertEqual(len(self.audit_rows(action="chief.report", outcome="denied")), 6)

    def test_one_audit_row_per_list_and_per_download(self):
        n_list, n_get = len(self.audit_rows(action="chief.reports")), len(self.audit_rows(action="chief.report"))
        chief.list_reports(self.path, self.deputy, "weekly")
        chief.get_report(self.path, self.deputy, self.rid)
        self.assertEqual(len(self.audit_rows(action="chief.reports")), n_list + 1)
        self.assertEqual(len(self.audit_rows(action="chief.report", outcome="ok")), n_get + 1)
        self.assertEqual([(r[0], r[4]) for r in self.audit_rows(action="chief.report", actor_id=self.deputy)], [(self.deputy, "ok")])

    def test_the_state_read_adds_no_audit_row(self):
        before = len(self.audit_rows())
        for _ in range(3):
            chief.report_mail_state(self.path, self.boss)
        self.assertEqual(len(self.audit_rows()), before)

    def test_list_filters_by_type_newest_first_and_rejects_unknown_types(self):
        self.make(D(2026, 11, 12), dt.datetime(2026, 11, 12, 8, tzinfo=dt.timezone.utc))
        rows = chief.list_reports(self.path, self.boss, "weekly")
        self.assertEqual([r["period_end"] for r in rows], ["2026-11-12", "2026-11-05"])
        self.assertEqual(chief.list_reports(self.path, self.boss, "monthly"), [])
        with self.assertRaises(ValueError):
            chief.list_reports(self.path, self.boss, "daily")
        self.assertNotIn("pdf", rows[0])

    def test_module_shape(self):
        pub = sorted(n for n, f in inspect.getmembers(chief, inspect.isfunction)
                     if not n.startswith("_") and f.__module__ == chief.__name__)
        self.assertEqual(pub, ["get_cadet_results", "get_report", "list_platoon_members", "list_reports", "overview",
                               "platoon_summary", "report_mail_state"])
        src = inspect.getsource(chief)
        self.assertFalse(re.findall(r"\b(UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE|REPLACE\s+INTO)\b", src))
        self.assertNotRegex(src, r"(?m)^(from|import)\s+.*\bbaror\.domain")


class Switch(ChiefBase):
    calls = ChiefOnly.calls

    def test_default_is_off_on_a_fresh_database(self):
        self.assertEqual(reports.mail_state(self.path), {"enabled": False, "changed_at": None})
        self.assertEqual(chief.report_mail_state(self.path, self.boss), {"enabled": False, "changed_at": None})

    def test_chief_and_deputy_can_switch_and_it_is_audited_with_who_and_when(self):
        t1 = dt.datetime(2026, 11, 6, 9, 0, tzinfo=dt.timezone.utc)
        st = reports.set_mail_enabled(self.path, self.deputy, True, now=t1)
        self.assertEqual(st, {"enabled": True, "changed_at": "2026-11-06T09:00:00Z"})
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT changed_by FROM report_settings").fetchone()[0], self.deputy)
        self.assertEqual([r[:2] for r in self.audit_rows(action="chief.mail_on")], [(self.deputy, "chief.mail_on")])
        reports.set_mail_enabled(self.path, self.boss, False, now=t1 + dt.timedelta(hours=1))
        self.assertEqual(self.audit_rows(action="chief.mail_off")[0][0], self.boss)
        self.assertFalse(chief.report_mail_state(self.path, self.boss)["enabled"])

    def test_pressing_the_same_value_again_does_not_move_the_cutoff(self):
        t1 = dt.datetime(2026, 11, 6, 9, 0, tzinfo=dt.timezone.utc)
        reports.set_mail_enabled(self.path, self.boss, True, now=t1)
        again = reports.set_mail_enabled(self.path, self.deputy, True, now=t1 + dt.timedelta(days=3))
        self.assertEqual(again["changed_at"], "2026-11-06T09:00:00Z")

    def test_a_non_boolean_is_refused_after_the_check(self):
        for bad in (1, "on", None):
            with self.assertRaises(ValueError):
                reports.set_mail_enabled(self.path, self.boss, bad)
        with self.assertRaises(AuthError):
            reports.set_mail_enabled(self.path, self.ins, "on")           # authorisation first: no input oracle

    def test_a_restore_switches_it_off(self):
        import importlib
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        backup = importlib.import_module("backup")
        reports.set_mail_enabled(self.path, self.boss, True)
        made = backup.backup(Path(self.path), Path(self.dir) / "bk", now=dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc))
        self.assertTrue(reports.mail_state(self.path)["enabled"])
        backup.restore(made, Path(self.path))
        self.assertFalse(reports.mail_state(self.path)["enabled"])


class Pdf(unittest.TestCase):
    """The PDF: valid structure, Hebrew text extractable, hidden groups carry no number, notes present."""

    @classmethod
    def setUpClass(cls):
        import os
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.path = os.path.join(tmp.name, "r.db")
        db.init_db(cls.path)
        pa, pb = ops.create_platoon(cls.path, "מחלקה א"), ops.create_platoon(cls.path, "ב")
        n = [0]

        def cadets(pl, count, results):
            for _ in range(count):
                n[0] += 1
                uid = repo.create_user(cls.path, email=f"q{n[0]}@example.com", name=f"Q{n[0]}", gender="M", is_km=False,
                                       pw_hash="x$hash")
                ops.set_user_platoon(cls.path, uid, pl)
                for d, run, push in results:
                    repo.add_result(cls.path, uid, test_date=d, run_tenths=run, pushups=push)
        cadets(pa, 6, [("2026-11-02", 8000, 30), ("2026-10-25", 9000, 20)])
        cadets(pb, 3, [("2026-11-02", 8000, 30)])
        with db.reader(cls.path) as c:
            cls.data = reports.compute(c, "weekly", *weekly_period(THU), G, THU)
            cls.monthly = reports.compute(c, "monthly", *monthly_period(2026, 11), G, D(2026, 11, 30))
        cls.pdf = export_report_pdf.build_report_pdf(cls.data, generated=THU)
        cls.lines = parse(cls.pdf)[1]

    def test_valid_pdf_with_embedded_font_and_searchable_hebrew(self):
        from baror.export_pdf import visual_order
        objects, lines, pages = parse(self.pdf)             # parse() asserts the xref table, trailer and stream lengths
        self.assertGreaterEqual(pages, 1)
        self.assertTrue(any(b"/FontFile2" in b and b"/Heebo" in b for b in objects.values()))
        self.assertTrue(any(b"/ToUnicode" in b for b in objects.values()))
        self.assertIn(visual_order(messages_he.t("report_pdf_title_weekly")), lines)

    def test_text_extracts_as_drawn_visual_order_and_contains_the_expected_phrases(self):
        from baror.export_pdf import visual_order
        drawn = set(self.lines)
        for expected in (messages_he.t("report_pdf_title_weekly"), messages_he.t("report_pdf_section_course"),
                         messages_he.t("report_section_notes"),
                         messages_he.t("report_platoon_hidden", platoon="ב"),
                         messages_he.t("report_pdf_data_until", date="05/11/2026")):
            if expected:
                self.assertIn(visual_order(expected), drawn, expected)

    def test_hidden_platoon_has_no_number_and_the_course_total_excludes_it(self):
        from baror.export_pdf import visual_order
        drawn = set(self.lines)
        self.assertIn(visual_order(messages_he.t("report_line_cadets", v="6")), drawn)         # course: A only
        self.assertNotIn(visual_order(messages_he.t("report_line_cadets", v="9")), drawn)
        self.assertNotIn(visual_order(messages_he.t("report_line_cadets", v="3")), drawn)
        self.assertNotIn(visual_order(messages_he.t("report_line_tested", v="3")), drawn)

    def test_no_cadet_name_in_the_file(self):
        text = "".join(self.lines)
        self.assertNotRegex(text, r"Q\d")
        self.assertNotIn(b"@example.com", self.pdf)

    def test_every_note_is_in_the_footer_section_and_the_month_report_states_data_until(self):
        from baror.export_pdf import visual_order
        monthly_lines = set(parse(export_report_pdf.build_report_pdf(self.monthly, generated=D(2026, 11, 30)))[1])
        self.assertIn(visual_order(messages_he.t("report_pdf_data_until", date="30/11/2026")), monthly_lines)
        self.assertIn(visual_order(messages_he.t("report_pdf_month", label="נובמבר 2026")), monthly_lines)
        # long notes wrap; check that the first words of each note are drawn
        for key in ("report_note_cohort", "report_note_latest", "report_note_monthly", "report_note_small", "report_note_acc",
                    "report_note_grading"):
            first = messages_he.t(key).split(" ")[0]
            self.assertTrue(any(visual_order(first) in x for x in monthly_lines), key)

    def test_deterministic(self):
        self.assertEqual(self.pdf, export_report_pdf.build_report_pdf(self.data, generated=THU))

    def test_change_text(self):
        f = export_report_pdf._change_text
        self.assertEqual(f("run", None), messages_he.t("report_omitted"))
        self.assertEqual(f("run", -500), messages_he.t("report_change_faster", v="00:50"))
        self.assertEqual(f("run", 120), messages_he.t("report_change_slower", v="00:12"))
        self.assertEqual(f("pushups", 7.0), messages_he.t("report_change_more", v="7.0"))
        self.assertEqual(f("pushups", -1.5), messages_he.t("report_change_less", v="1.5"))
        self.assertEqual(f("walk", 0), messages_he.t("report_change_same"))


if __name__ == "__main__":
    unittest.main()
