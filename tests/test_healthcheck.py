"""T8.3: scripts/healthcheck.py against a real local HTTP server and real SQLite files. Abuse cases: stopped / slow /
lying server, missing / empty / garbage / damaged / locked database, proxy variables, hostile environment values; the
output is one line with no traceback, path or database content."""
import http.server
import importlib
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "healthcheck.py"
sys.path.insert(0, str(ROOT / "scripts"))
H = importlib.import_module("healthcheck")


class Stub(http.server.BaseHTTPRequestHandler):
    mode = "ok"

    def do_GET(self):
        mode = type(self).mode
        if mode == "hang":
            time.sleep(3)
        status, body = {"ok": (200, b"ok"), "bad_body": (200, b"not ok"), "error": (500, b"ok"),
                        "empty": (200, b""), "hang": (200, b"ok"), "redirect": (302, b"")}[mode]
        self.send_response(status)
        if mode == "redirect":
            self.send_header("Location", "http://127.0.0.1:9/")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):      # the client gave up on the hanging stub: expected, not noise
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HealthCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = QuietServer(("127.0.0.1", 0), Stub)
        cls.port = cls.srv.server_port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        Stub.mode = "ok"
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.db = str(self.dir / "baror.db")
        self.make_db(self.db, wal=True)

    def make_db(self, path, wal=True, version=4):
        c = sqlite3.connect(path)
        if wal:
            c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE secrets(x TEXT)")
        c.executemany("INSERT INTO secrets VALUES(?)", [(f"SECRET-ROW-{i}",) for i in range(3000)])
        c.execute(f"PRAGMA user_version={version}")
        c.commit()
        c.close()

    def run_cli(self, port=None, db=None, env=None):
        e = {**os.environ, "HEALTH_PORT": str(port or self.port), "DB_PATH": db or self.db}
        e.update(env or {})
        return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, env=e, timeout=60, cwd=self.dir)


class Healthy(HealthCase):
    def test_exit_0_and_one_line(self):
        r = self.run_cli()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "healthy\n", ""))

    def test_a_database_with_a_writer_in_progress_is_still_healthy(self):
        w = sqlite3.connect(self.db, isolation_level=None)
        w.execute("BEGIN IMMEDIATE")
        w.execute("INSERT INTO secrets VALUES('pending')")
        try:
            self.assertEqual(self.run_cli().returncode, 0)
        finally:
            w.execute("ROLLBACK")
            w.close()

    def test_the_probe_never_changes_the_database_or_creates_other_files(self):
        import hashlib
        digest = lambda: hashlib.sha256(Path(self.db).read_bytes()).hexdigest()   # noqa: E731
        before, stat = digest(), os.stat(self.db).st_mtime_ns
        self.run_cli()
        self.assertEqual((digest(), os.stat(self.db).st_mtime_ns), (before, stat))
        # a read-only open of a WAL database may create the -wal / -shm sidecars when none exist (the running app
        # holds them anyway); nothing else may appear
        self.assertLessEqual({p.name for p in self.dir.iterdir()}, {"baror.db", "baror.db-wal", "baror.db-shm"})

    def test_proxy_variables_are_ignored_for_the_loopback_probe(self):
        r = self.run_cli(env={"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9"})
        self.assertEqual(r.returncode, 0, r.stdout)


class StreamlitSide(HealthCase):
    def test_stopped_server(self):
        r = self.run_cli(port=free_port())
        self.assertEqual((r.returncode, r.stdout.startswith("unhealthy: streamlit unreachable"), r.stderr), (1, True, ""))

    def test_a_server_that_does_not_say_ok(self):
        for mode in ("bad_body", "error", "empty", "redirect"):
            Stub.mode = mode
            r = self.run_cli()
            self.assertEqual(r.returncode, 1, mode)
            self.assertEqual(len(r.stdout.strip().splitlines()), 1, mode)

    def test_a_hanging_server_times_out(self):
        Stub.mode = "hang"
        start = time.perf_counter()
        reason = H.check_streamlit(self.port, timeout=0.5)
        self.assertLess(time.perf_counter() - start, 2.5)
        self.assertIn("unreachable", reason)

    def test_bad_port_values_are_unhealthy_not_a_traceback(self):
        for bad in ("abc", "", "-1", "70000", "8501; rm -rf /", "99999999999999999999"):
            r = self.run_cli(env={"HEALTH_PORT": bad})
            self.assertEqual(r.returncode, 1, bad)
            self.assertNotIn("Traceback", r.stdout + r.stderr, bad)
            self.assertEqual(len(r.stdout.strip().splitlines()), 1, bad)


class DatabaseSide(HealthCase):
    def reason(self, path):
        return H.check_db(path, busy_ms=200)

    def test_missing_file_is_not_created(self):
        path = str(self.dir / "nope.db")
        self.assertEqual(self.reason(path), "database file missing")
        self.assertFalse(os.path.exists(path))
        r = self.run_cli(db=path)
        self.assertEqual((r.returncode, r.stdout), (1, "unhealthy: database file missing\n"))

    def test_a_directory_instead_of_a_file(self):
        self.assertEqual(self.reason(str(self.dir)), "database file missing")

    def test_empty_file_is_not_the_apps_database(self):
        path = self.dir / "empty.db"
        path.write_bytes(b"")
        self.assertEqual(self.reason(str(path)), "database not initialised")

    def test_garbage_file(self):
        path = self.dir / "garbage.db"
        path.write_bytes(b"this is not a database " * 500)
        self.assertEqual(self.reason(str(path)), "database is corrupt")
        r = self.run_cli(db=str(path))
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_damaged_page(self):
        path = self.dir / "damaged.db"
        self.make_db(str(path), wal=False)
        with open(path, "r+b") as f:
            f.seek(4096 * 2)
            f.write(b"\xff" * 4096)
        self.assertIn(self.reason(str(path)), ("database is corrupt", "database quick_check failed"))

    def test_truncated_file(self):
        path = self.dir / "short.db"
        self.make_db(str(path), wal=False)
        with open(path, "r+b") as f:
            f.truncate(os.path.getsize(path) // 2)
        self.assertIsNotNone(self.reason(str(path)))

    def test_locked_database_is_unhealthy_only_while_it_stays_locked(self):
        path = str(self.dir / "locked.db")
        self.make_db(path, wal=False)                         # rollback-journal mode: an exclusive lock blocks readers
        c = sqlite3.connect(path, isolation_level=None)
        c.execute("BEGIN EXCLUSIVE")
        try:
            start = time.perf_counter()
            self.assertEqual(self.reason(path), "database locked")
            self.assertLess(time.perf_counter() - start, 3)
        finally:
            c.execute("ROLLBACK")
            c.close()
        self.assertIsNone(self.reason(path))

    def test_hostile_characters_in_the_path_select_exactly_the_file_named(self):
        # '#' / '?' / '%' must not truncate the path or inject URI options: the OTHER file must never be probed
        good = self.dir / "a"
        self.make_db(str(good), wal=False)
        for name in ("a#b.db", "a?b.db", "a%20b.db", "a b.db", "x?mode=rwc.db", "ünï.db"):
            bad = self.dir / name
            bad.write_bytes(b"corrupt " * 500)
            self.assertEqual(self.reason(str(bad)), "database is corrupt", name)
            good_named = self.dir / ("ok-" + name)
            self.make_db(str(good_named), wal=False)
            self.assertIsNone(self.reason(str(good_named)), name)
        self.assertFalse((self.dir / "x").exists(), "no file may be created by an injected mode=rwc")

    def test_unreadable_file(self):
        if not (hasattr(os, "geteuid") and os.geteuid() != 0):
            self.skipTest("root can read everything")
        path = self.dir / "noperm.db"
        self.make_db(str(path), wal=False)
        os.chmod(path, 0)
        self.addCleanup(lambda: os.chmod(path, 0o600))
        self.assertEqual(self.reason(str(path)), "database cannot be read")

    def test_output_never_contains_a_path_or_database_content(self):
        for db in (self.db, str(self.dir / "nope.db")):
            r = self.run_cli(db=db)
            out = r.stdout + r.stderr
            self.assertNotIn("SECRET-ROW", out)
            self.assertNotIn(str(self.dir), out)
            self.assertEqual(len(r.stdout.splitlines()), 1)


class Robustness(HealthCase):
    def test_an_unexpected_failure_is_one_line_exit_1(self):
        original = H.check_db
        H.check_db = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom with /secret/path"))
        try:
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = H.main(self.port, self.db)
        finally:
            H.check_db = original
        self.assertEqual(code, 1)
        self.assertEqual(buf.getvalue(), "unhealthy: internal error (RuntimeError)\n")

    def test_the_script_imports_nothing_from_baror_or_third_party_packages(self):
        import ast
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(mods - set(sys.stdlib_module_names) - {"__future__"}, set())
        self.assertNotIn("baror", mods)


if __name__ == "__main__":
    unittest.main()
