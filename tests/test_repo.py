import unittest

from baror.errors import ConflictError, NotFoundError, ValidationError
from baror.storage import repo
from tests.base import DbCase


class Isolation(DbCase):
    """T1.3 acceptance: A can never read, update or delete B's rows, even with B's result id."""

    def setUp(self):
        super().setUp()
        self.a = self.user("a@example.com")
        self.b = self.user("b@example.com")
        self.rb = repo.add_result(self.path, self.b, test_date="2026-01-02", run_tenths=9400, pushups=30)
        self.ra = repo.add_result(self.path, self.a, test_date="2026-01-03", run_tenths=9000, pushups=25)

    def test_list_is_scoped(self):
        self.assertEqual([r["id"] for r in repo.list_results(self.path, self.a)], [self.ra])
        self.assertEqual([r["id"] for r in repo.list_results(self.path, self.b)], [self.rb])

    def test_update_with_foreign_id_fails_and_changes_nothing(self):
        with self.assertRaises(NotFoundError):
            repo.update_result(self.path, self.a, self.rb, test_date="2026-01-02", run_tenths=1, pushups=1)
        self.assertEqual(repo.list_results(self.path, self.b)[0]["run_tenths"], 9400)

    def test_delete_with_foreign_id_fails_and_changes_nothing(self):
        with self.assertRaises(NotFoundError):
            repo.delete_result(self.path, self.a, self.rb)
        self.assertEqual(len(repo.list_results(self.path, self.b)), 1)

    def test_foreign_and_missing_ids_are_indistinguishable(self):
        with self.assertRaises(NotFoundError) as e1:
            repo.delete_result(self.path, self.a, self.rb)
        with self.assertRaises(NotFoundError) as e2:
            repo.delete_result(self.path, self.a, 99999)
        self.assertEqual(type(e1.exception), type(e2.exception))
        self.assertEqual(e1.exception.message_key, e2.exception.message_key)

    def test_export_has_no_password_and_no_other_users(self):
        data = repo.export_user_data(self.path, self.a)
        blob = repr(data)
        self.assertNotIn("pw_hash", blob)
        self.assertNotIn("b@example.com", blob)
        self.assertEqual(len(data["results"]), 1)

    def test_delete_user_cascades_only_that_user(self):
        repo.delete_user(self.path, self.a)
        self.assertEqual(repo.list_results(self.path, self.a), [])
        self.assertEqual(len(repo.list_results(self.path, self.b)), 1)

    def test_module_exposes_no_list_all_users(self):
        public = {n for n in dir(repo) if not n.startswith("_")}
        self.assertFalse({"list_users", "all_users", "load_users", "list_all_results"} & public)

    def test_sql_injection_in_email_is_data(self):
        self.assertIsNone(repo.get_login_record(self.path, "' OR 1=1 --@x.com"))
        uid = self.user("o'brien@example.com", name="Robert'); DROP TABLE users;--")
        self.assertEqual(repo.get_user(self.path, uid)["name"], "Robert'); DROP TABLE users;--")
        self.assertIsNotNone(repo.get_user(self.path, self.a))


class UserFunctions(DbCase):
    def test_email_normalisation_and_login_lookup(self):
        uid = self.user("  Mixed.Case@Example.COM ")
        for variant in ["mixed.case@example.com", "MIXED.CASE@EXAMPLE.COM", " mixed.case@example.com\t"]:
            self.assertEqual(repo.get_login_record(self.path, variant)["id"], uid)
        self.assertIsNone(repo.get_login_record(self.path, "missing@example.com"))
        self.assertIsNone(repo.get_login_record(self.path, None))

    def test_invalid_inputs(self):
        for kw in [dict(email=""), dict(email="no-at"), dict(email="a b@x.com"), dict(name="   "),
                   dict(name="x" * 81), dict(gender="זכר"), dict(gender="")]:
            with self.subTest(kw=kw), self.assertRaises(ValidationError):
                self.user(**kw)

    def test_duplicate_registration_conflict(self):
        self.user("x@example.com")
        with self.assertRaises(ConflictError):
            self.user("X@example.com")

    def test_pw_hash_only_via_login_record(self):
        uid = self.user("p@example.com", pw_hash="scrypt$secret")
        self.assertNotIn("pw_hash", repo.get_user(self.path, uid))
        self.assertEqual(repo.get_login_record(self.path, "p@example.com")["pw_hash"], "scrypt$secret")

    def test_password_change_revokes_sessions(self):
        from baror.storage import db
        uid = self.user("s@example.com")
        with db.transaction(self.path) as c:
            c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES('h1',?,'2099-01-01T00:00:00Z')", (uid,))
        repo.set_password_hash(self.path, uid, "scrypt$new", must_change_pw=False)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT revoked FROM sessions WHERE token_hash='h1'").fetchone()[0], 1)

    def test_update_profile(self):
        uid = self.user("u@example.com")
        repo.update_profile(self.path, uid, name="  New Name ", gender="F", is_km=True)
        u = repo.get_user(self.path, uid)
        self.assertEqual((u["name"], u["gender"], u["is_km"]), ("New Name", "F", 1))
        with self.assertRaises(NotFoundError):
            repo.update_profile(self.path, 9999, name="x")


class ResultFunctions(DbCase):
    def setUp(self):
        super().setUp()
        self.uid = self.user()

    def test_add_list_order(self):
        for d, p in [("2026-01-05", 1), ("2026-01-07", 2), ("2026-01-07", 3), ("2026-01-01", 4)]:
            repo.add_result(self.path, self.uid, test_date=d, pushups=p)
        got = [(r["test_date"], r["pushups"]) for r in repo.list_results(self.path, self.uid)]
        self.assertEqual(got, [("2026-01-07", 3), ("2026-01-07", 2), ("2026-01-05", 1), ("2026-01-01", 4)])

    def test_validation(self):
        bad = [dict(test_date="2026-02-31", pushups=1), dict(test_date="2026-1-1", pushups=1),
               dict(test_date="2026-01-01"), dict(test_date="2026-01-01", run_tenths=0),
               dict(test_date="2026-01-01", run_tenths=72001), dict(test_date="2026-01-01", pushups=-1),
               dict(test_date="2026-01-01", pushups=True), dict(test_date="2026-01-01", pushups=1.5),
               dict(test_date="2026-01-01", pushups="5"), dict(test_date=None, pushups=1)]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(ValidationError):
                repo.add_result(self.path, self.uid, **kw)
        self.assertEqual(repo.list_results(self.path, self.uid), [])

    def test_zero_pushups_storable_but_explicit(self):
        rid = repo.add_result(self.path, self.uid, test_date="2026-01-01", pushups=0)
        self.assertEqual(repo.list_results(self.path, self.uid)[0]["pushups"], 0)
        self.assertEqual(rid, repo.list_results(self.path, self.uid)[0]["id"])

    def test_update_and_delete(self):
        rid = repo.add_result(self.path, self.uid, test_date="2026-01-01", run_tenths=9400, pushups=20)
        repo.update_result(self.path, self.uid, rid, test_date="2026-01-02", run_tenths=9300, pushups=None)
        r = repo.list_results(self.path, self.uid)[0]
        self.assertEqual((r["test_date"], r["run_tenths"], r["pushups"]), ("2026-01-02", 9300, None))
        repo.delete_result(self.path, self.uid, rid)
        self.assertEqual(repo.list_results(self.path, self.uid), [])

    def test_find_duplicate_handles_nulls(self):
        repo.add_result(self.path, self.uid, test_date="2026-01-01", run_tenths=9400, pushups=None)
        self.assertTrue(repo.find_duplicate(self.path, self.uid, test_date="2026-01-01", run_tenths=9400, pushups=None))
        self.assertFalse(repo.find_duplicate(self.path, self.uid, test_date="2026-01-01", run_tenths=9400, pushups=0))


if __name__ == "__main__":
    unittest.main()
