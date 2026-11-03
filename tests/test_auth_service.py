import datetime as dt
import statistics
import threading
import time
import unittest

from baror.auth import passwords, service, sessions
from baror.errors import AuthError, RateLimitedError
from baror.storage import db, repo
from tests.base import DbCase

T0 = dt.datetime(2026, 11, 2, 8, 0, 0, tzinfo=dt.timezone.utc)


def at(seconds=0, days=0):
    return T0 + dt.timedelta(seconds=seconds, days=days)


class Login(DbCase):
    def setUp(self):
        super().setUp()
        self.uid = self.user("dana@example.com", pw_hash=passwords.hash_password("right-password"))

    def test_success_with_email_variants(self):
        for e in ["dana@example.com", " DANA@Example.com\t"]:
            self.assertEqual(service.login(self.path, e, "right-password", at())["id"], self.uid)

    def test_generic_error_for_wrong_unknown_and_malformed(self):
        keys = set()
        for e, p in [("dana@example.com", "nope"), ("ghost@example.com", "nope"), ("not-an-email", "nope"), (None, None)]:
            with self.assertRaises(AuthError) as cm:
                service.login(self.path, e, p, at())
            keys.add((type(cm.exception).__name__, cm.exception.message_key, str(cm.exception.params)))
        self.assertEqual(len(keys), 1, keys)

    def test_unknown_user_costs_a_real_scrypt(self):
        service.login  # warm caches
        for _ in range(2):
            with self.assertRaises(AuthError):
                service.login(self.path, "ghost@example.com", "x", at())
        known, unknown = [], []
        for _ in range(5):
            t = time.perf_counter()
            with self.assertRaises(AuthError):
                service.login(self.path, "dana@example.com", "wrong", at(1000 * _))  # distinct times: stay below backoff
            known.append(time.perf_counter() - t)
            t = time.perf_counter()
            with self.assertRaises(AuthError):
                service.login(self.path, "ghost@example.com", "wrong", at())
            unknown.append(time.perf_counter() - t)
            service._record_success(self.path, self.uid)
        mk, mu = statistics.median(known), statistics.median(unknown)
        print(f"\n[measured] wrong-password {mk*1000:.0f} ms vs unknown-email {mu*1000:.0f} ms")
        self.assertGreater(mu, mk * 0.5)
        self.assertLess(mu, mk * 2.0)

    def test_backoff_schedule(self):
        self.assertEqual([service.delay_for(n) for n in range(0, 12)], [0, 0, 0, 0, 0, 2, 4, 8, 16, 32, 64, 128])  # failures 1-4 free, 5th -> 2 s
        self.assertEqual(service.delay_for(12), 256)
        self.assertEqual(service.delay_for(13), service.MAX_DELAY_S)  # 512 s capped
        self.assertEqual(service.delay_for(50), service.MAX_DELAY_S)

    def test_backoff_blocks_even_correct_password_then_expires(self):
        for i in range(5):
            with self.assertRaises(AuthError):
                service.login(self.path, "dana@example.com", "bad", at(i))
        with self.assertRaises(RateLimitedError) as cm:
            service.login(self.path, "dana@example.com", "right-password", at(5))
        self.assertGreaterEqual(cm.exception.params["seconds"], 1)
        self.assertEqual(service.login(self.path, "dana@example.com", "right-password", at(60))["id"], self.uid)

    def test_unknown_email_gets_the_same_backoff_as_a_real_account(self):
        """A lock-out must not be an oracle for 'this e-mail has an account'."""
        def run(email):
            seen = []
            for i in range(7):
                try:
                    service.login(self.path, email, "bad", at(i))
                    seen.append("ok")
                except RateLimitedError as e:
                    seen.append(("rate", e.params["seconds"]))
                except AuthError as e:
                    seen.append(("auth", e.message_key))
            return seen
        real, ghost = run("dana@example.com"), run("ghost@example.com")
        self.assertEqual(real, ghost)
        self.assertEqual([x[0] for x in real], ["auth"] * 5 + ["rate", "auth"])   # 5th failure at t=4 locks 2 s: t=5 refused, t=6 is a real attempt

    def test_unknown_email_lockout_expires_and_is_per_email(self):
        for i in range(5):
            with self.assertRaises(AuthError):
                service.login(self.path, "ghost@example.com", "bad", at(i))
        with self.assertRaises(RateLimitedError):
            service.login(self.path, "GHOST@example.com ", "bad", at(5))     # normalised key
        with self.assertRaises(AuthError) as cm:
            service.login(self.path, "other@example.com", "bad", at(5))
        self.assertNotIsInstance(cm.exception, RateLimitedError)
        with self.assertRaises(AuthError) as cm:
            service.login(self.path, "ghost@example.com", "bad", at(60))     # delay (2 s) has passed
        self.assertNotIsInstance(cm.exception, RateLimitedError)

    def test_unknown_email_state_is_bounded(self):
        old = service.GHOST_MAX_KEYS
        service.GHOST_MAX_KEYS = 50
        try:
            for i in range(200):
                with self.assertRaises(AuthError):
                    service.login(self.path, f"g{i}@example.com", "bad", at())
            self.assertLessEqual(len(service._ghosts._d), 50)
        finally:
            service.GHOST_MAX_KEYS = old

    def test_success_resets_counter(self):
        for i in range(3):
            with self.assertRaises(AuthError):
                service.login(self.path, "dana@example.com", "bad", at(i))
        service.login(self.path, "dana@example.com", "right-password", at(10))
        self.assertEqual(repo.get_login_record(self.path, "dana@example.com")["failed_logins"], 0)

    def test_concurrent_failures_are_all_counted(self):
        barrier = threading.Barrier(12)

        def attempt():
            barrier.wait()
            try:
                service.login(self.path, "dana@example.com", "bad", at())
            except (AuthError, RateLimitedError):
                pass
        ts = [threading.Thread(target=attempt) for _ in range(12)]
        [t.start() for t in ts]; [t.join() for t in ts]
        # a rate-limited attempt is not counted, so the count is between FREE_FAILURES+1 and 12 and never lost below
        n = repo.get_login_record(self.path, "dana@example.com")["failed_logins"]
        self.assertGreaterEqual(n, service.FREE_FAILURES + 1)

    def test_rehash_on_login_keeps_other_sessions(self):
        # build a real hash with the old cost by temporarily accepting it
        import hashlib, base64, secrets
        salt = secrets.token_bytes(16)
        dk = hashlib.scrypt(b"legacy-pw", salt=salt, n=2**14, r=8, p=1, dklen=32)
        stored = "$".join(["scrypt", "16384", "8", "1", base64.b64encode(salt).decode(), base64.b64encode(dk).decode()])
        uid = self.user("legacy@example.com", pw_hash=stored)
        tok = sessions.issue(self.path, uid, 14, at())
        passwords._ACCEPTED.add((2**14, 8, 1))
        try:
            self.assertEqual(service.login(self.path, "legacy@example.com", "legacy-pw", at())["id"], uid)
        finally:
            passwords._ACCEPTED.discard((2**14, 8, 1))
        new = repo.get_login_record(self.path, "legacy@example.com")["pw_hash"]
        self.assertFalse(passwords.needs_rehash(new))
        self.assertIsNotNone(sessions.resolve(self.path, tok, at()), "transparent rehash must not log out other devices")


class Sessions(DbCase):
    def setUp(self):
        super().setUp()
        self.uid = self.user("s@example.com")

    def test_token_shape_and_hash_only_stored(self):
        tok = sessions.issue(self.path, self.uid, 14, at())
        self.assertRegex(tok, r"^[A-Za-z0-9_-]{43}$")
        with db.reader(self.path) as c:
            stored = [r[0] for r in c.execute("SELECT token_hash FROM sessions")]
        self.assertEqual(stored, [sessions.hash_token(tok)])
        self.assertNotIn(tok, stored[0])

    def test_resolve_valid_expired_revoked_unknown_malformed(self):
        tok = sessions.issue(self.path, self.uid, 14, at())
        self.assertEqual(sessions.resolve(self.path, tok, at(days=13))["id"], self.uid)
        self.assertIsNone(sessions.resolve(self.path, tok, at(days=14)), "expiry is exclusive at the boundary")
        self.assertIsNone(sessions.resolve(self.path, tok, at(days=15)))
        sessions.revoke(self.path, tok)
        self.assertIsNone(sessions.resolve(self.path, tok, at()))
        for bad in [None, "", "short", "A" * 44, "A" * 42 + "!", 5, b"x" * 43, "' OR 1=1 --" + "A" * 32]:
            with self.subTest(bad=bad):
                self.assertIsNone(sessions.resolve(self.path, bad, at()))
                sessions.revoke(self.path, bad)  # must not raise

    def test_resolve_does_not_expose_hash_or_password(self):
        tok = sessions.issue(self.path, self.uid, 14, at())
        self.assertNotIn("pw_hash", sessions.resolve(self.path, tok, at()))

    def test_two_users_tokens_never_cross(self):
        other = self.user("o@example.com")
        t1, t2 = sessions.issue(self.path, self.uid, 14, at()), sessions.issue(self.path, other, 14, at())
        self.assertEqual(sessions.resolve(self.path, t1, at())["email"], "s@example.com")
        self.assertEqual(sessions.resolve(self.path, t2, at())["email"], "o@example.com")

    def test_logout_everywhere_and_password_change_revoke(self):
        a, b = sessions.issue(self.path, self.uid, 14, at()), sessions.issue(self.path, self.uid, 14, at())
        sessions.revoke_all(self.path, self.uid)
        self.assertIsNone(sessions.resolve(self.path, a, at())); self.assertIsNone(sessions.resolve(self.path, b, at()))
        c = sessions.issue(self.path, self.uid, 14, at())
        repo.set_password_hash(self.path, self.uid, "scrypt$x")
        self.assertIsNone(sessions.resolve(self.path, c, at()))

    def test_account_deletion_kills_sessions(self):
        tok = sessions.issue(self.path, self.uid, 14, at())
        repo.delete_user(self.path, self.uid)
        self.assertIsNone(sessions.resolve(self.path, tok, at()))

    def test_active_session_cap(self):
        toks = [sessions.issue(self.path, self.uid, 14, at(seconds=i * 3600)) for i in range(sessions.MAX_ACTIVE_PER_USER + 3)]
        alive = [t for t in toks if sessions.resolve(self.path, t, at(days=1))]
        self.assertEqual(len(alive), sessions.MAX_ACTIVE_PER_USER)
        self.assertEqual(alive, toks[3:], "oldest sessions are the ones dropped")

    def test_sliding_expiry_only_extends_in_second_half(self):
        tok = sessions.issue(self.path, self.uid, 14, at())
        sessions.resolve(self.path, tok, at(days=3), slide_days=14)          # 11 d left of 14 -> no write
        with db.reader(self.path) as c:
            e1 = c.execute("SELECT expires_at FROM sessions").fetchone()[0]
        self.assertEqual(e1, "2026-11-16T08:00:00Z")
        sessions.resolve(self.path, tok, at(days=10), slide_days=14)         # 4 d left (< 7) -> extend
        with db.reader(self.path) as c:
            e2 = c.execute("SELECT expires_at FROM sessions").fetchone()[0]
        self.assertEqual(e2, "2026-11-26T08:00:00Z")
        self.assertIsNotNone(sessions.resolve(self.path, tok, at(days=20)))

    def test_purge(self):
        a = sessions.issue(self.path, self.uid, 1, at()); b = sessions.issue(self.path, self.uid, 30, at())
        sessions.revoke(self.path, b)
        self.assertEqual(sessions.purge_expired(self.path, at(days=2)), 2)


class ChangePassword(DbCase):
    def setUp(self):
        super().setUp()
        self.uid = self.user("dana@example.com", pw_hash=passwords.hash_password("old-password-1"))
        repo.set_password_hash(self.path, self.uid, passwords.hash_password("old-password-1"), must_change_pw=True,
                               revoke_sessions=False)

    def test_success_clears_the_flag_revokes_every_session_and_swaps_the_password(self):
        t1, t2 = sessions.issue(self.path, self.uid), sessions.issue(self.path, self.uid)
        service.change_password(self.path, self.uid, "old-password-1", "new-password-2", at())
        self.assertFalse(repo.get_user(self.path, self.uid)["must_change_pw"])
        self.assertIsNone(sessions.resolve(self.path, t1)); self.assertIsNone(sessions.resolve(self.path, t2))
        self.assertEqual(service.login(self.path, "dana@example.com", "new-password-2", at())["id"], self.uid)
        with self.assertRaises(AuthError):
            service.login(self.path, "dana@example.com", "old-password-1", at())

    def test_wrong_current_password_is_the_generic_error_and_counts_towards_the_backoff(self):
        for i in range(5):
            with self.assertRaises(AuthError):
                service.change_password(self.path, self.uid, "nope", "new-password-2", at(i))
        with self.assertRaises(RateLimitedError):
            service.change_password(self.path, self.uid, "old-password-1", "new-password-2", at(5))
        self.assertTrue(repo.get_user(self.path, self.uid)["must_change_pw"], "nothing changed")

    def test_policy_and_sameness_are_checked_without_touching_the_counter(self):
        from baror.errors import ValidationError
        for new in ("short", "", "x" * 129, None):
            with self.assertRaises(ValidationError):
                service.change_password(self.path, self.uid, "old-password-1", new, at())
        with self.assertRaises(ValidationError) as cm:
            service.change_password(self.path, self.uid, "old-password-1", "old-password-1", at())
        self.assertEqual(cm.exception.message_key, "PASSWORD_UNCHANGED")
        self.assertEqual(repo.get_login_record(self.path, "dana@example.com")["failed_logins"], 0)

    def test_unknown_deactivated_and_malformed_users_get_the_generic_error(self):
        from baror.storage import ops
        ops.deactivate_user(self.path, self.uid)
        for who in (self.uid, 9999, None, True, "1"):
            with self.assertRaises(AuthError):
                service.change_password(self.path, who, "old-password-1", "new-password-2", at())
        self.assertEqual(repo.get_login_record(self.path, "dana@example.com")["pw_hash"] is not None, True)

    def test_other_users_sessions_are_untouched(self):
        other = self.user("omer@example.com", pw_hash=passwords.hash_password("omer-password"))
        tok = sessions.issue(self.path, other)
        service.change_password(self.path, self.uid, "old-password-1", "new-password-2", at())
        self.assertEqual(sessions.resolve(self.path, tok)["id"], other)


if __name__ == "__main__":
    unittest.main()
