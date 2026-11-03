"""Schema v7 -> v8: stored reports, delivery bookkeeping and the automatic-mail switch (OFF by default)."""
import os
import sqlite3
import tempfile
import unittest

from baror.storage import db
from tests.test_migration_v2 import build_v1


class V7ToV8(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "baror.db")
        build_v1(self.path)
        db.init_db(self.path, target_version=7)

    def test_upgrade_adds_the_tables_with_the_switch_off_and_keeps_existing_data(self):
        with db.reader(self.path) as c:
            users = c.execute("SELECT count(*) FROM users").fetchone()[0]
        self.assertEqual(db.init_db(self.path), 8)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], users)
            self.assertEqual(tuple(c.execute("SELECT id, mail_enabled, changed_at, changed_by FROM report_settings").fetchone()),
                             (1, 0, None, None))
            self.assertEqual(c.execute("SELECT count(*) FROM reports").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM report_deliveries").fetchone()[0], 0)
        self.assertEqual(db.integrity_check(self.path), [])

    def test_idempotent_and_a_second_run_does_not_reset_the_switch(self):
        db.init_db(self.path)
        with db.transaction(self.path) as c:
            c.execute("UPDATE report_settings SET mail_enabled=1")
        self.assertEqual(db.init_db(self.path), 8)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT mail_enabled FROM report_settings").fetchone()[0], 1)

    def test_a_fresh_database_has_the_same_shape_and_the_switch_is_off(self):
        db.init_db(self.path)
        fresh = os.path.join(self._tmp.name, "fresh.db")
        db.init_db(fresh)

        def shape(p):
            with db.reader(p) as c:
                return sorted((r[0], tuple(x[1] for x in c.execute(f"PRAGMA table_info({r[0]})")))
                              for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        self.assertEqual(shape(fresh), shape(self.path))
        with db.reader(fresh) as c:
            self.assertEqual(c.execute("SELECT mail_enabled FROM report_settings").fetchone()[0], 0)

    def test_the_checks_are_a_backstop(self):
        db.init_db(self.path)
        conn = db.connect(self.path)
        try:
            good = ("INSERT INTO reports(period_type,period_start,period_end,pdf) VALUES('weekly','2026-10-30','2026-11-05',x'25')")
            conn.execute(good)
            for sql in (good,                                                    # one report per type and start
                        "INSERT INTO reports(period_type,period_start,period_end,pdf) VALUES('daily','2026-10-30','2026-11-05',x'25')",
                        "INSERT INTO reports(period_type,period_start,period_end,pdf) VALUES('weekly','2026-02-31','2026-11-05',x'25')",
                        "INSERT INTO reports(period_type,period_start,period_end,pdf) VALUES('weekly','2026-11-05','2026-10-30',x'25')",
                        "INSERT INTO reports(period_type,period_start,period_end,pdf) VALUES('monthly','2026-10-01','2026-10-29',x'')",
                        "INSERT INTO report_settings(id) VALUES(2)",
                        "UPDATE report_settings SET mail_enabled=2"):
                with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                    conn.execute(sql)
        finally:
            conn.close()

    def test_deleting_a_report_or_an_account_removes_its_delivery_rows(self):
        db.init_db(self.path)
        conn = db.connect(self.path)
        try:
            uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
            conn.execute("INSERT INTO reports(period_type,period_start,period_end,pdf) VALUES('weekly','2026-10-30','2026-11-05',x'25')")
            conn.execute("INSERT INTO report_deliveries(report_id,user_id) VALUES(1,?)", (uid,))
            conn.execute("DELETE FROM reports WHERE id=1")
            self.assertEqual(conn.execute("SELECT count(*) FROM report_deliveries").fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
