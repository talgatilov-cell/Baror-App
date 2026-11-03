"""ADR-0001: the chief's all-platoons view. Abuse-case matrix in the class names; every denial is also checked for
what it writes to the audit log."""
import datetime as dt
import inspect
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from baror.auth import service
from baror.domain.grading import grade_stored_value
from baror.errors import AuthError, NotFoundError, ValidationError
from baror.storage import admin, allowlist, chief, cohort, db, ops, repo, retention
from tests.v2base import V2Case

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 11, 3, 8, 0, tzinfo=dt.timezone.utc)
G = grade_stored_value


class ChiefCase(V2Case):
    """P4: 4 cadets. P5: 5 male cadets. P6: 6 (F,F,F,M,M and a walk track M). Unassigned: 2.

    Latest results (older ones are worse/better on purpose, to prove 'latest only'):
      P5 run (tenths): 7000 exc, 8000 pass, 9000 pass, 9500 fail, none      -> 4 contributors: SUPPRESSED
      P5 push-ups:     40 exc, 30 pass, 25 pass, 10 fail, 22 pass           -> n=5: 1 exc, 3 pass, 1 fail
      P6 run (non-walk, n=5): F 8000 exc, F 11000 pass, F 12000 fail (older 7000), M 7000 exc, M 9000 pass
      P6 walk (n=1): M 39000 pass                                         -> SUPPRESSED
      P6 push-ups:  F 35 exc, F 12 pass, F 5 fail, M 40 exc, M 20 fail, M(walk) 22 pass -> 2/2/2
    """

    def setUp(self):
        super().setUp()
        self.p4, self.p5, self.p6 = self.platoon("P4"), self.platoon("P5"), self.platoon("P6")
        self.boss = self.staff("chief.zq@example.com", "chief", name="Chief Zq")
        self.admin = self.staff("admin.zq@example.com", "admin", name="Admin Zq")
        self.ins = self.staff("ins.zq@example.com", "instructor", [self.p5], name="Ins Zq")
        self.cad = {}

        def add(key, platoon, gender="M", km=False, results=()):
            uid = self.user(f"{key}@example.com", name=f"Nm-{key}", gender=gender, is_km=km)
            if platoon:
                ops.set_user_platoon(self.path, uid, platoon)
            for d, run, push in results:
                repo.add_result(self.path, uid, test_date=d, run_tenths=run, pushups=push)
            self.cad[key] = uid
        for i in range(4):
            add(f"p4-{i}", self.p4, results=[("2026-05-01", 9000, 30)])
        for i, (run, push) in enumerate([(7000, 40), (8000, 30), (9000, 25), (9500, 10), (None, 22)]):
            add(f"p5-{i}", self.p5, results=[("2026-05-01", run, push)])
        add("p6-0", self.p6, "F", results=[("2026-04-01", 8000, 35)])
        add("p6-1", self.p6, "F", results=[("2026-04-01", 11000, 12)])
        add("p6-2", self.p6, "F", results=[("2026-03-01", 7000, 5), ("2026-04-01", 12000, None)])
        add("p6-3", self.p6, "M", results=[("2026-04-01", 7000, 40)])
        add("p6-4", self.p6, "M", results=[("2026-04-01", 9000, 20)])
        add("p6-5", self.p6, "M", km=True, results=[("2026-04-01", 39000, 22)])
        add("un-0", None, results=[("2026-05-05", 9000, 30)])
        add("un-1", None, results=[("2026-05-05", 9000, 30)])

    def overview(self, **kw):
        return chief.overview(self.path, self.boss, G, now=NOW, **kw)

    def rows(self, **where):
        return self.audit_rows(**where)


def walk_keys(x, acc=None):
    acc = set() if acc is None else acc
    if isinstance(x, dict):
        for k, v in x.items():
            acc.add(k)
            walk_keys(v, acc)
    elif isinstance(x, list):
        for v in x:
            walk_keys(v, acc)
    return acc


class Aggregates(ChiefCase):
    def by_name(self, ov):
        return {p["platoon"]: p for p in ov["platoons"]}

    def test_exact_figures_latest_result_per_cadet_only(self):
        p = self.by_name(self.overview())
        self.assertEqual(p["P5"]["pushups"], {"suppressed": False, "n": 5, "excellent": 1, "pass": 3, "fail": 1})
        self.assertEqual(p["P6"]["run"], {"suppressed": False, "n": 5, "excellent": 2, "pass": 2, "fail": 1})
        self.assertEqual(p["P6"]["pushups"], {"suppressed": False, "n": 6, "excellent": 2, "pass": 2, "fail": 2})
        self.assertEqual((p["P5"]["cadets"], p["P6"]["cadets"]), (5, 6))

    def test_groups_under_five_show_no_figure_at_all(self):
        ov = self.overview()
        p = self.by_name(ov)
        self.assertEqual(p["P4"], {"platoon_id": self.p4, "platoon": "P4", "suppressed": True, "cadets": None,
                                   "run": {"suppressed": True}, "walk": {"suppressed": True}, "pushups": {"suppressed": True}})
        un = [x for x in ov["platoons"] if x["platoon_id"] is None][0]
        self.assertEqual((un["suppressed"], un["cadets"], un["platoon"]), (True, None, None))
        self.assertEqual(p["P5"]["run"], {"suppressed": True}, "4 contributors: that metric is hidden although the platoon is shown")
        self.assertEqual(p["P6"]["walk"], {"suppressed": True})

    def test_totals_add_up_shown_blocks_only_so_nothing_can_be_got_by_subtraction(self):
        ov = self.overview()
        self.assertEqual(ov["total"]["run"], {"suppressed": False, "n": 5, "excellent": 2, "pass": 2, "fail": 1},
                         "P4 (4) and P5 (4 contributors) are hidden and therefore NOT in the total")
        self.assertEqual(ov["total"]["pushups"], {"suppressed": False, "n": 11, "excellent": 3, "pass": 5, "fail": 3})
        self.assertEqual(ov["total"]["walk"], {"suppressed": True})
        for m in ("run", "pushups"):
            shown = sum(p[m]["n"] for p in ov["platoons"] if not p[m]["suppressed"])
            self.assertEqual(ov["total"][m]["n"], shown)

    def test_boundary_four_hidden_five_shown(self):
        self.assertTrue(self.by_name(self.overview())["P4"]["suppressed"])
        extra = self.user("p4-extra@example.com", name="Nm-p4-extra")
        ops.set_user_platoon(self.path, extra, self.p4)
        repo.add_result(self.path, extra, test_date="2026-05-01", run_tenths=9000, pushups=30)
        shown = self.by_name(self.overview())["P4"]
        self.assertEqual((shown["suppressed"], shown["cadets"], shown["run"]["n"]), (False, 5, 5))
        ops.deactivate_user(self.path, extra)
        self.assertTrue(self.by_name(self.overview())["P4"]["suppressed"], "a deactivated cadet no longer counts")

    def test_output_has_no_names_ids_emails_or_raw_values(self):
        ov = self.overview()
        text = json.dumps(ov, ensure_ascii=False)
        for key in self.cad:
            self.assertNotIn(f"Nm-{key}", text)
            self.assertNotIn(f"{key}@example.com", text)
        self.assertEqual(walk_keys(ov) - {"min_group", "platoons", "total", "platoon_id", "platoon", "suppressed", "cadets",
                                          "run", "walk", "pushups", "n", "excellent", "pass", "fail"}, set())

    def test_the_grader_only_ever_sees_gender_date_and_value(self):
        seen = []

        def spy(metric, gender, test_date, value):
            seen.append((metric, gender, test_date, value))
            return G(metric, gender, test_date, value)
        chief.overview(self.path, self.boss, spy, now=NOW)
        self.assertTrue(seen)
        for metric, gender, test_date, value in seen:
            self.assertIn(metric, ("run", "walk", "pushups"))
            self.assertIn(gender, ("M", "F"))
            self.assertRegex(test_date, r"^\d{4}-\d\d-\d\d$")
            self.assertIsInstance(value, int)

    def test_ungradable_values_are_not_counted_and_a_misbehaving_grader_cannot_widen_a_group(self):
        ov = chief.overview(self.path, self.boss, lambda *a: None, now=NOW)
        self.assertTrue(all(p["run"]["suppressed"] and p["pushups"]["suppressed"] for p in ov["platoons"]))
        ov = chief.overview(self.path, self.boss, lambda *a: "banana", now=NOW)
        self.assertTrue(all(p["run"]["suppressed"] for p in ov["platoons"]))

    def test_a_caller_cannot_lower_the_minimum_group_size(self):
        for bad in (0, 1, 4, True, -5, None, "5"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                chief.overview(self.path, self.boss, G, min_group=bad, now=NOW)
        self.assertTrue(self.by_name(self.overview(min_group=7))["P5"]["suppressed"], "raising it is allowed")

    def test_staff_results_and_deactivated_cadets_are_not_counted(self):
        repo.add_result(self.path, self.ins, test_date="2026-05-01", run_tenths=7000, pushups=40)
        repo.add_result(self.path, self.boss, test_date="2026-05-01", run_tenths=7000, pushups=40)
        ops.deactivate_user(self.path, self.cad["p5-0"])
        p = self.by_name(self.overview())["P5"]
        self.assertEqual((p["suppressed"], p["cadets"]), (True, None), "5 -> 4 cadets after one is deactivated")

    def test_deidentified_cadets_keep_contributing_without_names(self):
        retention.set_course_end(self.path, self.p5, "2026-01-31")
        before = self.by_name(self.overview())["P5"]
        retention.run(self.path, today=dt.date(2026, 7, 31))
        after = self.by_name(self.overview())["P5"]
        self.assertEqual(after, before)
        self.assertEqual(after["cadets"], 5)
        with self.assertRaises(NotFoundError):
            chief.get_cadet_results(self.path, self.boss, self.cad["p5-0"], now=NOW)

    def test_overview_is_audited_once_with_no_target(self):
        self.overview()
        self.assertEqual(self.rows(action="chief.overview"), [(self.boss, "chief.overview", None, None, "ok")])

    def test_walk_track_cadets_are_counted_in_walk_not_run(self):
        self.assertEqual(self.by_name(self.overview())["P6"]["run"]["n"], 5, "the 6th cadet is on the walk track")


class WhoMayCall(ChiefCase):
    def calls(self, actor):
        return [lambda: chief.overview(self.path, actor, G, now=NOW),
                lambda: chief.list_platoon_members(self.path, actor, self.p5, now=NOW),
                lambda: chief.get_cadet_results(self.path, actor, self.cad["p5-0"], now=NOW)]

    def test_everyone_but_an_active_chief_is_refused_and_nothing_is_returned(self):
        deactivated = self.staff("old.chief@example.com", "chief")
        ops.deactivate_user(self.path, deactivated)
        for who, actor in [("cadet", self.cad["p5-0"]), ("instructor with a grant", self.ins), ("admin", self.admin),
                           ("deactivated chief", deactivated)]:
            for call in self.calls(actor):
                with self.assertRaises(AuthError, msg=who) as cm:
                    call()
                self.assertEqual(cm.exception.message_key, "NOT_AUTHORISED")

    def test_malformed_and_unknown_actors_are_refused_and_write_no_audit_row(self):
        n = len(self.rows())
        for bad in (None, 0, -1, True, False, "1", 1.0, 2**70, 99999, [], {}, b"1"):
            for call in self.calls(bad):
                with self.assertRaises(AuthError, msg=repr(bad)):
                    call()
        self.assertEqual(len(self.rows()), n, "strangers cannot fill the audit log")

    def test_refused_existing_accounts_leave_denied_rows_with_ids_only(self):
        for call in self.calls(self.ins):
            with self.assertRaises(AuthError):
                call()
        denied = self.rows(outcome="denied")
        self.assertEqual(sorted(r[1] for r in denied), ["chief.cadet", "chief.members", "chief.overview"])
        self.assertTrue(all(r[0] == self.ins for r in denied))
        self.assertEqual(self.rows(outcome="ok", action="chief.cadet"), [])

    def test_role_change_takes_effect_on_the_very_next_call(self):
        chief.overview(self.path, self.boss, G, now=NOW)
        admin.set_role(self.path, self.admin, self.boss, "cadet")
        with self.assertRaises(AuthError):
            chief.overview(self.path, self.boss, G, now=NOW)
        ops.set_role(self.path, self.boss, "chief")
        chief.overview(self.path, self.boss, G, now=NOW)
        ops.deactivate_user(self.path, self.boss)
        with self.assertRaises(AuthError):
            chief.overview(self.path, self.boss, G, now=NOW)

    def test_a_chief_has_no_instructor_rights_and_an_instructor_no_chief_rights(self):
        for fn in (lambda: cohort.cohort_summary(self.path, self.boss, self.p5),
                   lambda: cohort.list_platoon_members(self.path, self.boss, self.p5),
                   lambda: cohort.get_cadet_results(self.path, self.boss, self.cad["p5-0"])):
            with self.assertRaises(AuthError):
                fn()
        # the instructor still reads own platoon only (full matrix: tests/test_cohort.py)
        self.assertEqual(len(cohort.cohort_summary(self.path, self.ins, self.p5)), 5)
        with self.assertRaises(NotFoundError):
            cohort.cohort_summary(self.path, self.ins, self.p6)

    def test_a_chief_cannot_call_admin_functions(self):
        for fn in (lambda: admin.list_accounts(self.path, self.boss),
                   lambda: admin.set_role(self.path, self.boss, self.cad["p5-0"], "chief"),
                   lambda: admin.add_allowed_email(self.path, self.boss, "x@example.com")):
            with self.assertRaises(AuthError):
                fn()


class DrillDown(ChiefCase):
    def test_returns_name_and_results_of_a_cadet_in_any_platoon_even_a_hidden_small_one(self):
        r = chief.get_cadet_results(self.path, self.boss, self.cad["p4-1"], now=NOW)
        self.assertEqual(r["cadet"], {"id": self.cad["p4-1"], "name": "Nm-p4-1", "gender": "M", "is_km": 0,
                                      "platoon_id": self.p4, "platoon": "P4"})
        self.assertEqual(r["results"], [{"test_date": "2026-05-01", "run_tenths": 9000, "pushups": 30}])
        un = chief.get_cadet_results(self.path, self.boss, self.cad["un-0"], now=NOW)
        self.assertIsNone(un["cadet"]["platoon"])

    def test_no_email_or_hash_leaves_the_module(self):
        r = chief.get_cadet_results(self.path, self.boss, self.cad["p5-0"], now=NOW)
        self.assertEqual(set(r["cadet"]), {"id", "name", "gender", "is_km", "platoon_id", "platoon"})
        self.assertNotIn("example.com", json.dumps(r))

    def test_each_drill_down_writes_exactly_one_audit_row_with_actor_target_and_platoon(self):
        chief.get_cadet_results(self.path, self.boss, self.cad["p5-0"], now=NOW)
        self.assertEqual(self.rows(action="chief.cadet"), [(self.boss, "chief.cadet", self.cad["p5-0"], self.p5, "ok")])
        chief.get_cadet_results(self.path, self.boss, self.cad["p5-0"], now=NOW)
        self.assertEqual(len(self.rows(action="chief.cadet")), 2, "every call is logged, not just the first")

    def test_audit_row_and_data_commit_together_or_not_at_all(self):
        with mock.patch.object(chief.audit, "record", side_effect=RuntimeError("log down")):
            with self.assertRaises(RuntimeError):
                chief.get_cadet_results(self.path, self.boss, self.cad["p5-0"], now=NOW)
        self.assertEqual(self.rows(action="chief.cadet"), [])

    def test_targets_that_cannot_be_shown_all_look_the_same(self):
        ops.deactivate_user(self.path, self.cad["p5-1"])
        outcomes = set()
        for target in (self.cad["p5-1"], self.ins, self.admin, self.boss, 99999, 0, -3, None, True, "2", 2**70):
            with self.assertRaises(NotFoundError, msg=repr(target)) as cm:
                chief.get_cadet_results(self.path, self.boss, target, now=NOW)
            outcomes.add((cm.exception.message_key, str(cm.exception.params)))
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(self.rows(action="chief.cadet", outcome="ok"), [])
        self.assertTrue(self.rows(action="chief.cadet", outcome="denied"))

    def test_denied_probe_of_a_nonexistent_id_stores_null_not_the_id(self):
        with self.assertRaises(NotFoundError):
            chief.get_cadet_results(self.path, self.boss, 424242, now=NOW)
        self.assertEqual(self.rows(outcome="denied"), [(self.boss, "chief.cadet", None, None, "denied")])

    def test_member_list_by_platoon_and_unassigned_and_unknown(self):
        names = [m["name"] for m in chief.list_platoon_members(self.path, self.boss, self.p5, now=NOW)]
        self.assertEqual(names, [f"Nm-p5-{i}" for i in range(5)])
        self.assertEqual([m["name"] for m in chief.list_platoon_members(self.path, self.boss, None, now=NOW)],
                         ["Nm-un-0", "Nm-un-1"])
        self.assertEqual(set(chief.list_platoon_members(self.path, self.boss, self.p5, now=NOW)[0]),
                         {"id", "name", "gender", "is_km"})
        for bad in (9999, 0, -1, True, "1"):
            with self.assertRaises(NotFoundError, msg=repr(bad)):
                chief.list_platoon_members(self.path, self.boss, bad, now=NOW)
        self.assertEqual(len(self.rows(action="chief.members", outcome="ok")), 3)

    def test_the_cadet_can_see_that_a_chief_read_their_results(self):
        chief.get_cadet_results(self.path, self.boss, self.cad["p5-0"], now=NOW)
        chief.list_platoon_members(self.path, self.boss, self.p5, now=NOW)
        seen = repo.export_user_data(self.path, self.cad["p5-0"])["accessed_by"]
        self.assertEqual(seen, [{"date": "2026-11-03", "reader": "Chief Zq", "reads": 1}])
        self.assertEqual(repo.export_user_data(self.path, self.cad["p5-1"])["accessed_by"], [], "others were not opened")

    def test_reads_change_no_user_or_result_data(self):
        def snap():
            with db.reader(self.path) as c:
                return ([tuple(r) for r in c.execute("SELECT * FROM users ORDER BY id")],
                        [tuple(r) for r in c.execute("SELECT * FROM results ORDER BY id")])
        before = snap()
        self.overview()
        chief.list_platoon_members(self.path, self.boss, self.p6, now=NOW)
        chief.get_cadet_results(self.path, self.boss, self.cad["p6-0"], now=NOW)
        self.assertEqual(snap(), before)


class CreatingAChief(ChiefCase):
    def test_the_allowlist_can_never_mint_a_chief(self):
        with self.assertRaises(ValidationError):
            allowlist.add_entry(self.path, "new.chief@example.com", role="chief")
        with self.assertRaises(ValidationError):
            admin.add_allowed_email(self.path, self.admin, "new.chief@example.com", role="chief")
        with self.assertRaises(Exception):
            with db.transaction(self.path) as c:
                c.execute("INSERT INTO allowed_emails(email,role) VALUES('x@example.com','chief')")
        allowlist.add_entry(self.path, "plain@example.com")
        u = service.register(self.path, email="plain@example.com", name="Plain", gender="F", is_km=False, password="12345678")
        self.assertEqual(u["role"], "cadet")

    def test_admin_can_appoint_and_it_is_audited_and_clears_platoon_and_grants(self):
        cand = self.cad["p5-0"]
        admin.set_role(self.path, self.admin, cand, "chief")
        user = repo.get_user(self.path, cand)
        self.assertEqual((user["role"], user["platoon_id"]), ("chief", None))
        self.assertEqual(self.rows(action="admin.set_role", target_user_id=cand), [(self.admin, "admin.set_role", cand, None, "ok")])
        self.assertEqual(len(chief.overview(self.path, cand, G, now=NOW)["platoons"]), 4)
        with self.assertRaises(ValidationError):
            admin.grant_platoon(self.path, self.admin, cand, self.p5)     # grants are for instructors/admins only
        admin.set_role(self.path, self.admin, cand, "cadet")
        with self.assertRaises(AuthError):
            chief.overview(self.path, cand, G, now=NOW)

    def test_a_cadet_cannot_appoint_themselves_or_anyone(self):
        with self.assertRaises(AuthError):
            admin.set_role(self.path, self.cad["p5-0"], self.cad["p5-0"], "chief")
        with self.assertRaises(AuthError):
            admin.set_role(self.path, self.boss, self.cad["p5-0"], "chief")

    def test_operator_command_sets_the_role(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "admin_users.py"), "--db", self.path,
                            "set-role", "p5-0@example.com", "chief"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(repo.get_user(self.path, self.cad["p5-0"])["role"], "chief")

    def test_unknown_role_is_still_rejected(self):
        with self.assertRaises(ValidationError):
            ops.set_role(self.path, self.cad["p5-0"], "commander")


class ModuleShape(unittest.TestCase):
    def test_public_functions_are_the_three_reads(self):
        pub = sorted(n for n, f in inspect.getmembers(chief, inspect.isfunction)
                     if not n.startswith("_") and f.__module__ == chief.__name__)
        self.assertEqual(pub, ["get_cadet_results", "list_platoon_members", "overview"])

    def test_source_has_no_write_statements(self):
        src = inspect.getsource(chief)
        self.assertEqual(set(re.findall(r"INSERT\s+INTO\s+(\w+)", src)), set(), "audit goes through audit.record")
        self.assertFalse(re.findall(r"\b(UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE|REPLACE\s+INTO)\b", src))

    def test_chief_imports_no_domain_or_write_modules(self):
        src = inspect.getsource(chief)
        for bad in ("baror.domain", "allowlist", "admin", "ops", "repo", "retention", "streamlit"):
            self.assertNotRegex(src, rf"(?m)^(from|import)\s+.*\b{bad}\b", bad)


if __name__ == "__main__":
    unittest.main()
