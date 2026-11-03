"""Schema v2 -> v3 (and v1 -> v3): results move from user_id to a random pseudonym, data intact (ADR-0002)."""
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from baror.errors import StorageError
from baror.storage import db
from tests.test_migration_v2 import FUTURE, ROOT, build_v1

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def person_view(path):
    """Everything attributable to a PERSON (by e-mail), independent of ids and pseudonyms: survives the migration."""
    c = sqlite3.connect(path)
    try:
        has_ps = any(r[1] == "pseudonym" for r in c.execute("PRAGMA table_info(users)"))
        key = "u.pseudonym=r.pseudonym" if has_ps else "u.id=r.user_id"
        users = c.execute("SELECT email,name,gender,is_km,pw_hash,must_change_pw,role,failed_logins,locked_until,created_at "
                          "FROM users ORDER BY email").fetchall()
        results = c.execute(f"SELECT u.email,r.id,r.test_date,r.run_tenths,r.pushups,r.created_at FROM results r "
                            f"JOIN users u ON {key} ORDER BY r.id").fetchall()
        sess = c.execute("SELECT s.token_hash,u.email,s.expires_at,s.revoked FROM sessions s JOIN users u ON u.id=s.user_id "
                         "ORDER BY s.token_hash").fetchall()
        return users, results, sess
    finally:
        c.close()


class V2ToV3(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "baror.db")
        build_v1(self.path)
        db.init_db(self.path, target_version=2)
        c = sqlite3.connect(self.path)
        c.execute("INSERT INTO platoons(name) VALUES('A')")
        c.execute("UPDATE users SET platoon_id=1 WHERE id IN (2,3)")
        c.commit(); c.close()
        self.before = person_view(self.path)

    def test_upgrade_keeps_every_person_row_and_each_result_with_its_owner(self):
        self.assertEqual(db.user_version(self.path), 2)
        self.assertEqual(db.init_db(self.path), 3)
        self.assertEqual(person_view(self.path), self.before)
        self.assertEqual((len(self.before[0]), len(self.before[1]), len(self.before[2])), (3, 3, 3))
        self.assertEqual(db.integrity_check(self.path), [])

    def test_ids_platoons_and_sessions_are_preserved(self):
        db.init_db(self.path)
        with db.reader(self.path) as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT id,email,platoon_id,active FROM users ORDER BY id")],
                             [(1, "boss@example.com", None, 1), (2, "dana@example.com", 1, 1), (3, "omer@example.com", 1, 1)])
            self.assertEqual(c.execute("SELECT count(*) FROM sessions").fetchone()[0], 3)
            self.assertIsNone(c.execute("SELECT course_end FROM platoons").fetchone()[0])

    def test_pseudonyms_are_unique_random_32_hex_and_one_per_user(self):
        db.init_db(self.path)
        with db.reader(self.path) as c:
            ps = [r[0] for r in c.execute("SELECT pseudonym FROM users ORDER BY id")]
            parents = [r[0] for r in c.execute("SELECT pseudonym FROM pseudonyms")]
        self.assertEqual(len(set(ps)), 3)
        self.assertTrue(all(HEX32.match(p) for p in ps))
        self.assertEqual(sorted(ps), sorted(parents))
        self.assertNotEqual(ps, ["00000000000000000000000000000001", "00000000000000000000000000000002", "00000000000000000000000000000003"])

    def test_foreign_keys_after_upgrade(self):
        db.init_db(self.path)
        with db.reader(self.path) as c:
            fks = {(t, r[2]) for t in ("sessions", "results", "users", "instructor_platoons", "allowed_emails", "audit_log")
                   for r in c.execute(f"PRAGMA foreign_key_list({t})")}
            sql = " ".join(r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        self.assertIn(("results", "pseudonyms"), fks)
        self.assertNotIn(("results", "users"), fks, "the whole point: results do not depend on the identity row")
        self.assertIn(("users", "pseudonyms"), fks)
        self.assertIn(("sessions", "users"), fks)
        self.assertNotIn("users_v3", sql); self.assertNotIn("results_v3", sql); self.assertNotIn("_pmap", sql)

    def test_why_foreign_keys_must_be_off_during_the_rebuild(self):
        c = sqlite3.connect(self.path, isolation_level=None)
        c.execute("PRAGMA foreign_keys=ON"); c.execute("BEGIN")
        for stmt in db._split(db.SCHEMA_V3):
            c.execute(stmt)
        n_sessions = c.execute("SELECT count(*) FROM sessions").fetchone()[0]
        c.execute("ROLLBACK"); c.close()
        self.assertEqual(n_sessions, 0, "naive rebuild with FK ON wipes the sessions")
        self.assertEqual(person_view(self.path), self.before)

    def test_failed_migration_rolls_back_completely(self):
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES('orphan',999,?)", (FUTURE,))
        c.commit(); c.close()
        before = person_view(self.path)
        with self.assertRaises(StorageError):
            db.init_db(self.path)
        self.assertEqual(db.user_version(self.path), 2)
        self.assertEqual(person_view(self.path), before)
        with db.reader(self.path) as c:
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("pseudonyms", names); self.assertNotIn("users_v3", names); self.assertNotIn("_pmap", names)

    def test_an_orphan_result_aborts_the_migration_instead_of_being_dropped(self):
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO results(user_id,test_date,pushups) VALUES(999,'2026-03-03',5)")
        c.commit(); c.close()
        with self.assertRaises(StorageError) as cm:
            db.init_db(self.path)
        self.assertIn("row counts changed", str(cm.exception))
        self.assertEqual(db.user_version(self.path), 2)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM results").fetchone()[0], 4)

    def test_idempotent(self):
        db.init_db(self.path)
        again = person_view(self.path)
        with db.reader(self.path) as c:
            ps = c.execute("SELECT pseudonym FROM users ORDER BY id").fetchall()
        self.assertEqual(db.init_db(self.path), 3)
        self.assertEqual(person_view(self.path), again)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT pseudonym FROM users ORDER BY id").fetchall(), ps, "pseudonyms never change")

    def test_concurrent_upgrade_threads_and_processes(self):
        results, errors = [], []
        barrier = threading.Barrier(4)

        def up():
            barrier.wait()
            try:
                results.append(db.init_db(self.path))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
        ts = [threading.Thread(target=up) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual((errors, results), ([], [3] * 4))
        procs = [subprocess.Popen([sys.executable, "-m", "tests.workers", "init_only", self.path], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(5)]
        self.assertEqual([p.communicate(timeout=120)[0].strip() for p in procs], ["VERSION 3"] * 5)
        self.assertEqual(person_view(self.path), self.before)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(DISTINCT pseudonym) FROM users").fetchone()[0], 3)


class V1ToV3(unittest.TestCase):
    def test_straight_from_v1_keeps_people_and_results(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "baror.db")
            build_v1(path)
            before = person_view(path)
            self.assertEqual(db.init_db(path), 3)
            self.assertEqual(person_view(path), before)
            self.assertEqual(db.integrity_check(path), [])


if __name__ == "__main__":
    unittest.main()
