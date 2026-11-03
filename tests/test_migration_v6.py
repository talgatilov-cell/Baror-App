"""Schema v5 -> v6: the accommodations table (הקלות ופטורים). Nothing else changes, nothing is rebuilt, and an upgrade
keeps every existing row."""
import os
import sqlite3
import tempfile
import unittest

from baror.errors import StorageError
from baror.storage import db
from tests.test_migration_v2 import build_v1
from tests.test_migration_v3 import person_view


class V5ToV6(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "baror.db")
        build_v1(self.path)
        db.init_db(self.path, target_version=5)
        self.before = person_view(self.path)

    def test_upgrade_adds_the_table_and_keeps_every_row(self):
        self.assertEqual(db.user_version(self.path), 5)
        self.assertEqual(db.init_db(self.path), 6)
        self.assertEqual(person_view(self.path), self.before)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM accommodations").fetchone()[0], 0)
            fks = {(r[2], r[3]) for r in c.execute("PRAGMA foreign_key_list(accommodations)")}
            idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertEqual(fks, {("users", "user_id"), ("users", "created_by"), ("users", "delete_requested_by")})
        self.assertTrue({"idx_accommodations_user", "idx_accommodations_pending"} <= idx)
        self.assertEqual(db.integrity_check(self.path), [])

    def test_the_table_hangs_off_the_user_not_the_results_pseudonym(self):
        db.init_db(self.path)
        with db.reader(self.path) as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(accommodations)")]
        self.assertIn("user_id", cols)
        self.assertNotIn("pseudonym", cols)

    def test_idempotent_and_a_fresh_database_has_the_same_shape(self):
        db.init_db(self.path)
        self.assertEqual(db.init_db(self.path), 6)
        fresh = os.path.join(self._tmp.name, "fresh.db")
        db.init_db(fresh)

        def shape(p):
            with db.reader(p) as c:
                return sorted((r[0], tuple(x[1] for x in c.execute(f"PRAGMA table_info({r[0]})")),
                               tuple(sorted(y[2] for y in c.execute(f"PRAGMA foreign_key_list({r[0]})"))))
                              for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        self.assertEqual(shape(fresh), shape(self.path))

    def test_a_failed_upgrade_leaves_v5(self):
        c = sqlite3.connect(self.path)
        c.execute("CREATE TABLE accommodations(x)")             # a clash: the CREATE TABLE of v6 must fail as a whole
        c.commit()
        c.close()
        with self.assertRaises(StorageError):
            db.init_db(self.path)
        self.assertEqual(db.user_version(self.path), 5)
        self.assertEqual(person_view(self.path), self.before)

    def test_the_cascade_removes_records_with_the_account_and_the_author_link_is_nulled(self):
        db.init_db(self.path)
        with db.transaction(self.path) as c:
            c.execute("INSERT INTO accommodations(user_id,kind,scope,start_date,end_date,source,created_by) VALUES(2,'relief','run','2026-11-01','2026-11-30','instructor',3)")
            c.execute("INSERT INTO accommodations(user_id,kind,scope,start_date,end_date,source,created_by) VALUES(3,'relief','run','2026-11-01','2026-11-30','cadet',3)")
        with db.transaction(self.path) as c:
            c.execute("DELETE FROM users WHERE id=3")
        with db.reader(self.path) as c:
            rows = [tuple(r) for r in c.execute("SELECT user_id, created_by FROM accommodations")]
        self.assertEqual(rows, [(2, None)])


if __name__ == "__main__":
    unittest.main()
