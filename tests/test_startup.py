"""T8.3: start-up checks. Abuse cases: missing directory, read-only directory (as a non-root user), a file that is not a
database (must stay byte-identical), a damaged database, a database from the future, a nearly full disk, bad zone data."""
import ast
import os
import shutil
import sqlite3
import stat
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from baror import startup
from baror.errors import ConfigError, CorruptedDataError, StorageError
from baror.storage import db, repo
from tests.test_migration_v2 import build_v1

ROOT = Path(__file__).resolve().parent.parent
NOT_ROOT = hasattr(os, "geteuid") and os.geteuid() != 0


def cfg(path, tz="Asia/Jerusalem"):
    return types.SimpleNamespace(db_path=Path(path), tz_name=tz)


class StartupCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "baror.db"

    def tree(self):
        return sorted(p.name for p in self.dir.iterdir())


class HappyPath(StartupCase):
    def test_a_fresh_directory_gets_a_database_at_the_latest_schema(self):
        self.assertEqual(startup.run_startup_checks(cfg(self.path)), {"schema": db.LATEST_VERSION})
        self.assertTrue(self.path.exists())
        self.assertEqual(db.integrity_check(self.path), [])

    def test_idempotent_and_leaves_no_probe_files_behind(self):
        for _ in range(3):
            startup.run_startup_checks(cfg(self.path))
        self.assertFalse([n for n in self.tree() if n.startswith(".startup-check-")])

    def test_data_is_untouched(self):
        startup.run_startup_checks(cfg(self.path))
        uid = repo.create_user(self.path, email="a@example.com", name="A", gender="M", is_km=False, pw_hash="h")
        repo.add_result(self.path, uid, test_date="2026-01-01", pushups=5)
        startup.run_startup_checks(cfg(self.path))
        self.assertEqual(len(repo.list_results(self.path, uid)), 1)

    def test_inspect_only_mode_accepts_a_missing_file_and_does_not_create_it(self):
        self.assertEqual(startup.run_startup_checks(cfg(self.path), init=False), {"schema": None})
        self.assertFalse(self.path.exists())

    def test_an_older_schema_is_upgraded_by_default_and_reported_when_inspect_only(self):
        build_v1(str(self.path))
        with self.assertRaises(StorageError) as cm:
            startup.run_startup_checks(cfg(self.path), init=False)
        self.assertIn("needs v", cm.exception.detail)
        self.assertEqual(db.user_version(self.path), 1, "inspect-only must not migrate")
        self.assertEqual(startup.run_startup_checks(cfg(self.path)), {"schema": db.LATEST_VERSION})


class DataDirectory(StartupCase):
    def test_missing_directory(self):
        missing = self.dir / "nope" / "baror.db"
        with self.assertRaises(ConfigError) as cm:
            startup.run_startup_checks(cfg(missing))
        self.assertIn("DATA_DIR_MISSING", cm.exception.detail)
        self.assertFalse((self.dir / "nope").exists(), "the directory is never created for you")

    def test_a_file_where_the_directory_should_be(self):
        (self.dir / "afile").write_text("x")
        with self.assertRaises(ConfigError) as cm:
            startup.run_startup_checks(cfg(self.dir / "afile" / "baror.db"))
        self.assertIn("DATA_DIR_MISSING", cm.exception.detail)

    @unittest.skipUnless(NOT_ROOT, "root ignores directory permissions")
    def test_read_only_directory_is_reported_and_an_existing_database_is_not_touched(self):
        db.init_db(self.path)
        before = self.path.read_bytes()
        names = self.tree()
        os.chmod(self.dir, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(lambda: os.chmod(self.dir, stat.S_IRWXU))
        with self.assertRaises(ConfigError) as cm:
            startup.run_startup_checks(cfg(self.path))
        self.assertIn("DATA_DIR_NOT_WRITABLE", cm.exception.detail)
        self.assertIn(f"uid {os.geteuid()}", cm.exception.detail)
        self.assertEqual(self.path.read_bytes(), before)
        os.chmod(self.dir, stat.S_IRWXU)
        self.assertEqual(self.tree(), names)

    def test_a_nearly_full_disk(self):
        usage = shutil._ntuple_diskusage(100, 99, 1)
        with mock.patch.object(shutil, "disk_usage", return_value=usage):
            with self.assertRaises(ConfigError) as cm:
                startup.run_startup_checks(cfg(self.path))
        self.assertIn("DISK_LOW", cm.exception.detail)

    def test_timezone_data_missing(self):
        with self.assertRaises(ConfigError) as cm:
            startup.run_startup_checks(cfg(self.path, tz="Mars/Olympus"))
        self.assertIn("TZ_MISSING", cm.exception.detail)
        self.assertIn("tzdata", cm.exception.detail)

    def test_every_deployment_problem_is_listed_at_once(self):
        with self.assertRaises(ConfigError) as cm:
            startup.run_startup_checks(cfg(self.dir / "nope" / "x.db", tz="Mars/Olympus"))
        self.assertIn("TZ_MISSING", cm.exception.detail)
        self.assertIn("DATA_DIR_MISSING", cm.exception.detail)

    def test_the_detail_is_operator_text_with_no_database_content(self):
        db.init_db(self.path)
        uid = repo.create_user(self.path, email="secret.person@example.com", name="Secret Name", gender="M", is_km=False, pw_hash="h")
        with self.assertRaises(ConfigError) as cm:
            startup.run_startup_checks(cfg(self.path, tz="Mars/Olympus"))
        self.assertNotIn("secret", cm.exception.detail.lower())
        self.assertTrue(uid)


class Database(StartupCase):
    def test_a_file_that_is_not_a_database_is_reported_and_stays_byte_identical(self):
        payload = b"this is definitely not sqlite " * 200
        self.path.write_bytes(payload)
        with self.assertRaises(CorruptedDataError):
            startup.run_startup_checks(cfg(self.path))
        with self.assertRaises(CorruptedDataError):
            startup.run_startup_checks(cfg(self.path), init=False)
        self.assertEqual(self.path.read_bytes(), payload, "a corrupt file must never be overwritten")
        self.assertEqual(self.tree(), ["baror.db"], "no -wal / -shm / journal files created next to it")

    def test_a_damaged_page_is_an_integrity_error(self):
        db.init_db(self.path)
        uid = repo.create_user(self.path, email="a@example.com", name="A", gender="M", is_km=False, pw_hash="h")
        for i in range(300):
            repo.add_result(self.path, uid, test_date="2026-01-01", pushups=i % 90)
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size = self.path.stat().st_size
        with open(self.path, "r+b") as f:
            f.seek(4096 * 3)
            f.write(b"\xff" * 4096)
        self.assertGreater(size, 4096 * 4)
        with self.assertRaises(StorageError):
            startup.run_startup_checks(cfg(self.path))

    def test_a_damaged_older_database_is_inspected_before_it_is_migrated_and_stays_byte_identical(self):
        build_v1(str(self.path))
        with sqlite3.connect(self.path) as c:
            uid = c.execute("SELECT id FROM users LIMIT 1").fetchall()[0][0]
            c.executemany("INSERT INTO results(user_id,test_date,pushups) VALUES(?,?,?)", [(uid, "2026-01-01", i % 90) for i in range(800)])
        with sqlite3.connect(self.path) as c:                  # after the commit above
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with open(self.path, "r+b") as f:
            f.seek(4096 * 4)
            f.write(b"\xff" * 4096)
        before = self.path.read_bytes()
        with self.assertRaises(StorageError):
            startup.run_startup_checks(cfg(self.path))          # init=True would have migrated it
        self.assertEqual(self.path.read_bytes(), before, "no migration may run on a file that fails its integrity check")
        self.assertEqual(db.user_version(self.path), 1)

    def test_a_truncated_database(self):
        db.init_db(self.path)
        uid = repo.create_user(self.path, email="a@example.com", name="A", gender="M", is_km=False, pw_hash="h")
        for i in range(300):
            repo.add_result(self.path, uid, test_date="2026-01-01", pushups=i % 90)
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with open(self.path, "r+b") as f:
            f.truncate(self.path.stat().st_size // 2)
        with self.assertRaises(StorageError):
            startup.run_startup_checks(cfg(self.path))

    def test_a_database_from_a_newer_app_is_refused_not_downgraded(self):
        db.init_db(self.path)
        with sqlite3.connect(self.path) as c:
            c.execute(f"PRAGMA user_version={db.LATEST_VERSION + 5}")
        with self.assertRaises(StorageError) as cm:
            startup.run_startup_checks(cfg(self.path))
        self.assertIn("newer", cm.exception.detail)
        self.assertEqual(db.user_version(self.path), db.LATEST_VERSION + 5)

    def test_foreign_key_violations_are_an_integrity_error(self):
        db.init_db(self.path)
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES('orphan',999,'2099-01-01T00:00:00Z')")
        with self.assertRaises(StorageError) as cm:
            startup.run_startup_checks(cfg(self.path))
        self.assertIn("integrity", cm.exception.detail)


class RegistrationState(StartupCase):
    def test_summary_never_contains_the_join_secret_or_the_public_address(self):
        c = types.SimpleNamespace(db_path=self.path, tz_name="Asia/Jerusalem", registration_open=False,
                                  signup_cap_per_hour=50, join_link_secret="S" * 40,
                                  public_url="https://baror.example.org")
        state = startup.registration_state(c)
        self.assertEqual(state, {"open": False, "signup_cap_per_hour": 50, "join_links": True, "public_url_set": True})
        self.assertNotIn("S" * 40, str(state))
        self.assertNotIn("baror.example.org", str(state))

    def test_a_duck_typed_config_without_the_new_fields_reads_as_open_and_off(self):
        self.assertEqual(startup.registration_state(cfg(self.path)),
                         {"open": True, "signup_cap_per_hour": None, "join_links": False, "public_url_set": False})

    def test_the_state_is_logged_once_at_start_up_without_secrets(self):
        c = types.SimpleNamespace(db_path=self.path, tz_name="Asia/Jerusalem", registration_open=True,
                                  signup_cap_per_hour=120, join_link_secret="S" * 40, public_url=None)
        with self.assertLogs("baror.startup", level="INFO") as logs:
            startup.run_startup_checks(c)
        text = "\n".join(logs.output)
        self.assertIn("registration_state", text)
        self.assertNotIn("S" * 40, text)


class Maintenance(StartupCase):
    def test_env_flag_and_flag_file(self):
        self.assertFalse(startup.maintenance_active(self.path, env={}))
        for v in ("1", "true", "YES", " 1 "):
            self.assertTrue(startup.maintenance_active(self.path, env={"MAINTENANCE": v}), v)
        for v in ("0", "", "no", "false"):
            self.assertFalse(startup.maintenance_active(self.path, env={"MAINTENANCE": v}), v)
        (self.dir / "MAINTENANCE").touch()
        self.assertTrue(startup.maintenance_active(self.path, env={}))


class Layering(unittest.TestCase):
    def test_startup_and_logging_do_not_import_streamlit_or_the_config_module(self):
        for name in ("startup.py", "logging_setup.py"):
            tree = ast.parse((ROOT / "baror" / name).read_text(encoding="utf-8"))
            mods = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) and n.module else a.name.split(".")[0]
                    for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (getattr(n, "names", None) or [n])}
            self.assertNotIn("streamlit", mods, name)
            self.assertNotRegex((ROOT / "baror" / name).read_text(encoding="utf-8"),
                                r"(?m)^\s*(from baror\.config import|import baror\.config|from baror import [^\n]*\bconfig\b)", name)

    def test_only_the_boundary_outside_the_ui_package_imports_streamlit(self):
        offenders = []
        for f in (ROOT / "baror").rglob("*.py"):
            if f.relative_to(ROOT / "baror").parts[0] == "ui" or f.name == "boundary.py":
                continue
            if "streamlit" in {(n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
                               for n in ast.walk(ast.parse(f.read_text(encoding="utf-8")))
                               if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (getattr(n, "names", None) or [n])}:
                offenders.append(str(f.relative_to(ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
