"""Schema v1 -> v2 upgrade (each step is run with target_version=2; v3 has its own module) of a populated database (table rebuild per SQLite's documented 12-step procedure)."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from baror.auth import sessions
from baror.errors import StorageError
from baror.storage import db, repo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUTURE = "2099-01-01T00:00:00Z"


def build_v1(path):
    """A genuine v1 database (the released SCHEMA_V1 text) with users, results and sessions in it."""
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(db.SCHEMA_V1)
    c.execute("PRAGMA user_version=1")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("INSERT INTO users(email,name,gender,is_km,pw_hash,must_change_pw,role,failed_logins,locked_until) "
              "VALUES('boss@example.com','Boss','F',0,'scrypt$boss',0,'admin',0,NULL)")
    c.execute("INSERT INTO users(email,name,gender,is_km,pw_hash,must_change_pw,role,failed_logins,locked_until) "
              "VALUES('dana@example.com','Dana','F',1,'scrypt$dana',1,'cadet',3,'2026-01-01T00:00:00Z')")
    c.execute("INSERT INTO users(email,name,gender,is_km,pw_hash) VALUES('omer@example.com','Omer','M',0,'scrypt$omer')")
    for uid, d, r, p in [(2, "2026-01-01", 9000, 30), (2, "2026-02-01", None, 33), (3, "2026-01-05", 8700, None)]:
        c.execute("INSERT INTO results(user_id,test_date,run_tenths,pushups) VALUES(?,?,?,?)", (uid, d, r, p))
    for h, uid in [("tok-admin", 1), ("tok-dana", 2), ("tok-omer", 3)]:
        c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (h, uid, FUTURE))
    c.commit(); c.close()


def snapshot(path):
    c = sqlite3.connect(path)
    try:
        users = c.execute("SELECT id,email,name,gender,is_km,pw_hash,must_change_pw,role,failed_logins,locked_until,created_at "
                          "FROM users ORDER BY id").fetchall()
        results = c.execute("SELECT id,user_id,test_date,run_tenths,pushups,created_at FROM results ORDER BY id").fetchall()
        sess = c.execute("SELECT token_hash,user_id,expires_at,revoked FROM sessions ORDER BY token_hash").fetchall()
        return users, results, sess
    finally:
        c.close()


class V1ToV2(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "baror.db")
        build_v1(self.path)
        self.before = snapshot(self.path)

    def test_upgrade_keeps_every_row_byte_for_byte(self):
        self.assertEqual(db.user_version(self.path), 1)
        self.assertEqual(db.init_db(self.path, target_version=2), 2)
        self.assertEqual(snapshot(self.path), self.before)
        self.assertEqual(len(self.before[0]), 3); self.assertEqual(len(self.before[1]), 3); self.assertEqual(len(self.before[2]), 3)
        self.assertEqual(db.integrity_check(self.path), [])

    def test_new_columns_default_for_old_rows(self):
        db.init_db(self.path, target_version=2)
        with db.reader(self.path) as c:
            rows = c.execute("SELECT active,platoon_id FROM users").fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [(1, None)] * 3)
        db.init_db(self.path)       # the repository speaks the latest schema; the data survives all the later steps too
        self.assertEqual(repo.get_user(self.path, 2)["active"], 1)

    def test_child_foreign_keys_still_point_at_users_and_cascade_still_works(self):
        db.init_db(self.path, target_version=2)
        with db.reader(self.path) as c:
            sql = {r[0]: r[1] for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1, "connect() must give FK enforcement")
            fks = {(t, r[2]) for t in ("sessions", "results") for r in c.execute(f"PRAGMA foreign_key_list({t})")}
        self.assertEqual(fks, {("sessions", "users"), ("results", "users")})
        self.assertNotIn("users_v2", " ".join(sql.values()), "the temporary table name must not survive anywhere")
        with db.transaction(self.path) as c:   # raw SQL: the v3 repository does not apply to a v2 file
            c.execute("DELETE FROM users WHERE id=2")
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM results WHERE user_id=2").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM sessions WHERE user_id=2").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM results").fetchone()[0], 1, "other users untouched")
            self.assertEqual(c.execute("SELECT count(*) FROM sessions").fetchone()[0], 2)

    def test_old_sessions_still_resolve_after_upgrade(self):
        db.init_db(self.path)       # the session code reads the latest schema (title, platoon)
        tok = sessions.issue(self.path, 2, 14)
        self.assertEqual(sessions.resolve(self.path, tok)["id"], 2)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT user_id FROM sessions WHERE token_hash='tok-dana'").fetchone()[0], 2)

    def test_role_check_now_allows_instructor_and_still_rejects_unknown(self):
        db.init_db(self.path, target_version=2)
        c = sqlite3.connect(self.path)
        try:
            c.execute("INSERT INTO users(email,name,gender,is_km,pw_hash,role) VALUES('ins@example.com','I','M',0,'h','instructor')")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO users(email,name,gender,is_km,pw_hash,role) VALUES('x@example.com','X','M',0,'h','root')")
        finally:
            c.close()

    def test_why_foreign_keys_must_be_off_during_the_rebuild(self):
        """Evidence for the design: running the SAME script with foreign_keys=ON destroys the child rows
        (DROP TABLE users does an implicit DELETE FROM users, which cascades). init_db must therefore turn FKs off."""
        c = sqlite3.connect(self.path, isolation_level=None)
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("BEGIN")
        for stmt in db._split(db.SCHEMA_V2):
            c.execute(stmt)
        n_results = c.execute("SELECT count(*) FROM results").fetchone()[0]
        n_sessions = c.execute("SELECT count(*) FROM sessions").fetchone()[0]
        c.execute("ROLLBACK"); c.close()
        self.assertEqual((n_results, n_sessions), (0, 0), "naive rebuild with FK ON wipes results and sessions")
        self.assertEqual(snapshot(self.path), self.before, "...and the rollback restored them (nothing persisted)")

    def test_failed_migration_rolls_back_completely_and_reports_the_violation(self):
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES('orphan',999,?)", (FUTURE,))
        c.commit(); c.close()
        before = snapshot(self.path)
        with self.assertRaises(StorageError) as cm:
            db.init_db(self.path, target_version=2)
        self.assertIn("foreign key", str(cm.exception).lower())
        self.assertEqual(db.user_version(self.path), 1, "failed upgrade must leave the database at v1")
        self.assertEqual(snapshot(self.path), before, "failed upgrade must change nothing")
        with db.reader(self.path) as c:
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("platoons", names); self.assertNotIn("users_v2", names)

    def test_init_is_idempotent_at_v2(self):
        db.init_db(self.path, target_version=2)
        again = snapshot(self.path)
        self.assertEqual(db.init_db(self.path, target_version=2), 2)
        self.assertEqual(snapshot(self.path), again)

    def test_concurrent_upgrade_threads_and_processes(self):
        results, errors = [], []
        barrier = threading.Barrier(4)

        def up():
            barrier.wait()
            try:
                results.append(db.init_db(self.path, target_version=2))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
        ts = [threading.Thread(target=up) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual((errors, results), ([], [2] * 4))
        self.assertEqual(snapshot(self.path), self.before)

    def test_concurrent_upgrade_processes(self):
        procs = [subprocess.Popen([sys.executable, "-m", "tests.workers", "init_only", self.path, "2"], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(5)]
        outs = [p.communicate(timeout=120)[0].strip() for p in procs]
        self.assertEqual(outs, ["VERSION 2"] * 5, outs)
        self.assertEqual(snapshot(self.path), self.before)
        self.assertEqual(db.integrity_check(self.path), [])


class FreshV2(unittest.TestCase):
    def test_fresh_database_runs_all_migrations_to_the_same_shape_as_an_upgraded_one(self):
        with tempfile.TemporaryDirectory() as d:
            fresh, up = os.path.join(d, "f.db"), os.path.join(d, "u.db")
            db.init_db(fresh)
            build_v1(up); db.init_db(up)

            def shape(p):
                with db.reader(p) as c:
                    return sorted((r[0], tuple(x[1] for x in c.execute(f"PRAGMA table_info({r[0]})")))
                                  for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
            self.assertEqual(shape(fresh), shape(up))
            names = {n for n, _ in shape(fresh)}
            self.assertTrue({"users", "results", "sessions", "platoons", "instructor_platoons", "cadet_change_requests", "signup_events",
                                 "audit_log"} <= names)
            self.assertNotIn("allowed_emails", names)       # dropped in v9: there is no registration gate any more


if __name__ == "__main__":
    unittest.main()
