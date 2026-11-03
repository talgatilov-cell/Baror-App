"""ADR-0002: after the retention job the identity link is gone from the database file, its WAL and a fresh backup,
while the de-identified results stay. Abuse-case matrix: see the class docstrings and test names."""
import datetime as dt
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

from baror.auth import service, sessions
from baror.errors import AuthError, NotFoundError, ValidationError
from baror.storage import allowlist, db, ops, repo, retention
from tests.v2base import V2Case

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

D = dt.date
# unique, searchable markers (ASCII and Hebrew) for "the link is still in the bytes"
ADA = {"email": "ada.zq7x@example.com", "name": "עדה-צוערת-זקא"}     # a Hebrew name: checked as UTF-8 bytes
BO = {"email": "bo.zq7x@example.com", "name": "Zqname-Bo-Qx"}
CY = {"email": "cy.zq7x@example.com", "name": "Zqname-Cy-Qx"}
DEE = {"email": "dee.zq7x@example.com", "name": "Zqname-Dee-Qx"}
PW = "correct horse battery"
DUE_A = D(2026, 7, 31)      # platoon A course_end 2026-01-31 + 6 months
DUE_B = D(2026, 11, 1)      # platoon B course_end 2026-05-01 + 6 months


def raw_bytes(path) -> bytes:
    data = b""
    for suffix in ("", "-wal"):
        p = str(path) + suffix
        if os.path.exists(p):
            data += Path(p).read_bytes()
    return data


_REAL_TX = db.transaction


def _plain_tx(path):
    return _REAL_TX(path)                # the real transaction, but never asking for secure_delete


def markers(*people):
    out = []
    for p in people:
        out += [p["email"].encode("utf-8"), p["name"].encode("utf-8")]
    return out


class RetentionCase(V2Case):
    def setUp(self):
        super().setUp()
        self.pa, self.pb = self.platoon("A"), self.platoon("B")
        retention.set_course_end(self.path, self.pa, "2026-01-31")
        retention.set_course_end(self.path, self.pb, "2026-05-01")
        self.ada = self.register(ADA, self.pa, "F")
        self.bo = self.register(BO, self.pa, "M")
        self.cy = self.cadet(CY["email"], self.pb, name=CY["name"])
        self.dee = self.cadet(DEE["email"], None, name=DEE["name"])           # no platoon -> unscheduled
        self.ins = self.staff("ins.zq7x@example.com", "instructor", [self.pa], name="Zqname-Ins-Qx")
        for uid, n in ((self.ada, 3), (self.bo, 2), (self.cy, 2), (self.dee, 1)):
            for i in range(n):
                repo.add_result(self.path, uid, test_date=f"2026-0{i + 1}-10", run_tenths=9000 + i, pushups=20 + i)
        self.tok = sessions.issue(self.path, self.ada)
        self.ada_ps, self.bo_ps = self.pseudonym_of(self.ada), self.pseudonym_of(self.bo)

    def register(self, who, platoon, gender):
        allowlist.add_entry(self.path, who["email"], platoon_id=platoon)
        u = service.register(self.path, email=who["email"], name=who["name"], gender=gender, is_km=(gender == "M"),
                             password=PW)
        return u["id"]

    def count(self, sql, args=()):
        with db.reader(self.path) as c:
            return c.execute(sql, args).fetchone()[0]


class DeadlineLogic(RetentionCase):
    def test_calendar_month_arithmetic(self):
        for start, months, want in [(D(2026, 8, 31), 6, D(2027, 2, 28)), (D(2023, 8, 31), 6, D(2024, 2, 29)),
                                    (D(2026, 1, 31), 6, D(2026, 7, 31)), (D(2026, 12, 31), 6, D(2027, 6, 30)),
                                    (D(2026, 11, 3), -6, D(2026, 5, 3)), (D(2026, 8, 31), -6, D(2026, 2, 28))]:
            self.assertEqual(retention.add_months(start, months), want, (start, months))
        self.assertEqual(retention.deadline("2026-01-31"), DUE_A)

    def test_nothing_happens_the_day_before_the_deadline(self):
        r = retention.run(self.path, today=DUE_A - dt.timedelta(days=1))
        self.assertEqual((r["deidentified"], r["pending"], r["unscheduled"]), (0, 3, 1))
        self.assertEqual(self.count("SELECT count(*) FROM users"), 5)
        self.assertEqual(self.count("SELECT count(*) FROM audit_log WHERE action='retention.run'"), 0)

    def test_on_the_deadline_only_that_platoons_cadets_are_deidentified(self):
        r = retention.run(self.path, today=DUE_A)
        self.assertEqual((r["deidentified"], r["pending"], r["unscheduled"]), (2, 1, 1))
        self.assertEqual(sorted(retention.deidentified_pseudonyms(self.path)), sorted([self.ada_ps, self.bo_ps]))
        self.assertNotIn("pseudonyms", r, "the report carries counts only")
        with db.reader(self.path) as c:
            left = {x[0] for x in c.execute("SELECT email FROM users")}
        self.assertEqual(left, {CY["email"], DEE["email"], "ins.zq7x@example.com"})

    def test_staff_and_unscheduled_cadets_are_never_deleted_however_late(self):
        retention.run(self.path, today=D(2099, 1, 1))
        with db.reader(self.path) as c:
            emails = {x[0] for x in c.execute("SELECT email FROM users")}
        self.assertEqual(emails, {DEE["email"], "ins.zq7x@example.com"}, "cy (platoon B) is due by 2099; dee and staff are not")

    def test_platoon_without_course_end_is_unscheduled_and_clearing_stops_the_clock(self):
        retention.set_course_end(self.path, self.pa, None)
        r = retention.run(self.path, today=D(2099, 1, 1))
        self.assertEqual(r["deidentified"], 1)           # only cy (platoon B)
        self.assertEqual(r["unscheduled"], 3)            # ada, bo (A has no end now), dee

    def test_deactivated_cadets_are_deidentified_too(self):
        ops.deactivate_user(self.path, self.bo)
        self.assertEqual(retention.run(self.path, today=DUE_A)["deidentified"], 2)

    def test_set_course_end_validation(self):
        for bad in ("2026-02-30", "31/01/2026", "", 20260131, "2026-1-31"):
            with self.assertRaises(ValidationError, msg=str(bad)):
                retention.set_course_end(self.path, self.pa, bad)
        with self.assertRaises(NotFoundError):
            retention.set_course_end(self.path, 9999, "2026-01-31")
        self.assertEqual(self.count("SELECT course_end FROM platoons WHERE id=?", (self.pa,)), "2026-01-31")


class LinkIsGone(RetentionCase):
    def setUp(self):
        super().setUp()
        self.deleted = markers(ADA, BO)
        self.kept = markers(CY, DEE)

    def test_guard_the_search_can_find_the_strings_before_the_job(self):
        data = raw_bytes(self.path)
        for m in self.deleted + self.kept:
            self.assertIn(m, data, f"test is blind to {m!r}: the byte search would prove nothing")

    def test_after_the_job_no_trace_in_the_database_file_or_the_wal(self):
        retention.run(self.path, today=DUE_A)
        data = raw_bytes(self.path)
        for m in self.deleted:
            self.assertNotIn(m, data, f"{m!r} still in the database bytes")
        for m in self.kept:
            self.assertIn(m, data, "the job must not touch people who are not due")

    def _default_secure_delete_off(self):
        """Make every new connection start with secure_delete OFF, as on a SQLite build without the compile-time default
        (Debian's build has it ON, which would otherwise hide a missing explicit setting)."""
        real = sqlite3.connect

        def connect_off(*a, **kw):
            c = real(*a, **kw)
            c.execute("PRAGMA secure_delete=OFF")
            return c
        return mock.patch.object(db.sqlite3, "connect", connect_off)

    def test_control_without_secure_delete_and_scrub_the_strings_do_leak(self):
        """Proves the byte search can see a leak, i.e. that the passing test below is not vacuous."""
        with self._default_secure_delete_off(), mock.patch.object(retention, "_scrub_file", lambda p: None), \
                mock.patch.object(retention.db, "transaction", lambda path, **kw: _plain_tx(path)):
            retention.run(self.path, today=DUE_A)
        data = raw_bytes(self.path)
        self.assertTrue(any(m in data for m in self.deleted), "expected a leak when the protections are off")

    def test_the_job_scrubs_even_when_the_sqlite_build_does_not_default_secure_delete_on(self):
        with self._default_secure_delete_off():
            retention.run(self.path, today=DUE_A)
        data = raw_bytes(self.path)
        for m in self.deleted:
            self.assertNotIn(m, data)

    def test_no_table_column_holds_the_name_or_email_any_more(self):
        retention.run(self.path, today=DUE_A)
        with db.reader(self.path) as c:
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                for row in c.execute(f"SELECT * FROM {t}").fetchall():       # table names come from sqlite_master
                    for v in row:
                        if isinstance(v, str):
                            self.assertNotIn(v, (ADA["email"], BO["email"], ADA["name"], BO["name"]), t)

    def test_results_stay_under_the_pseudonym_with_the_attributes_kept(self):
        retention.run(self.path, today=DUE_A, now=dt.datetime(2026, 7, 31, 3, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(self.count("SELECT count(*) FROM results WHERE pseudonym=?", (self.ada_ps,)), 3)
        self.assertEqual(self.count("SELECT count(*) FROM results WHERE pseudonym=?", (self.bo_ps,)), 2)
        with db.reader(self.path) as c:
            row = c.execute("SELECT gender,is_km,platoon_id,deidentified_at FROM pseudonyms WHERE pseudonym=?",
                            (self.ada_ps,)).fetchone()
        self.assertEqual(tuple(row), ("F", 0, self.pa, "2026-07-31T03:00:00Z"))
        self.assertEqual(self.count("SELECT count(*) FROM pseudonyms WHERE deidentified_at IS NULL"), 3, "cy, dee, instructor")

    def test_nothing_in_the_database_can_still_join_a_person_to_those_results(self):
        retention.run(self.path, today=DUE_A)
        self.assertEqual(self.count("SELECT count(*) FROM users WHERE pseudonym IN (?,?)", (self.ada_ps, self.bo_ps)), 0)
        self.assertEqual(self.count("SELECT count(*) FROM sessions"), 0, "ada's session cascaded; nobody else had one")
        self.assertEqual(self.count("SELECT count(*) FROM allowed_emails WHERE email IN (?,?)", (ADA["email"], BO["email"])), 0)
        self.assertEqual(self.count("SELECT count(*) FROM allowed_emails WHERE used_by IS NOT NULL AND used_by NOT IN (SELECT id FROM users)"), 0)
        # audit rows that named them now name nobody
        self.assertEqual(self.count("SELECT count(*) FROM audit_log WHERE action='auth.register' AND actor_id IS NULL"), 2)
        self.assertEqual(self.count("SELECT count(*) FROM audit_log WHERE action='retention.run'"), 1)

    def test_the_old_session_token_and_password_no_longer_work(self):
        retention.run(self.path, today=DUE_A)
        self.assertIsNone(sessions.resolve(self.path, self.tok))
        with self.assertRaises(AuthError):
            service.login(self.path, ADA["email"], PW)

    def test_database_is_healthy_and_the_others_are_unaffected(self):
        retention.run(self.path, today=DUE_A)
        self.assertEqual(db.integrity_check(self.path), [])
        self.assertEqual(len(repo.list_results(self.path, self.cy)), 2)
        self.assertEqual(repo.get_user(self.path, self.ins)["role"], "instructor")

    def test_second_run_changes_nothing(self):
        retention.run(self.path, today=DUE_A)
        snapshot = raw_bytes(self.path)
        r = retention.run(self.path, today=DUE_A)
        self.assertEqual(r["deidentified"], 0)
        self.assertEqual(self.count("SELECT count(*) FROM audit_log WHERE action='retention.run'"), 1)
        self.assertEqual(raw_bytes(self.path), snapshot, "a run with nothing due must not write at all")

    def test_a_failure_in_the_middle_changes_nothing(self):
        with mock.patch.object(retention.audit, "record", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                retention.run(self.path, today=DUE_A)
        self.assertEqual(self.count("SELECT count(*) FROM users"), 5)
        self.assertEqual(self.count("SELECT count(*) FROM pseudonyms WHERE deidentified_at IS NOT NULL"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM allowed_emails WHERE used_at IS NOT NULL"), 2)
        for m in self.deleted:
            self.assertIn(m, raw_bytes(self.path))


class FailureModes(RetentionCase):
    """Review findings: the link must not survive a failed or interrupted scrub, and a typo must be catchable."""

    def test_dry_run_writes_nothing_and_reports_who_would_go(self):
        before = raw_bytes(self.path)
        r = retention.run(self.path, today=DUE_A, dry_run=True)
        self.assertEqual((r["would_deidentify"], r["deidentified"]), (2, 0))
        self.assertEqual(raw_bytes(self.path), before)
        self.assertEqual(self.count("SELECT count(*) FROM users"), 5)

    def test_a_failed_scrub_is_retried_by_the_next_run_even_though_nothing_is_due_any_more(self):
        with mock.patch.object(retention, "_scrub_file", side_effect=retention.StorageError(detail="disk full")):
            with self.assertRaises(retention.StorageError):
                retention.run(self.path, today=DUE_A)
        self.assertEqual(self.count("SELECT count(*) FROM users WHERE pseudonym IN (?,?)", (self.ada_ps, self.bo_ps)), 0,
                         "the deletion itself had committed")
        self.assertEqual(self.count("SELECT count(*) FROM audit_log WHERE action='retention.scrubbed'"), 0)
        r = retention.run(self.path, today=DUE_A)                      # nothing due now, but the scrub is still owed
        self.assertEqual((r["deidentified"], r["scrubbed"]), (0, True))
        self.assertEqual(self.count("SELECT count(*) FROM audit_log WHERE action='retention.scrubbed'"), 1)
        self.assertFalse(retention.run(self.path, today=DUE_A)["scrubbed"], "and not again once done")

    def test_an_open_reader_blocks_the_wal_truncate_and_the_job_says_so_instead_of_claiming_success(self):
        reader = db.connect(self.path)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM results").fetchone()    # holds a WAL snapshot
            with self.assertRaises(retention.StorageError):
                retention.run(self.path, today=DUE_A)
        finally:
            reader.execute("ROLLBACK"); reader.close()
        r = retention.run(self.path, today=DUE_A)                          # reader gone: retry succeeds
        self.assertTrue(r["scrubbed"])
        for m in markers(ADA, BO):
            self.assertNotIn(m, raw_bytes(self.path))

    def test_deadline_in_the_past_is_flagged_by_the_course_end_command(self):
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "admin_users.py"), "--db", self.path,
                            "course-end", "A", "2020-01-01"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0)
        self.assertIn("2020-07-01", r.stdout)
        self.assertIn("WARNING", r.stderr)
        self.assertEqual(self.count("SELECT count(*) FROM users"), 5, "setting a date never deletes anything by itself")


class AfterTheJob(RetentionCase):
    def test_same_email_registering_again_is_a_new_person_without_the_old_results(self):
        retention.run(self.path, today=DUE_A)
        allowlist.add_entry(self.path, ADA["email"], platoon_id=self.pa)
        u = service.register(self.path, email=ADA["email"], name="Someone Else", gender="F", is_km=False, password=PW)
        self.assertNotEqual(self.pseudonym_of(u["id"]), self.ada_ps)
        self.assertEqual(repo.list_results(self.path, u["id"]), [])
        self.assertEqual(self.count("SELECT count(*) FROM results WHERE pseudonym=?", (self.ada_ps,)), 3, "old ones untouched")

    def test_erasure_by_the_user_deletes_results_but_retention_keeps_them(self):
        repo.delete_user(self.path, self.cy)                         # the user's own right to erasure
        self.assertEqual(self.count("SELECT count(*) FROM results WHERE pseudonym IN (SELECT pseudonym FROM pseudonyms)"),
                         3 + 2 + 1)
        self.assertEqual(self.count("SELECT count(*) FROM pseudonyms"), 4, "cy's pseudonym row is gone with the account")
        retention.run(self.path, today=DUE_A)                        # retention: results of ada, bo remain
        self.assertEqual(self.count("SELECT count(*) FROM results"), 3 + 2 + 1)

    def test_other_users_cannot_reach_deidentified_results_through_the_repository(self):
        retention.run(self.path, today=DUE_A)
        with db.reader(self.path) as c:
            rid = c.execute("SELECT id FROM results WHERE pseudonym=?", (self.ada_ps,)).fetchone()[0]
        with self.assertRaises(NotFoundError):
            repo.update_result(self.path, self.cy, rid, test_date="2026-01-01", run_tenths=1, pushups=None)
        with self.assertRaises(NotFoundError):
            repo.delete_result(self.path, self.cy, rid)

    def test_web_code_does_not_import_the_retention_module(self):
        import ast
        for f in (ROOT / "baror").rglob("*.py"):
            if f.name == "retention.py":
                continue
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("baror.storage"):
                    self.assertNotIn("retention", [a.name for a in node.names], f.name)
                if isinstance(node, ast.ImportFrom) and node.module == "baror.storage.retention":
                    self.fail(f"{f} imports retention")
                if isinstance(node, ast.Import):
                    self.assertFalse([a for a in node.names if a.name == "baror.storage.retention"], f.name)


if __name__ == "__main__":
    unittest.main()
