"""Schema v6 -> v7: the approval step of accommodations. Existing 'cadet' rows become 'pending', every other row
'approved'; nothing else changes and no row is lost."""
import os
import sqlite3
import tempfile
import unittest

from baror.errors import StorageError
from baror.storage import db
from tests.test_migration_v2 import build_v1
from tests.test_migration_v3 import person_view

_INS = ("INSERT INTO accommodations(user_id,kind,scope,start_date,end_date,source,created_by,note) "
        "VALUES(?,'relief','run','2026-11-01','2026-11-30',?,?,?)")


class V6ToV7(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "baror.db")
        build_v1(self.path)
        db.init_db(self.path, target_version=6)
        with db.transaction(self.path) as c:
            for source, note in (("cadet", "a"), ("instructor", "b"), ("chief", "c"), ("admin", "d"), ("cadet", "e")):
                c.execute(_INS, (2, source, 3, note))
        self.before = person_view(self.path)

    def approvals(self):
        with db.reader(self.path) as c:
            return [tuple(r) for r in c.execute("SELECT source, approval, decided_at, decided_by FROM accommodations ORDER BY id")]

    def test_upgrade_marks_cadet_rows_pending_and_all_others_approved_and_keeps_every_row(self):
        self.assertEqual(db.user_version(self.path), 6)
        self.assertEqual(db.init_db(self.path, target_version=7), 7)
        self.assertEqual(self.approvals(), [("cadet", "pending", None, None), ("instructor", "approved", None, None),
                                            ("chief", "approved", None, None), ("admin", "approved", None, None),
                                            ("cadet", "pending", None, None)])
        self.assertEqual(person_view(self.path), self.before)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM accommodations").fetchone()[0], 5)
            fks = {(r[2], r[3]) for r in c.execute("PRAGMA foreign_key_list(accommodations)")}
            idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn(("users", "decided_by"), fks)
        self.assertIn("idx_accommodations_approval", idx)
        self.assertEqual(db.integrity_check(self.path), [])

    def test_idempotent_and_a_second_run_does_not_touch_decisions(self):
        db.init_db(self.path)
        with db.transaction(self.path) as c:
            c.execute("UPDATE accommodations SET approval='approved', decided_at='2026-11-03T08:00:00Z', decided_by=3 WHERE id=1")
        self.assertEqual(db.init_db(self.path), db.LATEST_VERSION)
        self.assertEqual(self.approvals()[0], ("cadet", "approved", "2026-11-03T08:00:00Z", 3))

    def test_a_fresh_database_has_the_same_shape(self):
        db.init_db(self.path)
        fresh = os.path.join(self._tmp.name, "fresh.db")
        db.init_db(fresh)

        def shape(p):
            with db.reader(p) as c:
                return sorted((r[0], tuple(x[1] for x in c.execute(f"PRAGMA table_info({r[0]})")),
                               tuple(sorted(y[2] for y in c.execute(f"PRAGMA foreign_key_list({r[0]})"))))
                              for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        self.assertEqual(shape(fresh), shape(self.path))

    def test_the_checks_are_a_backstop(self):
        db.init_db(self.path)
        conn = db.connect(self.path)
        try:
            for sql in ("UPDATE accommodations SET approval='maybe' WHERE id=1",
                        "UPDATE accommodations SET approval=NULL WHERE id=1",
                        "UPDATE accommodations SET decided_by=3 WHERE id=1",                      # a decider without a time
                        "UPDATE accommodations SET decided_at='2026-11-03T08:00:00Z' WHERE id=1",  # a decision time while pending
                        "UPDATE accommodations SET decided_by=999 WHERE id=1"):
                with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                    conn.execute(sql)
            conn.execute("INSERT INTO accommodations(user_id,kind,scope,start_date,end_date,source) "
                         "VALUES(2,'relief','run','2026-11-01','2026-11-30','chief')")
            self.assertEqual(conn.execute("SELECT approval FROM accommodations ORDER BY id DESC").fetchone()[0], "approved",
                             "the default for an operator insert is approved")
        finally:
            conn.close()

    def test_deleting_the_decider_keeps_the_decision(self):
        db.init_db(self.path)
        with db.transaction(self.path) as c:
            c.execute("UPDATE accommodations SET approval='rejected', decided_at='2026-11-03T08:00:00Z', decided_by=3 WHERE id=1")
            c.execute("DELETE FROM users WHERE id=3")
        row = self.approvals()[0]
        self.assertEqual((row[1], row[3]), ("rejected", None))

    def test_a_failed_upgrade_leaves_v6(self):
        c = sqlite3.connect(self.path)
        c.execute("ALTER TABLE accommodations ADD COLUMN approval TEXT")      # a clash: ADD COLUMN of v7 must fail as a whole
        c.commit()
        c.close()
        with self.assertRaises(StorageError):
            db.init_db(self.path)
        self.assertEqual(db.user_version(self.path), 6)
        self.assertEqual(person_view(self.path), self.before)


if __name__ == "__main__":
    unittest.main()
