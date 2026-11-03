"""Schema v4 -> v5: the date CHECK must refuse impossible dates on EVERY SQLite build (the production image's 3.40.1
returns date('2026-02-31') unchanged, so the old check accepted it there)."""
import os
import sqlite3
import tempfile
import unittest

from baror.errors import StorageError, ValidationError
from baror.storage import db, repo, retention
from tests.base import DbCase
from tests.test_migration_v2 import build_v1
from tests.test_migration_v3 import person_view

IMPOSSIBLE = ["2026-02-31", "2026-02-30", "2026-04-31", "2026-06-31", "2026-02-29", "2023-02-29", "2026-13-01", "2026-00-10",
              "2026-1-5", "20260105", "2026-01-05 10:00", "", "abcd-ef-gh"]


class V4ToV5(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "baror.db")
        build_v1(self.path)
        db.init_db(self.path, target_version=4)
        c = sqlite3.connect(self.path)
        c.execute("INSERT INTO platoons(name,course_end) VALUES('A','2026-01-31')")
        c.execute("INSERT INTO platoons(name) VALUES('B')")
        c.execute("UPDATE users SET platoon_id=1 WHERE id IN (2,3)")
        c.commit()
        c.close()
        self.before = person_view(self.path)
        with db.reader(self.path) as c:
            self.platoons = [tuple(r) for r in c.execute("SELECT id,name,created_at,course_end FROM platoons ORDER BY id")]

    def test_upgrade_keeps_every_row(self):
        self.assertEqual(db.init_db(self.path), 5)
        self.assertEqual(person_view(self.path), self.before)
        with db.reader(self.path) as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT id,name,created_at,course_end FROM platoons ORDER BY id")], self.platoons)
            self.assertEqual([r[0] for r in c.execute("SELECT platoon_id FROM users WHERE platoon_id IS NOT NULL")], [1, 1])
            fks = {(t, r[2]) for t in ("results", "users", "instructor_platoons", "allowed_emails", "audit_log", "pseudonyms")
                   for r in c.execute(f"PRAGMA foreign_key_list({t})")}
            idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn(("results", "pseudonyms"), fks)
        self.assertIn(("users", "platoons"), fks)
        self.assertIn(("pseudonyms", "platoons"), fks)
        self.assertIn("idx_results_pseudonym_date", idx)
        self.assertEqual(db.integrity_check(self.path), [])

    def test_the_check_uses_julianday_and_refuses_impossible_dates(self):
        db.init_db(self.path)
        with db.reader(self.path) as c:
            sql = " ".join(r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE name IN ('results','platoons')"))
        self.assertEqual(sql.count("julianday"), 2, "both the result date and the course end use the build-independent form")
        with db.reader(self.path) as c:
            ps = c.execute("SELECT pseudonym FROM users WHERE id=2").fetchone()[0]
        conn = db.connect(self.path)
        try:
            for bad in IMPOSSIBLE:
                with self.assertRaises(sqlite3.IntegrityError, msg=bad):
                    conn.execute("INSERT INTO results(pseudonym,test_date,pushups) VALUES(?,?,5)", (ps, bad))
                with self.assertRaises(sqlite3.IntegrityError, msg=bad):
                    conn.execute("UPDATE platoons SET course_end=? WHERE id=2", (bad,))
            for good in ("2024-02-29", "2026-02-28", "2026-12-31", "2000-01-01"):
                conn.execute("INSERT INTO results(pseudonym,test_date,pushups) VALUES(?,?,5)", (ps, good))
                conn.execute("UPDATE platoons SET course_end=? WHERE id=2", (good,))
            conn.execute("UPDATE platoons SET course_end=NULL WHERE id=2")
        finally:
            conn.close()

    def test_a_row_that_already_violates_the_stricter_check_aborts_the_migration(self):
        c = sqlite3.connect(self.path)
        with db.reader(self.path) as r:
            ps = r.execute("SELECT pseudonym FROM users WHERE id=2").fetchone()[0]
        # emulate what the old check let through on the production SQLite build: write the bad row with checks off
        c.execute("PRAGMA ignore_check_constraints=ON")
        c.execute("INSERT INTO results(pseudonym,test_date,pushups) VALUES(?,'2026-02-31',5)", (ps,))
        c.commit()
        c.close()
        before = person_view(self.path)
        with self.assertRaises(ValidationError) as cm:          # sqlite3.IntegrityError, translated by the storage layer
            db.init_db(self.path)
        self.assertIn("CHECK", str(cm.exception).upper())
        self.assertEqual(db.user_version(self.path), 4, "nothing was changed")
        self.assertEqual(person_view(self.path), before)
        with db.reader(self.path) as r:
            self.assertEqual(r.execute("SELECT count(*) FROM results WHERE test_date='2026-02-31'").fetchone()[0], 1)

    def test_idempotent_and_fresh_database_has_the_same_shape(self):
        db.init_db(self.path)
        self.assertEqual(db.init_db(self.path), 5)
        fresh = os.path.join(self._tmp.name, "fresh.db")
        db.init_db(fresh)

        def shape(p):
            with db.reader(p) as c:
                return sorted((r[0], tuple(x[1] for x in c.execute(f"PRAGMA table_info({r[0]})")),
                               tuple(sorted(y[2] for y in c.execute(f"PRAGMA foreign_key_list({r[0]})"))))
                              for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        self.assertEqual(shape(fresh), shape(self.path))

    def test_failed_upgrade_rolls_back_to_v4(self):
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES('orphan',999,'2099-01-01T00:00:00Z')")
        c.commit()
        c.close()
        with self.assertRaises(StorageError):
            db.init_db(self.path)
        self.assertEqual(db.user_version(self.path), 4)


class RepositoryStillRefusesFirst(DbCase):
    def test_the_application_layer_and_the_database_agree(self):
        uid = self.user()
        for bad in IMPOSSIBLE[:6]:
            with self.assertRaises(Exception, msg=bad):
                repo.add_result(self.path, uid, test_date=bad, pushups=5)
        with self.assertRaises(Exception):
            retention.set_course_end(self.path, 1, "2026-02-31")


if __name__ == "__main__":
    unittest.main()
