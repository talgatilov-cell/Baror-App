import base64
import time
import unittest

from baror.auth import passwords as P


class Passwords(unittest.TestCase):
    def test_round_trip_and_wrong(self):
        h = P.hash_password("correct horse")
        self.assertTrue(P.verify_password("correct horse", h))
        self.assertFalse(P.verify_password("correct horsf", h))
        self.assertFalse(P.verify_password("", h))

    def test_hebrew_and_nfkc(self):
        h = P.hash_password("סיסמה-חזקה-123")
        self.assertTrue(P.verify_password("סיסמה-חזקה-123", h))
        self.assertFalse(P.verify_password("סיסמה-חזקה-124", h))
        h2 = P.hash_password("pass１２３４５")  # full-width digits
        self.assertTrue(P.verify_password("pass12345", h2))

    def test_never_raises_on_garbage(self):
        for stored in [None, 5, b"x", "", "x", "scrypt$1$2", "bcrypt$a$b$c$d$e", "a$b$c$d$e$f$g",
                       "scrypt$x$8$3$AA==$AA==", "scrypt$32768$8$3$!!!$!!!", "scrypt$32768$8$3$$"]:
            with self.subTest(stored=stored):
                self.assertFalse(P.verify_password("x", stored))
        for pw in [None, 5, b"x", "a" * 10_000]:
            self.assertFalse(P.verify_password(pw, P.hash_password("x")))

    def test_tampered_cost_parameters_are_not_executed(self):
        salt = base64.b64encode(b"s" * 16).decode(); dk = base64.b64encode(b"d" * 32).decode()
        for n, r, p in [(2**14, 8, 64), (2**20, 8, 1), (2**15, 8, 1000), (2**15, 8, 4)]:
            t = time.perf_counter()
            ok = P.verify_password("x", f"scrypt${n}${r}${p}${salt}${dk}")
            self.assertFalse(ok)
            self.assertLess(time.perf_counter() - t, 0.05, f"params {n},{r},{p} must be rejected without hashing")

    def test_wrong_salt_or_digest_length_rejected(self):
        n, r, p = P._CURRENT
        short = base64.b64encode(b"s" * 4).decode(); dk = base64.b64encode(b"d" * 32).decode()
        self.assertFalse(P.verify_password("x", f"scrypt${n}${r}${p}${short}${dk}"))

    def test_unique_salt_and_format(self):
        a, b = P.hash_password("same"), P.hash_password("same")
        self.assertNotEqual(a, b)
        self.assertEqual(a.split("$")[:4], ["scrypt", "32768", "8", "3"])
        self.assertFalse(P.needs_rehash(a))
        self.assertTrue(P.needs_rehash("scrypt$16384$8$1$AA==$AA=="))

    def test_length_bounds(self):
        with self.assertRaises(ValueError):
            P.hash_password("")
        with self.assertRaises(ValueError):
            P.hash_password("a" * 129)
        P.hash_password("a" * 128)

    def test_cost_is_in_expected_range(self):
        t = time.perf_counter(); P.hash_password("x"); dt = time.perf_counter() - t
        self.assertLess(dt, 3.0)
        print(f"\n[measured] hash time {dt:.3f}s")

    def test_dummy_verify_runs_full_cost(self):
        P.verify_against_dummy("x")  # warm cache
        t = time.perf_counter(); self.assertFalse(P.verify_against_dummy("x")); dt = time.perf_counter() - t
        self.assertGreater(dt, 0.02, "dummy verify must perform a real scrypt for timing equalisation")


class PasswordPolicy(unittest.TestCase):
    """Owner decision 2026-11-02: minimum 8 characters (characters, not bytes), maximum 128."""

    def test_minimum_is_eight_characters(self):
        from baror.errors import ValidationError
        P.check_policy("abcdefgh")
        P.check_policy("סיסמה-12")                      # 8 characters, 15 bytes
        for bad in ("", "abcdefg", "1234567", "        "[:7], None, 12345678, b"abcdefgh", "a" * 129):
            with self.assertRaises(ValidationError) as cm:
                P.check_policy(bad)
            self.assertEqual((cm.exception.message_key, cm.exception.field), ("INVALID_PASSWORD", "password"))
        P.check_policy("a" * 128)

    def test_policy_does_not_apply_to_verifying_or_hashing_legacy_passwords(self):
        h = P.hash_password("short")                    # the migration must hash legacy passwords of any length
        self.assertTrue(P.verify_password("short", h))


if __name__ == "__main__":
    unittest.main()
