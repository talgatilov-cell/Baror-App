import os
import signal
import subprocess
import sys
import threading
import time
import unittest

from baror.errors import ConflictError
from baror.storage import db, repo
from tests.base import DbCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def worker(*args, **kw):
    return subprocess.Popen([sys.executable, "-m", "tests.workers", *map(str, args)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


class Threads(DbCase):
    def test_20_threads_inserting_results_lose_nothing(self):
        users = [self.user(f"u{i}@example.com") for i in range(20)]
        errors = []

        def run(uid):
            try:
                for i in range(25):
                    repo.add_result(self.path, uid, test_date="2026-01-01", pushups=i)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        ts = [threading.Thread(target=run, args=(u,)) for u in users]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        for u in users:
            self.assertEqual(len(repo.list_results(self.path, u)), 25)

    def test_20_concurrent_distinct_registrations_all_persist(self):
        errors, barrier = [], threading.Barrier(20)

        def reg(i):
            barrier.wait()
            try:
                repo.create_user(self.path, email=f"n{i}@example.com", name="n", gender="F", is_km=False, pw_hash="h")
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        ts = [threading.Thread(target=reg, args=(i,)) for i in range(20)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], 20)

    def test_same_email_race_exactly_one_winner_threads(self):
        """BR-03c (account takeover by overwrite) cannot happen: UNIQUE is enforced by the database."""
        outcomes, barrier = [], threading.Barrier(20)

        def reg(i):
            barrier.wait()
            try:
                repo.create_user(self.path, email="same@example.com", name=f"racer{i}", gender="M", is_km=False,
                                 pw_hash=f"hash{i}")
                outcomes.append("ok")
            except ConflictError:
                outcomes.append("conflict")

        ts = [threading.Thread(target=reg, args=(i,)) for i in range(20)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(outcomes.count("ok"), 1)
        self.assertEqual(outcomes.count("conflict"), 19)
        # the stored account is the winner's, intact (name and hash belong to the same racer)
        rec = repo.get_login_record(self.path, "same@example.com")
        winner = rec["pw_hash"].removeprefix("hash")
        with db.reader(self.path) as c:
            name = c.execute("SELECT name FROM users WHERE email='same@example.com'").fetchone()[0]
        self.assertEqual(name, f"racer{winner}")

    def test_reader_never_sees_half_a_transaction(self):
        uid = self.user()
        stop, seen_odd, errors = threading.Event(), [], []

        def writer():
            try:
                for _ in range(150):
                    with db.transaction(self.path) as c:  # two rows per transaction -> count must always be even
                        c.execute("INSERT INTO results(user_id,test_date,pushups) VALUES(?,?,1)", (uid, "2026-01-01"))
                        c.execute("INSERT INTO results(user_id,test_date,pushups) VALUES(?,?,2)", (uid, "2026-01-01"))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
            finally:
                stop.set()

        def reader():
            try:
                while not stop.is_set():
                    n = len(repo.list_results(self.path, uid))
                    if n % 2:
                        seen_odd.append(n)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        ts = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(seen_odd, [])
        self.assertEqual(len(repo.list_results(self.path, uid)), 300)


class Processes(DbCase):
    def test_8_processes_x_50_inserts_lose_nothing(self):
        uid = self.user()
        procs = [worker("insert_results", self.path, uid, 50) for _ in range(8)]
        outs = [p.communicate(timeout=120) for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs), [o[1][-300:] for o in outs])
        self.assertEqual(len(repo.list_results(self.path, uid)), 400)
        self.assertEqual(db.integrity_check(self.path), [])

    def test_same_email_race_across_processes(self):
        procs = [worker("register_same", self.path, "race@example.com") for _ in range(10)]
        outs = [p.communicate(timeout=120)[0].strip() for p in procs]
        self.assertEqual(sorted(outs).count("OK"), 1, outs)
        self.assertEqual(sorted(outs).count("ConflictError"), 9, outs)


class CrashSafety(DbCase):
    def _kill(self, p):
        p.send_signal(signal.SIGKILL); p.wait(timeout=10)
        p.stdout.close(); p.stderr.close()

    def test_kill9_with_uncommitted_transaction_leaves_database_intact(self):
        uid = self.user()
        repo.add_result(self.path, uid, test_date="2026-01-01", pushups=7)
        p = worker("open_tx_and_hang", self.path, uid)
        self.assertEqual(p.stdout.readline().strip(), "READY")
        self._kill(p)
        self.assertEqual(db.integrity_check(self.path), [])
        rows = repo.list_results(self.path, uid)
        self.assertEqual([r["pushups"] for r in rows], [7], "uncommitted rows must vanish, committed row must stay")
        repo.add_result(self.path, uid, test_date="2026-01-02", pushups=8)  # lock was released by the kill

    def test_kill9_during_stream_of_commits_is_atomic(self):
        uid = self.user()
        for delay in (0.15, 0.4, 0.7, 1.0, 1.3):
            p = worker("commit_batches_forever", self.path, uid, 5)
            self.assertEqual(p.stdout.readline().strip(), "READY")
            time.sleep(delay)
            self._kill(p)
            self.assertEqual(db.integrity_check(self.path), [], f"after kill at {delay}s")
            n = len(repo.list_results(self.path, uid))
            self.assertEqual(n % 5, 0, f"a batch was half-applied (count={n})")
        self.assertGreater(n, 0)

    def test_reopen_after_kill_is_clean(self):
        uid = self.user()
        p = worker("commit_batches_forever", self.path, uid, 3)
        self.assertEqual(p.stdout.readline().strip(), "READY")
        time.sleep(0.5); self._kill(p)
        # NOTE: whether a -wal file survives depends on whether the kill landed inside a transaction, so we do not
        # assert on it (an earlier version of this test did and was non-deterministic).
        self.assertEqual(db.init_db(self.path), db.LATEST_VERSION)
        self.assertEqual(db.integrity_check(self.path), [])


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "needs root to chown and drop to uid 10001")
class VolumePermissions(DbCase):
    """Stand-in for the Docker volume check: the image runs as uid 10001; a host bind mount owned by root breaks it."""
    UID = 10001

    def run_as(self, mode, path):
        p = worker(mode, path, self.UID)
        out, err = p.communicate(timeout=60)
        return out.strip(), err.strip()

    def setUp(self):
        super().setUp()
        self.vol = os.path.join(self.dir, "vol"); os.mkdir(self.vol)
        os.chmod(self.dir, 0o755)

    def test_root_owned_volume_fails_with_storage_error_not_traceback(self):
        os.chown(self.vol, 0, 0); os.chmod(self.vol, 0o755)
        out, err = self.run_as("as_uid_init", os.path.join(self.vol, "baror.db"))
        self.assertEqual(out, "ERR StorageError", (out, err))

    def test_volume_owned_by_10001_works(self):
        os.chown(self.vol, self.UID, self.UID); os.chmod(self.vol, 0o700)
        out, err = self.run_as("as_uid_init", os.path.join(self.vol, "baror.db"))
        self.assertEqual(out, "INIT_OK", (out, err))
        self.assertEqual(os.stat(os.path.join(self.vol, "baror.db")).st_uid, self.UID)

    def test_database_created_by_root_is_unreadable_for_10001_after_chmod(self):
        path = os.path.join(self.vol, "baror.db")
        db.init_db(path)  # as root
        os.chown(self.vol, 0, 0)
        for f in os.listdir(self.vol):
            os.chmod(os.path.join(self.vol, f), 0o600)
        os.chmod(self.vol, 0o755)
        out, _ = self.run_as("as_uid_read", path)
        self.assertEqual(out, "ERR StorageError")

    def test_readonly_directory_with_existing_db_readers(self):
        """Documents a real WAL limitation: a reader needs to create -shm; in a read-only dir that fails
        unless the -wal/-shm files already exist and are writable."""
        path = os.path.join(self.vol, "baror.db")
        db.init_db(path)
        self.user_in(path)
        for f in os.listdir(self.vol):
            os.chown(os.path.join(self.vol, f), self.UID, self.UID)
        os.chown(self.vol, 0, 0); os.chmod(self.vol, 0o555)
        out, err = self.run_as("as_uid_read", path)
        print(f"\n[observed] read-only dir, db files owned by 10001 -> {out!r}")
        self.assertTrue(out.startswith("READ_OK") or out == "ERR StorageError", (out, err))

    def user_in(self, path):
        repo.create_user(path, email="a@example.com", name="n", gender="M", is_km=False, pw_hash="h")


if __name__ == "__main__":
    unittest.main()
