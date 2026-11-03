import contextlib
import datetime as dt
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import backup as B  # noqa: E402
import migrate_json_to_sqlite as M  # noqa: E402

from baror.auth.passwords import verify_password  # noqa: E402
from baror.storage import db, repo  # noqa: E402

LEGACY = {
    "Dana@Example.com": {"name": "דנה", "gender": "נקבה", "password": "pw-dana-1", "is_km": False,
                         "history": [{"date": "2026-01-05", "run_seconds": 1000, "pushups": 20},
                                     {"date": "2026-01-12", "run_seconds": 980, "pushups": 0}]},
    "dana@example.com": {"name": "דנה שניה", "gender": "נקבה", "password": "pw-other", "is_km": False, "history": []},
    "avi@example.com": {"name": "אבי", "gender": "זכר", "password": "pw-avi-1", "is_km": True,
                        "history": [{"date": "2026-02-01", "run_seconds": 3900, "pushups": 30},
                                    {"date": "2026-02-31", "run_seconds": 3900, "pushups": 30},
                                    {"date": "2026-03-01", "run_seconds": -300, "pushups": 30},
                                    {"date": "2026-03-02", "run_seconds": 6039, "pushups": 10},
                                    {"date": "2026-03-03", "run_seconds": 9999, "pushups": 10},
                                    "garbage"]},
    "x@example.com": {"name": "מישהו", "gender": "אחר", "password": "p", "is_km": False, "history": []},
    "nopass@example.com": {"name": "n", "gender": "זכר", "history": []},
    "bad email": {"name": "n", "gender": "זכר", "password": "p", "history": []},
    "nullu@example.com": None,
    "old@example.com": {"name": "ישן", "gender": "זכר", "password": "p-old"},  # no history/is_km keys (old format)
}


class Migration(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)
        self.json = self.d / "users.json"
        self.json.write_text(json.dumps(LEGACY, ensure_ascii=False), encoding="utf-8")
        self.db = self.d / "baror.db"

    def run_migrate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return M.migrate(self.json, self.db, self.d)

    def test_counts_add_up(self):
        s = self.run_migrate()
        self.assertEqual(s["users_read"], len(LEGACY))
        self.assertEqual(s["users_imported"] + s["users_existing"] + s["users_rejected"], s["users_read"])
        self.assertEqual(s["records_imported"] + s["records_existing"] + s["records_rejected"], s["records_read"])
        self.assertEqual((s["users_imported"], s["users_rejected"]), (3, 5))  # dana, avi, old  |  dup, 4 invalid
        self.assertEqual((s["records_imported"], s["records_rejected"]), (4, 4))

    def test_idempotent(self):
        first = self.run_migrate()
        second = self.run_migrate()
        self.assertEqual(second["users_imported"], 0)
        self.assertEqual(second["records_imported"], 0)
        self.assertEqual(second["users_existing"], first["users_imported"])
        self.assertEqual(second["records_existing"], first["records_imported"])
        with db.reader(self.db) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM results").fetchone()[0], first["records_imported"])

    def test_passwords_hashed_and_flagged(self):
        self.run_migrate()
        rec = repo.get_login_record(self.db, "dana@example.com")
        self.assertTrue(rec["pw_hash"].startswith("scrypt$"))
        self.assertNotIn("pw-dana-1", rec["pw_hash"])
        self.assertTrue(verify_password("pw-dana-1", rec["pw_hash"]))
        self.assertEqual(rec["must_change_pw"], 1)

    def test_case_collision_is_not_merged(self):
        self.run_migrate()
        self.assertEqual(repo.get_user(self.db, repo.get_login_record(self.db, "dana@example.com")["id"])["name"], "דנה")
        rej = json.loads((self.d / "migration_rejects.json").read_text(encoding="utf-8"))
        collisions = [r for r in rej if "collides" in r["reason"]]
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["legacy_key"], "dana@example.com")

    def test_gender_mapping_and_unknown_rejected(self):
        self.run_migrate()
        avi = repo.get_user(self.db, repo.get_login_record(self.db, "avi@example.com")["id"])
        self.assertEqual((avi["gender"], avi["is_km"]), ("M", 1))
        self.assertIsNone(repo.get_login_record(self.db, "x@example.com"))

    def test_run_seconds_converted_to_tenths(self):
        self.run_migrate()
        uid = repo.get_login_record(self.db, "dana@example.com")["id"]
        self.assertEqual(sorted(r["run_tenths"] for r in repo.list_results(self.db, uid)), [9800, 10000])

    def test_bad_records_rejected_with_reason(self):
        self.run_migrate()
        rej = json.loads((self.d / "migration_rejects.json").read_text(encoding="utf-8"))
        reasons = " | ".join(r["reason"] for r in rej if r["kind"] == "record")
        for needle in ["bad date", "out of range", "not an object"]:
            self.assertIn(needle, reasons)

    def test_99_99_style_value_is_imported_but_reported(self):
        s = self.run_migrate()
        # 6039 s (legacy '99:99') is in range so it IS imported; the walk user is_km=True so it is not counted as suspect,
        # but a non-walk record above 100 minutes would be. The point: the script cannot tell, so it counts zero-push-ups.
        self.assertEqual(s["zero_pushup_records"], 1)

    def test_rejects_file_never_contains_plaintext_passwords(self):
        self.run_migrate()
        text = (self.d / "migration_rejects.json").read_text(encoding="utf-8")
        for secret in ["pw-other", "pw-avi-1", "pw-dana-1"]:
            self.assertNotIn(secret, text)
        mode = stat.S_IMODE(os.stat(self.d / "migration_rejects.json").st_mode)
        self.assertEqual(mode, 0o600)

    def test_original_untouched_and_premigration_copy(self):
        before = self.json.read_bytes()
        self.run_migrate()
        self.assertEqual(self.json.read_bytes(), before)
        copy = self.d / "users.json.premigration"
        self.assertEqual(copy.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(os.stat(copy).st_mode), 0o400)

    def test_unreadable_inputs_exit_nonzero_and_create_nothing(self):
        for payload in [b"", b"\xff\xfe\x00", b"[1,2]", b"null", b'{"a": '[:5]]:
            with self.subTest(payload=payload):
                self.json.write_bytes(payload)
                with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
                    M.migrate(self.json, self.db, self.d)
                self.assertEqual(cm.exception.code, 1)
                self.assertFalse(self.db.exists())

    def test_main_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(M.main(["--json", str(self.json), "--db", str(self.db)]), 2)  # rejects present
            clean = self.d / "clean.json"
            clean.write_text(json.dumps({"ok@example.com": LEGACY["old@example.com"]}), encoding="utf-8")
            self.assertEqual(M.main(["--json", str(clean), "--db", str(self.d / "c.db")]), 0)


class Backup(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)
        self.db = self.d / "baror.db"
        db.init_db(self.db)
        self.uid = repo.create_user(self.db, email="a@example.com", name="n", gender="M", is_km=False, pw_hash="h")
        for i in range(30):
            repo.add_result(self.db, self.uid, test_date="2026-01-01", pushups=i)

    def test_backup_is_valid_complete_and_private(self):
        out = B.backup(self.db, self.d / "bk", now=dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc))
        self.assertEqual(out.name, "baror-20261102.db")
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        self.assertEqual(db.integrity_check(out), [])
        self.assertEqual(len(repo.list_results(out, self.uid)), 30)
        self.assertEqual([p.name for p in (self.d / "bk").iterdir()], ["baror-20261102.db"], "no .partial left behind")

    def test_same_day_second_backup_does_not_overwrite(self):
        t = dt.datetime(2026, 11, 2, 10, 0, 5, tzinfo=dt.timezone.utc)
        a = B.backup(self.db, self.d / "bk", now=t); b = B.backup(self.db, self.d / "bk", now=t)
        self.assertNotEqual(a, b); self.assertTrue(a.exists() and b.exists())

    def test_retention_keeps_newest_14_and_only_matching_files(self):
        bk = self.d / "bk"; bk.mkdir()
        (bk / "notes.txt").write_text("keep me"); (bk / "baror-latest.db").write_text("keep me too")
        for day in range(1, 21):
            B.backup(self.db, bk, keep=14, now=dt.datetime(2026, 10, day, tzinfo=dt.timezone.utc))
        names = sorted(p.name for p in bk.iterdir() if B.PATTERN.match(p.name))
        self.assertEqual(len(names), 14)
        self.assertEqual(names[0], "baror-20261007.db"); self.assertEqual(names[-1], "baror-20261020.db")
        self.assertTrue((bk / "notes.txt").exists() and (bk / "baror-latest.db").exists())

    def test_backup_while_writers_are_active_is_consistent(self):
        import threading
        stop = threading.Event()

        def write():
            while not stop.is_set():
                with db.transaction(self.db) as c:
                    c.execute("INSERT INTO results(user_id,test_date,pushups) VALUES(?,?,1)", (self.uid, "2026-05-05"))
                    c.execute("INSERT INTO results(user_id,test_date,pushups) VALUES(?,?,2)", (self.uid, "2026-05-05"))
        t = threading.Thread(target=write); t.start()
        try:
            outs = [B.backup(self.db, self.d / "bk", now=dt.datetime(2026, 1, i + 1, tzinfo=dt.timezone.utc)) for i in range(5)]
        finally:
            stop.set(); t.join()
        for o in outs:
            self.assertEqual(db.integrity_check(o), [])
            n = len(repo.list_results(o, self.uid)) - 30
            self.assertEqual(n % 2, 0, "backup captured half a transaction")

    def test_corrupt_source_is_not_backed_up_silently(self):
        bad = self.d / "bad.db"; bad.write_bytes(b"not sqlite" * 100)
        with self.assertRaises((RuntimeError, sqlite3.Error)):
            B.backup(bad, self.d / "bk2")
        self.assertEqual([p for p in (self.d / "bk2").glob("baror-*.db")], [])

    def test_restore_roundtrip_keeps_previous_and_clears_stale_wal(self):
        out = B.backup(self.db, self.d / "bk")
        repo.add_result(self.db, self.uid, test_date="2026-06-06", pushups=99)  # change after the backup
        saved = B.restore(out, self.db)
        self.assertTrue(saved.exists())
        self.assertEqual(len(repo.list_results(self.db, self.uid)), 30)
        self.assertEqual(len(repo.list_results(saved, self.uid)), 31)
        self.assertEqual(db.integrity_check(self.db), [])

    def test_backup_dir_and_restored_database_are_private_even_with_a_loose_umask(self):
        old = os.umask(0o000)
        try:
            out = B.backup(self.db, self.d / "bk2")
            B.restore(out, self.db)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(self.d / "bk2").st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.db).st_mode), 0o600)

    def test_restore_refuses_corrupt_backup_and_leaves_db_alone(self):
        bad = self.d / "bad.db"; bad.write_bytes(b"x" * 5000)
        before = self.db.read_bytes() if not (self.d / "baror.db-wal").exists() else None
        with self.assertRaises((RuntimeError, sqlite3.Error)):
            B.restore(bad, self.db)
        self.assertEqual(len(repo.list_results(self.db, self.uid)), 30)

    def test_cli_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(B.main(["--db", str(self.db), "--dir", str(self.d / "bk3")]), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(B.main(["--db", str(self.d / "missing.db"), "--dir", str(self.d / "bk4")]), 1)


if __name__ == "__main__":
    unittest.main()
