"""The demo seed (demo/demo_seed.py) builds a schema v9 database through the same rules as the app: everybody starts as a
cadet, staff roles are granted afterwards, the two admins have no platoon, and the open requests give the bell content."""
import importlib.util
import io
import contextlib
import tempfile
import unittest
from pathlib import Path

from baror.auth import passwords
from baror.storage import db, notifications, people, repo

SEED = Path(__file__).resolve().parent.parent / "demo" / "demo_seed.py"


def seed(path):
    spec = importlib.util.spec_from_file_location("demo_seed_under_test", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with contextlib.redirect_stdout(io.StringIO()):
        module.main(str(path))
    return module


class DemoSeedV9(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "demo.db"
        cls.seed = seed(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self, sql, *args):
        with db.reader(self.path) as c:
            return [tuple(r) for r in c.execute(sql, args)]

    def uid(self, email):
        return self.rows("SELECT id FROM users WHERE email=?", email)[0][0]

    def test_schema_is_v9_and_the_allowlist_is_gone(self):
        self.assertEqual(self.rows("PRAGMA user_version")[0][0], 9)
        self.assertEqual(self.rows("SELECT name FROM sqlite_master WHERE name='allowed_emails'"), [])

    def test_every_listed_account_exists_with_the_demo_password(self):
        for email, _what in self.seed.ACCOUNTS:
            rec = repo.get_login_record(self.path, email)
            self.assertIsNotNone(rec, email)
            self.assertTrue(email.endswith("@example.com"))
        rec = repo.get_login_record(self.path, "chief@example.com")
        self.assertTrue(passwords.verify_password(self.seed.PASSWORD, rec["pw_hash"]))

    def test_structure_is_one_company_two_chiefs_two_admins_one_mm_per_platoon(self):
        self.assertEqual(self.rows("SELECT count(*) FROM platoons")[0][0], 3)
        self.assertEqual(self.rows("SELECT title FROM users WHERE role='chief' ORDER BY title"), [("mp",), ("smp",)])
        self.assertEqual(self.rows("SELECT count(*) FROM users WHERE role='admin'")[0][0], 2)
        mm = self.rows("SELECT ip.platoon_id, count(*) FROM users u JOIN instructor_platoons ip ON ip.instructor_id=u.id "
                       "WHERE u.title='mm' GROUP BY ip.platoon_id")
        self.assertEqual(len(mm), 3)
        self.assertTrue(all(n == 1 for _p, n in mm))
        cmdrs = self.rows("SELECT ip.platoon_id FROM users u JOIN instructor_platoons ip ON ip.instructor_id=u.id "
                          "WHERE u.title='cmdr'")
        self.assertEqual(len(cmdrs), 2)
        self.assertEqual(len({p for (p,) in cmdrs}), 1, "the demo מ\"כים are both in platoon א")

    def test_admins_have_no_platoon_grants_and_instructors_exactly_one(self):
        self.assertEqual(self.rows("SELECT count(*) FROM instructor_platoons ip JOIN users u ON u.id=ip.instructor_id "
                                   "WHERE u.role<>'instructor'")[0][0], 0)
        self.assertEqual(self.rows("SELECT count(*) FROM users WHERE role='instructor'")[0][0],
                         self.rows("SELECT count(*) FROM instructor_platoons")[0][0])

    def test_unassigned_and_promotable_cadets_exist(self):
        for email in ("unassigned", "candidate2", "candidate3"):
            self.assertEqual(self.rows("SELECT role, platoon_id FROM users WHERE email=?", f"{email}@example.com"),
                             [("cadet", None)], email)

    def test_open_requests_give_the_bell_content(self):
        kinds = {k for (k,) in self.rows("SELECT kind FROM cadet_change_requests WHERE status='pending'")}
        self.assertIn("nominate_cmdr", kinds)
        self.assertTrue(kinds & {"add_conflict", "edit"})
        chief = self.uid("chief@example.com")
        admin = self.uid("admin@example.com")
        self.assertGreaterEqual(notifications.counts(self.path, chief)["change_requests"], 2)
        self.assertGreaterEqual(notifications.counts(self.path, admin)["change_requests"], 2)
        self.assertEqual(notifications.counts(self.path, chief)["deletion_requests"], 1)

    def test_the_pending_nomination_is_for_a_registered_cadet_of_the_mms_platoon(self):
        ins = self.uid("ins@example.com")
        rows = people.pending_changes(self.path, self.uid("admin@example.com"))
        nom = [r for r in rows if r["kind"] == "nominate_cmdr"]
        self.assertEqual(len(nom), 1)
        self.assertEqual(nom[0]["requested_by"], ins)
        self.assertEqual(nom[0]["target_user_id"], self.uid("candidate1@example.com"))

    def test_it_refuses_an_existing_database_file(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                self.seed.main(str(self.path))


if __name__ == "__main__":
    unittest.main()
