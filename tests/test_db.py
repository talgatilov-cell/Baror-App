import os
import sqlite3
import unittest

from baror.errors import ConflictError, CorruptedDataError, StorageError, ValidationError
from baror.storage import db, repo
from tests.base import DbCase


class SchemaConstraints(DbCase):
    def raw(self, sql, args=()):
        c = db.connect(self.path)
        try:
            c.execute(sql, args)
        finally:
            c.close()

    def test_version_and_pragmas(self):
        self.assertEqual(db.user_version(self.path), db.LATEST_VERSION)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA busy_timeout").fetchone()[0], db.BUSY_TIMEOUT_MS)

    def test_init_is_idempotent(self):
        uid = self.user()
        for _ in range(3):
            self.assertEqual(db.init_db(self.path), db.LATEST_VERSION)
        self.assertEqual(repo.get_user(self.path, uid)["email"], "a@example.com")

    def test_email_must_be_lowercase_trimmed_at_sql_level(self):
        for bad in ["A@x.com", " a@x.com", "a@x.com ", "nope", "@x.com"]:
            with self.subTest(bad=bad), self.assertRaises(sqlite3.IntegrityError):
                self.raw("INSERT INTO users(email,name,gender,pw_hash) VALUES(?,?,?,?)", (bad, "n", "M", "h"))

    def test_gender_and_bool_checks(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("INSERT INTO users(email,name,gender,pw_hash) VALUES('a@x.com','n','X','h')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("INSERT INTO users(email,name,gender,is_km,pw_hash) VALUES('a@x.com','n','M',2,'h')")

    def test_empty_pw_hash_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("INSERT INTO users(email,name,gender,pw_hash) VALUES('a@x.com','n','M','')")

    def test_brief_glob_accepted_impossible_dates_but_schema_v1_does_not(self):
        uid = self.user()
        for bad in ["2026-99-99", "2026-02-31", "2026-13-01", "2026-1-5", "20260101", "abcd-ef-gh", ""]:
            with self.subTest(bad=bad), self.assertRaises(sqlite3.IntegrityError):
                self.raw("INSERT INTO results(user_id,test_date,pushups) VALUES(?,?,10)", (uid, bad))
        self.raw("INSERT INTO results(user_id,test_date,pushups) VALUES(?,?,10)", (uid, "2024-02-29"))  # leap day ok

    def test_measurement_ranges(self):
        uid = self.user()
        cases = [(0, None), (72001, None), (-1, None), (None, -1), (None, 301), (None, None)]
        for run, push in cases:
            with self.subTest(run=run, push=push), self.assertRaises(sqlite3.IntegrityError):
                self.raw("INSERT INTO results(user_id,test_date,run_tenths,pushups) VALUES(?,?,?,?)",
                         (uid, "2026-01-01", run, push))
        self.raw("INSERT INTO results(user_id,test_date,run_tenths,pushups) VALUES(?,?,?,?)",
                 (uid, "2026-01-01", 72000, 0))  # inclusive upper bound; 0 push-ups is a legal stored value

    def test_foreign_key_and_cascade(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("INSERT INTO results(user_id,test_date,pushups) VALUES(999,'2026-01-01',5)")
        uid = self.user()
        repo.add_result(self.path, uid, test_date="2026-01-01", pushups=5)
        repo.delete_user(self.path, uid)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM results").fetchone()[0], 0)

    def test_integrity_check_clean(self):
        self.user()
        self.assertEqual(db.integrity_check(self.path), [])


class ErrorTranslation(DbCase):
    def test_duplicate_email_is_conflict(self):
        self.user("dup@example.com")
        with self.assertRaises(ConflictError):
            self.user("DUP@example.com ")

    def test_check_failure_is_validation_error(self):
        with db.transaction(self.path) as c:
            pass
        with self.assertRaises(ValidationError):
            with db.transaction(self.path) as c:
                c.execute("INSERT INTO users(email,name,gender,pw_hash) VALUES('a@x.com','n','Q','h')")

    def test_garbage_file_is_corrupt_and_untouched(self):
        bad = os.path.join(self.dir, "garbage.db")
        payload = b"this is definitely not sqlite " * 50
        with open(bad, "wb") as f:
            f.write(payload)
        with self.assertRaises(CorruptedDataError):
            db.init_db(bad)
        with open(bad, "rb") as f:
            self.assertEqual(f.read(), payload, "corrupt file must never be overwritten")

    def test_truncated_database_is_detected(self):
        uid = self.user()
        for i in range(200):
            repo.add_result(self.path, uid, test_date="2026-01-01", pushups=i % 50)
        with db.reader(self.path) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size = os.path.getsize(self.path)
        with open(self.path, "r+b") as f:
            f.truncate(size // 2)
        with self.assertRaises((CorruptedDataError, StorageError)):
            db.init_db(self.path)
            problems = db.integrity_check(self.path)
            if problems:
                raise CorruptedDataError(detail=str(problems))

    def test_missing_directory_is_storage_error(self):
        with self.assertRaises(StorageError):
            db.init_db(os.path.join(self.dir, "nope", "x.db"))

    def test_newer_schema_refused(self):
        c = db.connect(self.path)
        c.execute(f"PRAGMA user_version={db.LATEST_VERSION + 1}")
        c.close()
        with self.assertRaises(StorageError):
            db.init_db(self.path)

    def test_lock_timeout_is_error_not_hang(self):
        holder = db.connect(self.path)
        holder.execute("BEGIN IMMEDIATE")
        try:
            import time
            t = time.perf_counter()
            with self.assertRaises(StorageError):
                with db.transaction(self.path, timeout_ms=300):
                    pass
            self.assertLess(time.perf_counter() - t, 3)
        finally:
            holder.execute("ROLLBACK"); holder.close()

    def test_rollback_on_exception(self):
        class Boom(Exception):
            pass
        with self.assertRaises(Boom):
            with db.transaction(self.path) as c:
                c.execute("INSERT INTO users(email,name,gender,pw_hash) VALUES('a@x.com','n','M','h')")
                raise Boom()
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
