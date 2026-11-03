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

    def ps(self):
        """A parent pseudonym row, so a raw users insert fails ONLY on the constraint a test is about."""
        p = os.urandom(16).hex()
        self.raw("INSERT INTO pseudonyms(pseudonym) VALUES(?)", (p,))
        return p

    def test_raw_user_insert_baseline_is_valid(self):
        """Control for the constraint tests below: with everything valid the insert succeeds."""
        self.raw("INSERT INTO users(email,name,gender,is_km,pw_hash,pseudonym) VALUES('ok@x.com','n','M',0,'h',?)", (self.ps(),))
        with self.assertRaises(sqlite3.IntegrityError):      # and the pseudonym really is required
            self.raw("INSERT INTO users(email,name,gender,pw_hash) VALUES('no-ps@x.com','n','M','h')")
        with self.assertRaises(sqlite3.IntegrityError):      # ...and must exist in the parent table
            self.raw("INSERT INTO users(email,name,gender,pw_hash,pseudonym) VALUES('orph@x.com','n','M','h',?)", ("cd" * 16,))

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
                self.raw("INSERT INTO users(email,name,gender,pw_hash,pseudonym) VALUES(?,?,?,?,?)", (bad, "n", "M", "h", self.ps()))

    def test_gender_and_bool_checks(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("INSERT INTO users(email,name,gender,pw_hash,pseudonym) VALUES('a@x.com','n','X','h',?)", (self.ps(),))
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("INSERT INTO users(email,name,gender,is_km,pw_hash,pseudonym) VALUES('a@x.com','n','M',2,'h',?)", (self.ps(),))

    def test_empty_pw_hash_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("INSERT INTO users(email,name,gender,pw_hash,pseudonym) VALUES('a@x.com','n','M','',?)", (self.ps(),))

    def test_brief_glob_accepted_impossible_dates_but_schema_v1_does_not(self):
        ps = self.pseudonym_of(self.user())
        for bad in ["2026-99-99", "2026-02-31", "2026-13-01", "2026-1-5", "20260101", "abcd-ef-gh", ""]:
            with self.subTest(bad=bad), self.assertRaises(sqlite3.IntegrityError):
                self.raw("INSERT INTO results(pseudonym,test_date,pushups) VALUES(?,?,10)", (ps, bad))
        self.raw("INSERT INTO results(pseudonym,test_date,pushups) VALUES(?,?,10)", (ps, "2024-02-29"))  # leap day ok

    def test_measurement_ranges(self):
        ps = self.pseudonym_of(self.user())
        cases = [(0, None), (72001, None), (-1, None), (None, -1), (None, 301), (None, None)]
        for run, push in cases:
            with self.subTest(run=run, push=push), self.assertRaises(sqlite3.IntegrityError):
                self.raw("INSERT INTO results(pseudonym,test_date,run_tenths,pushups) VALUES(?,?,?,?)",
                         (ps, "2026-01-01", run, push))
        self.raw("INSERT INTO results(pseudonym,test_date,run_tenths,pushups) VALUES(?,?,?,?)",
                 (ps, "2026-01-01", 72000, 0))  # inclusive upper bound; 0 push-ups is a legal stored value

    def test_foreign_key_and_cascade(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("INSERT INTO results(pseudonym,test_date,pushups) VALUES(?,'2026-01-01',5)", ("ab" * 16,))  # unknown parent
        uid = self.user()
        repo.add_result(self.path, uid, test_date="2026-01-01", pushups=5)
        repo.delete_user(self.path, uid)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM results").fetchone()[0], 0)

    def test_deleting_only_the_identity_row_keeps_the_results_unlinked(self):
        """ADR-0002: the retention path. Erasure (repo.delete_user) is the test above."""
        uid = self.user()
        repo.add_result(self.path, uid, test_date="2026-01-01", pushups=5)
        ps = self.pseudonym_of(uid)
        self.raw("DELETE FROM users WHERE id=?", (uid,))
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM results WHERE pseudonym=?", (ps,)).fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM pseudonyms WHERE pseudonym=?", (ps,)).fetchone()[0], 1)

    def test_pseudonym_shape_is_enforced(self):
        for bad in ["", "x" * 32, "AB" * 16, "ab" * 15, "ab" * 17]:
            with self.subTest(bad=bad), self.assertRaises(sqlite3.IntegrityError):
                self.raw("INSERT INTO pseudonyms(pseudonym) VALUES(?)", (bad,))

    def test_pseudonyms_are_random_and_not_the_user_id(self):
        ids = [self.user(f"u{i}@example.com") for i in range(20)]
        ps = [self.pseudonym_of(i) for i in ids]
        self.assertEqual(len(set(ps)), 20)
        self.assertTrue(all(len(p) == 32 and p == p.lower() for p in ps))

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
                c.execute("INSERT INTO pseudonyms(pseudonym) VALUES(?)", ("ef" * 16,))
                c.execute("INSERT INTO users(email,name,gender,pw_hash,pseudonym) VALUES('a@x.com','n','Q','h',?)", ("ef" * 16,))

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
                c.execute("INSERT INTO pseudonyms(pseudonym) VALUES(?)", ("ef" * 16,))
                c.execute("INSERT INTO users(email,name,gender,pw_hash,pseudonym) VALUES('a@x.com','n','M','h',?)", ("ef" * 16,))
                raise Boom()
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM pseudonyms").fetchone()[0], 0, "both inserts rolled back")


if __name__ == "__main__":
    unittest.main()
