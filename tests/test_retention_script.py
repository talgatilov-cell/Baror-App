"""scripts/retention.py: backup purge and the 'fresh backup has no link' proof (ADR-0002)."""
import datetime as dt
import importlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from baror.storage import db
from tests.test_migration_v2 import build_v1
from tests.test_retention import ADA, BO, CY, DEE, DUE_A, ROOT, RetentionCase, markers, raw_bytes

sys.path.insert(0, str(ROOT / "scripts"))
script = importlib.import_module("retention")      # scripts/retention.py
B = importlib.import_module("backup")              # scripts/backup.py

NOW = dt.datetime(2026, 7, 31, 3, 0, tzinfo=dt.timezone.utc)
UTC = dt.timezone.utc


class ScriptCase(RetentionCase):
    def setUp(self):
        super().setUp()
        self.bk = Path(self.dir) / "bk"
        B.backup(self.path, self.bk, now=dt.datetime(2025, 12, 1, tzinfo=UTC))        # too old, and holds the links
        B.backup(self.path, self.bk, now=dt.datetime(2026, 7, 20, tzinfo=UTC))        # holds the links
        legacy = self.bk / "baror-20260725.db"                                         # a backup from before schema v3
        build_v1(str(legacy)); db.init_db(legacy, target_version=2)
        (self.bk / "baror-20260720-120000.db").write_bytes(b"garbage" * 100)           # unreadable: cannot be proven clean
        (self.bk / "notes.txt").write_text("keep me")
        (self.bk / "baror-latest.db").write_bytes(b"not a dated backup name")
        self.deleted, self.kept = markers(ADA, BO), markers(CY, DEE)

    def names(self):
        return sorted(p.name for p in self.bk.iterdir())


class BackupPurge(ScriptCase):
    def test_every_backup_that_holds_or_may_hold_a_link_is_removed_and_a_clean_one_written(self):
        out = script.run(Path(self.path), self.bk, today=DUE_A, now=NOW)
        self.assertEqual(out["deidentified"], 2)
        self.assertEqual(out["backups_removed"], {"age": 1, "link": 3})
        self.assertEqual(self.names(), ["baror-20260731.db", "baror-latest.db", "notes.txt"])
        fresh = self.bk / "baror-20260731.db"
        data = fresh.read_bytes()
        self.assertFalse(os.path.exists(str(fresh) + "-wal"))
        for m in self.deleted:
            self.assertNotIn(m, data, f"{m!r} is in the fresh backup")
        for m in self.kept:
            self.assertIn(m, data)
        self.assertEqual(db.integrity_check(fresh), [])
        c = sqlite3.connect(fresh)
        try:
            self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], 3)
            self.assertEqual(c.execute("SELECT count(*) FROM results").fetchone()[0], 3 + 2 + 2 + 1, "all results kept")
            self.assertEqual(c.execute("SELECT count(*) FROM pseudonyms WHERE deidentified_at IS NOT NULL").fetchone()[0], 2)
        finally:
            c.close()
        self.assertEqual(stat.S_IMODE(os.stat(fresh).st_mode), 0o600)

    def test_no_backup_with_a_link_survives_a_scan(self):
        out = script.run(Path(self.path), self.bk, today=DUE_A, now=NOW)
        for p in self.bk.glob("baror-2*.db"):
            self.assertFalse(script.backup_has_links(p, out and [self.ada_ps, self.bo_ps]), p.name)

    def test_a_run_that_crashed_after_the_commit_is_cleaned_up_by_the_next_one(self):
        from baror.storage import retention as core
        core.run(Path(self.path), today=DUE_A, now=NOW)                         # database part only: the script "crashed" here
        self.assertEqual(len(list(self.bk.glob("baror-2*.db"))), 4)
        out = script.run(Path(self.path), self.bk, today=DUE_A, now=NOW)       # nothing is due any more
        self.assertEqual(out["deidentified"], 0)
        self.assertEqual(out["backups_removed"], {"age": 1, "link": 3})
        self.assertTrue(out["fresh_backup"])
        self.assertEqual(self.names(), ["baror-20260731.db", "baror-latest.db", "notes.txt"])
        for m in self.deleted:
            self.assertNotIn(m, (self.bk / "baror-20260731.db").read_bytes())

    def test_dry_run_touches_neither_database_nor_backups(self):
        before = self.names()
        out = script.run(Path(self.path), self.bk, today=DUE_A, now=NOW, dry_run=True)
        self.assertEqual(out["would_deidentify"], 2)
        self.assertEqual(self.names(), before)
        self.assertEqual(self.count("SELECT count(*) FROM users"), 5)

    def test_nothing_due_means_no_fresh_backup_and_only_age_purging(self):
        out = script.run(Path(self.path), self.bk, today=DUE_A - dt.timedelta(days=1), now=NOW - dt.timedelta(days=1))
        self.assertEqual(out["deidentified"], 0)
        self.assertEqual(out["backups_removed"], {"age": 1, "link": 0})
        self.assertNotIn("fresh_backup", out)
        self.assertEqual(self.names(), ["baror-20260720-120000.db", "baror-20260720.db", "baror-20260725.db",
                                        "baror-latest.db", "notes.txt"], "link-holding backups stay while nothing is due")

    def test_age_purge_uses_calendar_months(self):
        edge = self.bk / "baror-20260131.db"        # exactly 6 months before 2026-07-31: kept (not older than retention)
        shutil.copy(self.bk / "baror-20260720.db", edge)
        older = self.bk / "baror-20260130.db"       # one day older: purged
        shutil.copy(self.bk / "baror-20260720.db", older)
        script.purge_backups(self.bk, [], DUE_A)
        self.assertTrue(edge.exists()); self.assertFalse(older.exists())

    def test_missing_backup_dir_is_not_an_error(self):
        out = script.run(Path(self.path), Path(self.dir) / "nope", today=DUE_A, now=NOW)
        self.assertEqual(out["backups_removed"], {"age": 0, "link": 0})

    def test_unrelated_files_are_never_touched(self):
        script.run(Path(self.path), self.bk, today=DUE_A, now=NOW)
        self.assertEqual((self.bk / "notes.txt").read_text(), "keep me")
        self.assertEqual((self.bk / "baror-latest.db").read_bytes(), b"not a dated backup name")


class CommandLine(ScriptCase):
    def run_cli(self, *extra):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "retention.py"), "--db", self.path,
                               "--backup-dir", str(self.bk), *extra], capture_output=True, text=True, cwd=ROOT)

    def test_exit_code_and_output_contain_counts_only(self):
        r = self.run_cli("--today", DUE_A.isoformat())
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["deidentified"], out["pending"], out["unscheduled"]), (2, 1, 1))
        self.assertEqual(self.run_cli("--today", DUE_A.isoformat(), "--dry-run").returncode, 0)
        everything = r.stdout + r.stderr
        for m in self.deleted + self.kept + [self.ada_ps.encode(), self.bo_ps.encode()]:
            self.assertNotIn(m.decode("utf-8"), everything)

    def test_unusable_database_exits_1_without_a_traceback(self):
        bad = Path(self.dir) / "bad.db"
        bad.write_bytes(b"this is not sqlite" * 100)
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "retention.py"), "--db", str(bad)],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)

    def test_course_end_command_sets_the_clock(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "admin_users.py"), "--db", self.path,
                            "course-end", "B", "2026-06-30"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.count("SELECT course_end FROM platoons WHERE name='B'"), "2026-06-30")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "admin_users.py"), "--db", self.path,
                            "course-end", "B", "30/06/2026"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
