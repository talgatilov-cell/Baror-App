import datetime as dt
import inspect
import re
import unittest

from baror.errors import AuthError, NotFoundError
from baror.storage import admin, audit, cohort, db, ops, repo
from tests.v2base import V2Case

NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc)


class CohortBase(V2Case):
    def setUp(self):
        super().setUp()
        self.pa, self.pb = self.platoon("A"), self.platoon("B")
        self.ca = self.cadet("ca@x.com", self.pa, name="Alef")
        self.cb = self.cadet("cb@x.com", self.pb, name="Bet")
        self.ins = self.staff("i@x.com", platoons=[self.pa])
        self.adm = self.staff("adm@x.com", role="admin")
        repo.add_result(self.path, self.ca, test_date="2026-01-01", run_tenths=900, pushups=30)
        repo.add_result(self.path, self.ca, test_date="2026-02-01", run_tenths=950, pushups=40)
        repo.add_result(self.path, self.cb, test_date="2026-01-01", run_tenths=800, pushups=50)

    def calls(self, actor):
        return [lambda: cohort.list_platoon_members(self.path, actor, self.pa),
                lambda: cohort.cohort_summary(self.path, actor, self.pa),
                lambda: cohort.get_cadet_results(self.path, actor, self.ca)]

    def n_audit(self):
        return len(self.audit_rows())


class IdorMatrix(CohortBase):
    def test_instructor_reads_own_platoon(self):
        m = cohort.list_platoon_members(self.path, self.ins, self.pa)
        self.assertEqual([x["id"] for x in m], [self.ca])
        self.assertEqual(set(m[0]), {"id", "name", "gender", "is_km"})
        r = cohort.get_cadet_results(self.path, self.ins, self.ca)
        self.assertEqual([x["pushups"] for x in r["results"]], [40, 30])
        s = cohort.cohort_summary(self.path, self.ins, self.pa)[0]
        self.assertEqual((s["best_run_tenths"], s["best_pushups"], s["result_count"]), (900, 40, 2))
        self.assertEqual(s["latest_run"], {"test_date": "2026-02-01", "run_tenths": 950})

    def test_other_platoon_cadet_and_platoon_denied_identically(self):
        before = self.n_audit()
        for f in (lambda: cohort.get_cadet_results(self.path, self.ins, self.cb),
                  lambda: cohort.list_platoon_members(self.path, self.ins, self.pb),
                  lambda: cohort.cohort_summary(self.path, self.ins, self.pb)):
            with self.assertRaises(NotFoundError):
                f()
        # same error as a non-existent cadet / platoon (no existence probing)
        codes = set()
        for f in (lambda: cohort.get_cadet_results(self.path, self.ins, self.cb),
                  lambda: cohort.get_cadet_results(self.path, self.ins, 9999),
                  lambda: cohort.list_platoon_members(self.path, self.ins, 9999)):
            with self.assertRaises(NotFoundError) as cm:
                f()
            codes.add(cm.exception.message_key)
        self.assertEqual(len(codes), 1)
        self.assertTrue(all(r[4] == "denied" for r in self.audit_rows()[before:]))

    def test_instructor_without_platoons(self):
        i2 = self.staff("i2@x.com")
        for f in self.calls(i2):
            with self.assertRaises(NotFoundError):
                f()

    def test_cadet_actor_denied_everything(self):
        for f in self.calls(self.ca) + self.calls(self.cb):
            with self.assertRaises(AuthError):
                f()

    def test_bad_actor_ids(self):
        for bad in (None, 0, -1, 9999, "1", True, 1.0, 2**70):
            with self.subTest(bad=bad):
                for f in self.calls(bad):
                    with self.assertRaises(AuthError):
                        f()

    def test_bad_target_ids_are_not_found(self):
        for bad in (None, 0, -1, "1", True, 2**70):
            with self.subTest(bad=bad), self.assertRaises(NotFoundError):
                cohort.get_cadet_results(self.path, self.ins, bad)

    def test_deactivated_instructor(self):
        ops.deactivate_user(self.path, self.ins)
        for f in self.calls(self.ins):
            with self.assertRaises(AuthError):
                f()

    def test_role_change_midsession_is_reread(self):
        self.assertTrue(cohort.list_platoon_members(self.path, self.ins, self.pa))
        ops.set_role(self.path, self.ins, "cadet")
        for f in self.calls(self.ins):
            with self.assertRaises(AuthError):
                f()
        ops.set_role(self.path, self.ins, "instructor")  # grants were deleted on demotion
        for f in self.calls(self.ins):
            with self.assertRaises(NotFoundError):
                f()

    def test_revoked_grant_takes_effect_immediately(self):
        cohort.get_cadet_results(self.path, self.ins, self.ca)
        ops.revoke_platoon(self.path, self.ins, self.pa)
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, self.ca)

    def test_cadet_moved_to_other_platoon_loses_access(self):
        ops.set_user_platoon(self.path, self.ca, self.pb)
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, self.ca)
        self.assertEqual(cohort.list_platoon_members(self.path, self.ins, self.pa), [])

    def test_deleted_and_deactivated_cadet(self):
        ops.deactivate_user(self.path, self.ca)
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, self.ca)
        self.assertEqual(cohort.list_platoon_members(self.path, self.ins, self.pa), [])
        ops.reactivate_user(self.path, self.ca)
        repo.delete_user(self.path, self.ca)
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, self.ca)

    def test_staff_account_is_not_a_readable_target(self):
        i2 = self.staff("i2@x.com", platoons=[self.pa])
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, i2)

    def test_admin_has_no_implicit_access_but_can_be_granted(self):
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.adm, self.ca)
        ops.grant_platoon(self.path, self.adm, self.pa)
        self.assertEqual(cohort.get_cadet_results(self.path, self.adm, self.ca)["cadet"]["id"], self.ca)
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.adm, self.cb)

    def test_cadet_without_platoon_unreadable(self):
        c = self.cadet("np@x.com")
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, c)


class CohortAudit(CohortBase):
    def test_every_read_writes_ids_only_audit_rows(self):
        cohort.list_platoon_members(self.path, self.ins, self.pa, now=NOW)
        cohort.get_cadet_results(self.path, self.ins, self.ca, now=NOW)
        cohort.cohort_summary(self.path, self.ins, self.pa, now=NOW)
        rows = self.audit_rows(actor_id=self.ins)
        self.assertEqual(rows, [(self.ins, "cohort.members", None, self.pa, "ok"),
                                (self.ins, "cohort.cadet", self.ca, self.pa, "ok"),
                                (self.ins, "cohort.summary", self.ca, self.pa, "ok")])
        with db.reader(self.path) as c:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(audit_log)")]
            blob = repr([tuple(r) for r in c.execute("SELECT * FROM audit_log WHERE actor_id=?", (self.ins,))])
        self.assertEqual(cols, ["id", "ts", "actor_id", "action", "target_user_id", "platoon_id", "outcome"])
        for secret in ("ca@x.com", "Alef", "900", "Test"):
            self.assertNotIn(secret, blob)
        self.assertIn("2026-11-02T12:00:00Z", blob)

    def test_summary_one_row_per_cadet_and_empty_platoon_one_row(self):
        self.cadet("ca2@x.com", self.pa)
        cohort.cohort_summary(self.path, self.ins, self.pa)
        self.assertEqual(len(self.audit_rows(action="cohort.summary")), 2)
        pe = self.platoon("E")
        ops.grant_platoon(self.path, self.ins, pe)
        self.assertEqual(cohort.cohort_summary(self.path, self.ins, pe), [])
        self.assertEqual(self.audit_rows(action="cohort.summary", platoon_id=pe), [(self.ins, "cohort.summary", None, pe, "ok")])

    def test_denied_probe_audited_for_existing_actor_only(self):
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, self.cb)
        self.assertEqual(self.audit_rows(outcome="denied"), [(self.ins, "cohort.cadet", self.cb, None, "denied")])
        with self.assertRaises(AuthError):
            cohort.get_cadet_results(self.path, self.ca, self.cb)  # cadet actor exists -> denied row
        with self.assertRaises(AuthError):
            cohort.get_cadet_results(self.path, 9999, self.cb)  # unknown actor -> nothing recorded
        self.assertEqual(len(self.audit_rows(outcome="denied")), 2)
        self.assertEqual(self.audit_rows(actor_id=9999), [])

    def test_denied_probe_of_nonexistent_ids_stores_null(self):
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, 9999)
        self.assertEqual(self.audit_rows(outcome="denied"), [(self.ins, "cohort.cadet", None, None, "denied")])

    def test_audit_row_is_in_same_transaction_as_read(self):
        orig = audit.record
        before = self.audit_rows()

        def boom(*a, **k):
            raise RuntimeError("audit failed")
        audit.record = boom
        try:
            with self.assertRaises(RuntimeError):
                cohort.get_cadet_results(self.path, self.ins, self.ca)
        finally:
            audit.record = orig
        self.assertEqual(self.audit_rows(), before)

    def test_disclosure_counts_reads_not_denied(self):
        cohort.get_cadet_results(self.path, self.ins, self.ca, now=NOW)
        cohort.cohort_summary(self.path, self.ins, self.pa, now=NOW)
        cohort.list_platoon_members(self.path, self.ins, self.pa, now=NOW)  # not a data read
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, self.cb, now=NOW)
        with db.reader(self.path) as c:
            d = audit.reads_of_user(c, self.ca, now=NOW)
            self.assertEqual(d, [{"date": "2026-11-02", "reader": "I", "reads": 2}])
            self.assertEqual(audit.reads_of_user(c, self.cb, now=NOW), [])


class CohortReadOnly(CohortBase):
    def test_module_public_functions_are_the_four_reads(self):
        pub = [n for n, f in inspect.getmembers(cohort, inspect.isfunction)
               if not n.startswith("_") and f.__module__ == cohort.__name__]
        self.assertEqual(sorted(pub), sorted(cohort.__all__))
        self.assertEqual(sorted(pub), ["cohort_summary", "get_cadet_results", "list_platoon_members", "my_platoons"])

    def test_source_has_no_write_statements_except_audit_insert(self):
        src = inspect.getsource(cohort)
        for m in re.finditer(r"\b(UPDATE|DELETE|REPLACE|DROP|ALTER|CREATE)\b\s", re.sub(r'""".*?"""', "", src, flags=re.S)):
            self.fail(f"write keyword in cohort.py: {m.group(0)}")
        inserts = re.findall(r"INSERT\s+INTO\s+(\w+)", src)
        self.assertTrue(inserts and set(inserts) == {"audit_log"})

    def test_cohort_does_not_import_write_modules(self):
        for name in ("admin", "ops", "repo", "allowlist"):
            self.assertFalse(hasattr(cohort, name), name)

    def test_reads_do_not_change_user_or_result_data(self):
        def snap():
            with db.reader(self.path) as c:
                return [tuple(r) for t in ("users", "results", "instructor_platoons", "platoons")
                        for r in c.execute(f"SELECT * FROM {t}")]
        before = snap()
        for f in self.calls(self.ins):
            f()
        self.assertEqual(snap(), before)

    def test_instructor_cannot_call_admin_functions(self):
        for f in (lambda: admin.set_role(self.path, self.ins, self.ca, "admin"),
                  lambda: admin.grant_platoon(self.path, self.ins, self.ins, self.pb)):
            with self.assertRaises(AuthError):
                f()


class MyPlatoons(CohortBase):
    def test_lists_exactly_the_granted_platoons_by_name_and_writes_no_audit_row(self):
        ops.grant_platoon(self.path, self.ins, self.pb)
        n = self.n_audit()
        self.assertEqual(cohort.my_platoons(self.path, self.ins), [{"id": self.pa, "name": "A"}, {"id": self.pb, "name": "B"}])
        self.assertEqual(self.n_audit(), n)

    def test_instructor_without_grants_gets_an_empty_list(self):
        self.assertEqual(cohort.my_platoons(self.path, self.staff("none@x.com")), [])

    def test_admin_sees_only_explicit_grants(self):
        self.assertEqual(cohort.my_platoons(self.path, self.adm), [])
        ops.grant_platoon(self.path, self.adm, self.pa)
        self.assertEqual(cohort.my_platoons(self.path, self.adm), [{"id": self.pa, "name": "A"}])

    def test_cadets_deactivated_staff_and_strangers_are_refused(self):
        ops.deactivate_user(self.path, self.ins)
        for actor in (self.ca, self.ins, 999, None, True, "1"):
            with self.assertRaises(AuthError, msg=repr(actor)):
                cohort.my_platoons(self.path, actor)


if __name__ == "__main__":
    unittest.main()
