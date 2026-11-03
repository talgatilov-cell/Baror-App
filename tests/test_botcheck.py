import unittest

from baror.auth.botcheck import BotCheck


class Clock:
    def __init__(self): self.t = 5000.0
    def __call__(self): return self.t


class BotCheckTests(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.b = BotCheck(b"s" * 32, clock=self.c)

    def test_ok_then_replay(self):
        t = self.b.issue("sess1")
        self.c.t += 3
        self.assertEqual(self.b.verify(t, "sess1"), (True, "ok"))
        self.assertEqual(self.b.verify(t, "sess1"), (False, "replay"))

    def test_honeypot(self):
        t = self.b.issue("s"); self.c.t += 5
        self.assertEqual(self.b.verify(t, "s", "http://spam"), (False, "honeypot"))

    def test_too_fast(self):
        t = self.b.issue("s"); self.c.t += 0.5
        self.assertEqual(self.b.verify(t, "s"), (False, "too_fast"))

    def test_expired(self):
        t = self.b.issue("s"); self.c.t += 3601
        self.assertEqual(self.b.verify(t, "s"), (False, "expired"))

    def test_wrong_session(self):
        t = self.b.issue("s1"); self.c.t += 5
        self.assertEqual(self.b.verify(t, "s2"), (False, "signature"))

    def test_tampered_and_malformed(self):
        t = self.b.issue("s"); self.c.t += 5
        p, sig = t.split(".")
        self.assertFalse(self.b.verify(p + ".AAAA", "s")[0])
        self.assertEqual(self.b.verify("garbage", "s"), (False, "malformed"))
        self.assertFalse(self.b.verify(sig + "." + sig, "s")[0])
        self.assertEqual(self.b.verify(None, "s")[0], False)

    def test_forged_timestamp_other_secret(self):
        other = BotCheck(b"x" * 32, clock=self.c)
        t = other.issue("s"); self.c.t += 5
        self.assertEqual(self.b.verify(t, "s"), (False, "signature"))

    def test_short_secret_rejected(self):
        with self.assertRaises(ValueError):
            BotCheck(b"short")

    def test_failed_verify_does_not_burn_token(self):
        t = self.b.issue("s"); self.c.t += 0.5
        self.assertEqual(self.b.verify(t, "s")[1], "too_fast")
        self.c.t += 5
        self.assertEqual(self.b.verify(t, "s"), (True, "ok"))


if __name__ == "__main__":
    unittest.main()
