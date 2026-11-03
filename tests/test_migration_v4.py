"""Schema v3 -> v4: the `chief` role. Only the role CHECK changes; every row must survive."""
import os
import sqlite3
import tempfile
import unittest

from baror.errors import StorageError
from baror.storage import db
from tests.test_migration_v2 import build_v1
from tests.test_migration_v3 import person_view


class V3ToV4(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "baror.db")
        build_v1(self.path)
        db.init_db(self.path, target_version=3)
        c = sqlite3.connect(self.path)
        c.execute("INSERT INTO platoons(name) VALUES('A')")
        c.execute("UPDATE users SET platoon_id=1 WHERE id IN (2,3)")
        c.commit()
        c.close()
        self.before = person_view(self.path)
        with db.reader(self.path) as c:
            self.ps = [tuple(r) for r in c.execute("SELECT id,pseudonym,platoon_id,active FROM users ORDER BY id")]

    def test_upgrade_keeps_everything_and_pseudonyms_do_not_change(self):
        self.assertEqual(db.init_db(self.path), 4)
        self.assertEqual(person_view(self.path), self.before)
        with db.reader(self.path) as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT id,pseudonym,platoon_id,active FROM users ORDER BY id")], self.ps)
            self.assertEqual(c.execute("SELECT count(*) FROM pseudonyms").fetchone()[0], 3)
        self.assertEqual(db.integrity_check(self.path), [])

    def test_chief_is_accepted_and_unknown_roles_are_not(self):
        db.init_db(self.path)
        with db.reader(self.path) as c:
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
        self.assertIn("'chief'", sql)
        c = sqlite3.connect(self.path)
        try:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("UPDATE users SET role='chief' WHERE id=2")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE users SET role='commander' WHERE id=3")
        finally:
            c.close()

    def test_child_tables_still_point_at_users_and_nothing_temporary_survives(self):
        db.init_db(self.path)
        with db.reader(self.path) as c:
            fks = {(t, r[2]) for t in ("sessions", "instructor_platoons", "allowed_emails", "audit_log")
                   for r in c.execute(f"PRAGMA foreign_key_list({t})")}
            sql = " ".join(r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
            self.assertEqual(c.execute("SELECT count(*) FROM sessions").fetchone()[0], 3)
        self.assertIn(("sessions", "users"), fks)
        self.assertIn(("audit_log", "users"), fks)
        self.assertNotIn("users_v4", sql)

    def test_idempotent_and_a_fresh_database_has_the_same_shape(self):
        db.init_db(self.path)
        self.assertEqual(db.init_db(self.path), 4)
        fresh = os.path.join(self._tmp.name, "fresh.db")
        db.init_db(fresh)

        def shape(p):
            with db.reader(p) as c:
                return sorted((r[0], tuple(x[1] for x in c.execute(f"PRAGMA table_info({r[0]})")))
                              for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        self.assertEqual(shape(fresh), shape(self.path))

    def test_failed_upgrade_rolls_back_to_v3(self):
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES('orphan',999,'2099-01-01T00:00:00Z')")
        c.commit()
        c.close()
        with self.assertRaises(StorageError):
            db.init_db(self.path)
        self.assertEqual(db.user_version(self.path), 3)


if __name__ == "__main__":
    unittest.main()
