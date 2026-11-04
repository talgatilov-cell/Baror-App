"""Schema v8 -> v9: titles, one platoon per instructor (is_mm), join epoch, signup_events, change requests, no allowlist.

Local SQLite note: these tests ran against the interpreter's SQLite (see ``SqliteVersion``). The production image
(Debian bookworm) has 3.40.1 and Docker could not be run here, so the two constructs the migration relies on that
differ between builds are exercised explicitly: ``ALTER TABLE ... ADD COLUMN ... CHECK(...)`` and the partial UNIQUE
indexes. Both exist since SQLite 3.8 / 3.9 and 3.40 is well past them; the suite must be run once inside the image."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from baror.errors import StorageError
from baror.storage import db


def add_user(c, n, role="cadet", platoon=None, active=1):
    ps = f"{n:032x}"
    c.execute("INSERT INTO pseudonyms(pseudonym) VALUES(?)", (ps,))
    c.execute("INSERT INTO users(id,email,name,gender,pw_hash,role,platoon_id,pseudonym,active) VALUES(?,?,?,?,?,?,?,?,?)",
              (n, f"u{n}@x.com", f"Name {n}", "M", "h", role, platoon, ps, active))
    return n


def build_v8(path, conn_setup=None):
    """A v8 database with a bit of everything. Users: 1,2 instructors, 3 instructor with a single grant, 4 admin (granted),
    5 chief (granted), 6 cadet in platoon 1, 7 cadet without platoon, 8 deactivated instructor."""
    db.init_db(path, target_version=8)
    c = db.connect(path)
    try:
        c.execute("INSERT INTO platoons(name) VALUES('P1'),('P2'),('P3')")
        for n, role in ((1, "instructor"), (2, "instructor"), (3, "instructor"), (4, "admin"), (5, "chief"), (7, "cadet")):
            add_user(c, n, role)
        add_user(c, 6, "cadet", platoon=1)
        add_user(c, 8, "instructor", active=0)
        c.execute("INSERT INTO instructor_platoons(instructor_id,platoon_id,granted_by,granted_at) VALUES"
                  "(1,3,4,'2026-03-01T00:00:00Z'),(1,2,4,'2026-02-01T00:00:00Z'),(1,1,4,'2026-02-01T00:00:00Z'),"   # earliest, tie -> platoon 1
                  "(2,2,NULL,'2026-01-01T00:00:00Z'),(3,3,NULL,'2026-01-01T00:00:00Z'),"
                  "(4,1,NULL,'2026-01-01T00:00:00Z'),(5,2,NULL,'2026-01-01T00:00:00Z'),(8,1,NULL,'2026-01-01T00:00:00Z')")
        c.execute("INSERT INTO allowed_emails(email,role) VALUES('a@x.com','cadet'),('b@x.com','instructor')")
        c.execute("INSERT INTO results(pseudonym,test_date,run_tenths,pushups) VALUES(?, '2026-01-01', 900, 30)", (f"{6:032x}",))
        c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES('t6',6,'2099-01-01T00:00:00Z')")
        if conn_setup:
            conn_setup(c)
    finally:
        c.close()


def snapshot(path, tables):
    with db.reader(path) as c:
        return {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1,2")] for t in tables}


class SqliteVersion(unittest.TestCase):
    def test_record_which_sqlite_ran_these_tests(self):
        # not an assertion about the version: it makes the log say what the local result is worth (see module docstring)
        print(f"[test_migration_v9] SQLite {sqlite3.sqlite_version}")
        self.assertGreaterEqual(sqlite3.sqlite_version_info, (3, 35, 0))


class V8ToV9(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "baror.db")
        build_v8(self.path)
        self.before = snapshot(self.path, ("users", "results", "sessions", "platoons", "pseudonyms"))

    def up(self):
        self.assertEqual(db.init_db(self.path), 9)

    def q(self, sql, args=()):
        with db.reader(self.path) as c:
            return [tuple(r) for r in c.execute(sql, args)]

    def test_existing_rows_keep_their_role_platoon_and_data(self):
        self.up()
        after = snapshot(self.path, ("results", "sessions", "pseudonyms"))
        for t in ("results", "sessions", "pseudonyms"):
            self.assertEqual(after[t], self.before[t], t)
        users_before = {r[0]: r for r in self.before["users"]}
        cols = [r[1] for r in self.q("PRAGMA table_info(users)")]
        self.assertEqual(cols.count("title"), 1)
        for r in self.q("SELECT * FROM users"):
            d = dict(zip(cols, r))
            old = users_before[d["id"]]
            self.assertEqual(tuple(v for k, v in d.items() if k != "title"), old, d["id"])
            self.assertIsNone(d["title"], "legacy staff have no title")
        self.assertEqual(self.q("SELECT id,name,join_epoch FROM platoons ORDER BY id"), [(1, "P1", 0), (2, "P2", 0), (3, "P3", 0)])
        self.assertEqual(db.integrity_check(self.path), [])

    def test_one_grant_per_instructor_the_earliest_and_lowest_platoon_wins(self):
        self.up()
        self.assertEqual(self.q("SELECT instructor_id,platoon_id,is_mm,granted_by,granted_at FROM instructor_platoons ORDER BY 1"),
                         [(1, 1, 0, 4, "2026-02-01T00:00:00Z"),            # three grants: earliest date, tie -> platoon 1
                          (2, 2, 0, None, "2026-01-01T00:00:00Z"),
                          (3, 3, 0, None, "2026-01-01T00:00:00Z"),
                          (8, 1, 0, None, "2026-01-01T00:00:00Z")])       # a deactivated instructor keeps theirs
        pk = [r for r in self.q("PRAGMA table_info(instructor_platoons)") if r[5]]
        self.assertEqual([r[1] for r in pk], ["instructor_id"])

    def test_every_dropped_grant_leaves_an_audit_row_with_ids_only(self):
        self.up()
        rows = self.q("SELECT actor_id,action,target_user_id,platoon_id,outcome FROM audit_log "
                      "WHERE action='migrate.v9_grant_dropped' ORDER BY target_user_id, platoon_id")
        self.assertEqual(rows, [(None, "migrate.v9_grant_dropped", 1, 2, "ok"),     # instructor 1: platoons 2 and 3 dropped
                                (None, "migrate.v9_grant_dropped", 1, 3, "ok"),
                                (None, "migrate.v9_grant_dropped", 4, 1, "ok"),     # the admin's grant
                                (None, "migrate.v9_grant_dropped", 5, 2, "ok")])    # the chief's grant
        self.assertEqual(len(self.q("SELECT 1 FROM audit_log")), 4, "nothing else is written")

    def test_the_allowlist_is_gone(self):
        self.assertEqual(self.q("SELECT count(*) FROM allowed_emails")[0][0], 2)
        self.up()
        self.assertEqual(self.q("SELECT name FROM sqlite_master WHERE name='allowed_emails'"), [])

    def test_new_tables_and_indexes_exist_and_start_empty(self):
        self.up()
        self.assertEqual(self.q("SELECT count(*) FROM signup_events")[0][0], 0)
        self.assertEqual(self.q("SELECT count(*) FROM cadet_change_requests")[0][0], 0)
        idx = {r[0] for r in self.q("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({"uq_users_chief_title", "idx_signup_events_ts", "idx_ccr_pending", "uq_ccr_open",
                         "idx_instructor_platoons_platoon", "uq_one_mm_per_platoon"} <= idx)

    def test_idempotent(self):
        self.up()
        snap = snapshot(self.path, ("users", "instructor_platoons", "audit_log"))
        self.up()
        self.assertEqual(snapshot(self.path, ("users", "instructor_platoons", "audit_log")), snap)

    def test_fresh_database_has_the_same_shape_as_an_upgraded_one(self):
        self.up()
        fresh = os.path.join(self._tmp.name, "fresh.db")
        db.init_db(fresh)

        def shape(p):
            with db.reader(p) as c:
                return sorted((r[0], tuple(x[1] for x in c.execute(f"PRAGMA table_info({r[0]})")))
                              for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))

        def indexes(p):
            with db.reader(p) as c:
                return sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"))
        self.assertEqual(shape(fresh), shape(self.path))
        self.assertEqual(indexes(fresh), indexes(self.path))

    def test_platoons_already_over_the_limits_are_left_alone(self):
        def seven_in_one(c):
            for n in range(20, 27):
                add_user(c, n, "instructor")
                c.execute("INSERT INTO instructor_platoons(instructor_id,platoon_id) VALUES(?,1)", (n,))
        path = os.path.join(self._tmp.name, "over.db")
        build_v8(path, seven_in_one)
        db.init_db(path)
        with db.reader(path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM instructor_platoons WHERE platoon_id=1").fetchone()[0], 7 + 2)
            self.assertEqual(c.execute("SELECT count(*) FROM instructor_platoons WHERE is_mm=1").fetchone()[0], 0)

    def test_a_failure_in_the_middle_leaves_a_complete_v8(self):
        broken = db.SCHEMA_V9 + ";\nSELECT * FROM no_such_table"
        before = snapshot(self.path, ("users", "instructor_platoons", "audit_log", "allowed_emails"))
        with mock.patch.dict(db.MIGRATIONS, {9: broken}):
            with self.assertRaises(StorageError):
                db.init_db(self.path)
        self.assertEqual(db.user_version(self.path), 8)
        self.assertEqual(snapshot(self.path, ("users", "instructor_platoons", "audit_log", "allowed_emails")), before)
        names = {r[0] for r in self.q("SELECT name FROM sqlite_master")}
        self.assertTrue({"allowed_emails", "instructor_platoons"} <= names)
        self.assertFalse({"signup_events", "cadet_change_requests", "instructor_platoons_v9", "uq_one_mm_per_platoon"} & names)
        self.assertNotIn("title", [r[1] for r in self.q("PRAGMA table_info(users)")])
        self.up()                                                      # and the real migration still works afterwards
        self.assertEqual(db.integrity_check(self.path), [])


class ChecksAreABackstop(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "baror.db")
        db.init_db(self.path)
        self.c = db.connect(self.path)
        self.addCleanup(self.c.close)
        self.c.execute("INSERT INTO platoons(name) VALUES('P1'),('P2')")
        for n, role in ((1, "chief"), (2, "chief"), (3, "instructor"), (4, "instructor"), (5, "cadet"), (6, "chief")):
            add_user(self.c, n, role)

    def refuses(self, sql, args=()):
        with self.assertRaises(sqlite3.IntegrityError, msg=sql):
            self.c.execute(sql, args)

    def test_title_must_fit_the_role(self):
        for uid, title in ((1, "mm"), (1, "cmdr"), (3, "mp"), (3, "smp"), (5, "mm"), (5, "mp"), (3, "boss"), (1, "")):
            self.refuses("UPDATE users SET title=? WHERE id=?", (title, uid))
        self.c.execute("UPDATE users SET title='mp' WHERE id=1")
        self.c.execute("UPDATE users SET title='cmdr' WHERE id=3")
        self.refuses("UPDATE users SET role='cadet' WHERE id=1")          # the title would no longer fit
        self.c.execute("UPDATE users SET role='cadet', title=NULL WHERE id=1")

    def test_a_chief_title_is_unique_among_active_chiefs_only(self):
        self.c.execute("UPDATE users SET title='mp' WHERE id=1")
        self.refuses("UPDATE users SET title='mp' WHERE id=2")
        self.c.execute("UPDATE users SET active=0 WHERE id=1")             # a deactivated chief frees the title
        self.c.execute("UPDATE users SET title='mp' WHERE id=2")
        self.refuses("UPDATE users SET active=1 WHERE id=1")               # ...and cannot come back over it
        self.c.execute("UPDATE users SET title='smp' WHERE id=6")           # NULL titles are not compared

    def test_one_mm_per_platoon_is_a_database_rule(self):
        self.c.execute("INSERT INTO instructor_platoons(instructor_id,platoon_id,is_mm) VALUES(3,1,1)")
        self.refuses("INSERT INTO instructor_platoons(instructor_id,platoon_id,is_mm) VALUES(4,1,1)")
        self.c.execute("INSERT INTO instructor_platoons(instructor_id,platoon_id,is_mm) VALUES(4,1,0)")      # a cmdr is fine
        self.refuses("UPDATE instructor_platoons SET is_mm=1 WHERE instructor_id=4")
        self.c.execute("INSERT INTO instructor_platoons(instructor_id,platoon_id,is_mm) VALUES(5,2,1)")      # other platoon
        self.refuses("INSERT INTO instructor_platoons(instructor_id,platoon_id,is_mm) VALUES(6,2,2)")        # is_mm in (0,1)

    def test_an_instructor_has_one_row(self):
        self.c.execute("INSERT INTO instructor_platoons(instructor_id,platoon_id) VALUES(3,1)")
        self.refuses("INSERT INTO instructor_platoons(instructor_id,platoon_id) VALUES(3,2)")

    def test_the_join_epoch_cannot_go_negative(self):
        self.refuses("UPDATE platoons SET join_epoch=-1 WHERE id=1")
        self.c.execute("UPDATE platoons SET join_epoch=join_epoch+1 WHERE id=1")

    def test_signup_events_default_to_now(self):
        self.c.execute("INSERT INTO signup_events DEFAULT VALUES")
        ts = self.c.execute("SELECT ts FROM signup_events").fetchone()[0]
        self.assertRegex(ts, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    REQ = "INSERT INTO cadet_change_requests(kind,requested_by,target_user_id,platoon_id,new_name,new_gender,new_is_km,new_platoon_id%s) VALUES(?,3,5,?,?,?,?,?%s)"

    def test_change_request_shapes(self):
        ok = [("add_conflict", 1, None, None, None, None), ("delete", None, None, None, None, None),
              ("edit", None, "New", None, None, None), ("edit", None, None, "F", None, None),
              ("edit", None, None, None, 1, None), ("edit", None, None, None, None, 2),
              ("nominate_cmdr", 1, None, None, None, None)]
        for i, row in enumerate(ok):
            self.c.execute("DELETE FROM cadet_change_requests")
            self.c.execute(self.REQ % ("", ""), row)
        bad = [("edit", None, None, None, None, None),                     # an edit changes something
               ("delete", None, "x", None, None, None), ("add_conflict", 1, "x", None, None, None),
               ("add_conflict", None, None, None, None, None),            # needs the destination platoon
               ("nominate_cmdr", None, None, None, None, None),           # needs a platoon
               ("nominate_cmdr", 1, "x", None, None, None),               # and no new_* values
               ("edit", None, "", None, None, None), ("edit", None, "x" * 81, None, None, None),
               ("edit", None, None, "X", None, None), ("edit", None, None, None, 2, None),
               ("merge", 1, None, None, None, None)]
        for row in bad:
            self.c.execute("DELETE FROM cadet_change_requests")
            self.refuses(self.REQ % ("", ""), row)

    def test_status_and_decision_columns_stay_consistent(self):
        self.c.execute("INSERT INTO cadet_change_requests(kind,requested_by,target_user_id) VALUES('delete',3,5)")
        self.refuses("UPDATE cadet_change_requests SET status='approved'")                  # decided without a time
        self.refuses("UPDATE cadet_change_requests SET decided_at='2026-01-01T00:00:00Z'")   # a time but still pending
        self.refuses("UPDATE cadet_change_requests SET decided_by=1")
        self.refuses("UPDATE cadet_change_requests SET status='maybe', decided_at='2026-01-01T00:00:00Z'")
        self.c.execute("UPDATE cadet_change_requests SET status='rejected', decided_by=1, decided_at='2026-01-01T00:00:00Z'")

    def test_only_one_open_request_per_kind_target_and_requester(self):
        ins = "INSERT INTO cadet_change_requests(kind,requested_by,target_user_id) VALUES('delete',?,5)"
        self.c.execute(ins, (3,))
        self.refuses(ins, (3,))
        self.c.execute(ins, (4,))                                        # another requester
        self.c.execute("UPDATE cadet_change_requests SET status='rejected', decided_at='2026-01-01T00:00:00Z' WHERE requested_by=3")
        self.c.execute(ins, (3,))                                        # the closed one no longer blocks

    def test_foreign_keys(self):
        self.c.execute("INSERT INTO cadet_change_requests(kind,requested_by,target_user_id) VALUES('delete',3,5)")
        self.c.execute("DELETE FROM users WHERE id=3")                  # the requester goes: the request stays, author NULL
        self.assertIsNone(self.c.execute("SELECT requested_by FROM cadet_change_requests").fetchone()[0])
        self.c.execute("DELETE FROM users WHERE id=5")                  # the target goes: the request goes with them
        self.assertEqual(self.c.execute("SELECT count(*) FROM cadet_change_requests").fetchone()[0], 0)
        self.c.execute("INSERT INTO instructor_platoons(instructor_id,platoon_id) VALUES(4,1)")
        self.c.execute("DELETE FROM users WHERE id=4")
        self.assertEqual(self.c.execute("SELECT count(*) FROM instructor_platoons").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
