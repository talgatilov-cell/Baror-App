"""Join links (baror/domain/joinlink.py): signed, expiring, revocable by epoch, never raising on bad input."""
import unittest

from baror.domain import joinlink

SECRET = "k" * 40
NOW = 1_800_000_000


class SignAndVerify(unittest.TestCase):
    def test_round_trip(self):
        tok = joinlink.sign(SECRET, 7, 0, NOW + 3600)
        self.assertRegex(tok, r"^7\.0\.\d+\.[0-9a-f]{32}$")
        self.assertEqual(joinlink.parse(tok)[:3], (7, 0, NOW + 3600))
        self.assertEqual(joinlink.verify(SECRET, tok, 0, NOW), 7)

    def test_expired_wrong_epoch_wrong_secret_and_tampering_are_refused(self):
        tok = joinlink.sign(SECRET, 7, 2, NOW + 3600)
        self.assertEqual(joinlink.verify(SECRET, tok, 2, NOW), 7)
        self.assertIsNone(joinlink.verify(SECRET, tok, 2, NOW + 3600), "expiry second is already dead")
        self.assertIsNone(joinlink.verify(SECRET, tok, 2, NOW + 99999))
        self.assertIsNone(joinlink.verify(SECRET, tok, 3, NOW), "a bumped epoch revokes the link")
        self.assertIsNone(joinlink.verify(SECRET, tok, 1, NOW))
        self.assertIsNone(joinlink.verify("z" * 40, tok, 2, NOW))
        pid, epoch, exp, mac = joinlink.parse(tok)
        for forged in (f"8.{epoch}.{exp}.{mac}", f"{pid}.{epoch + 1}.{exp}.{mac}", f"{pid}.{epoch}.{exp + 1}.{mac}",
                       f"{pid}.{epoch}.{exp}.{'0' * 32}", tok[:-1] + ("0" if tok[-1] != "0" else "1")):
            self.assertIsNone(joinlink.verify(SECRET, forged, 2, NOW), forged)

    def test_a_short_secret_is_off(self):
        with self.assertRaises(ValueError):
            joinlink.sign("short", 1, 0, NOW)
        tok = joinlink.sign(SECRET, 1, 0, NOW + 10)
        self.assertIsNone(joinlink.verify("short", tok, 0, NOW))
        self.assertIsNone(joinlink.verify(None, tok, 0, NOW))
        self.assertIsNone(joinlink.verify("", tok, 0, NOW))

    def test_sign_refuses_bad_numbers(self):
        for args in ((0, 0, NOW), (-1, 0, NOW), (1, -1, NOW), (1, 0, -5), (True, 0, NOW), (1, 0, 1.5), ("1", 0, NOW)):
            with self.assertRaises(ValueError, msg=str(args)):
                joinlink.sign(SECRET, *args)

    def test_verify_and_parse_never_raise(self):
        junk = [None, 5, b"x", "", " ", "a.b.c.d", "1.2.3", "1.2.3.4.5", "1.0.9." + "g" * 32, "1.0.9." + "A" * 32,
                "１.0.9." + "a" * 32, "0.0.9." + "a" * 32, "-1.0.9." + "a" * 32, "9" * 40 + ".0.9." + "a" * 32,
                "1.0.9." + "a" * 31, "1.0.9." + "a" * 33, "x" * 5000, "1.0.9.\n" + "a" * 32, "<script>"]
        for token in junk:
            self.assertIsNone(joinlink.parse(token), repr(token)[:30])
            self.assertIsNone(joinlink.verify(SECRET, token, 0, NOW), repr(token)[:30])
        tok = joinlink.sign(SECRET, 1, 0, NOW + 10)
        for epoch in (None, "0", True, 1.0):
            self.assertIsNone(joinlink.verify(SECRET, tok, epoch, NOW))

    def test_the_secret_is_not_in_the_token(self):
        self.assertNotIn(SECRET, joinlink.sign(SECRET, 1, 0, NOW))

    def test_two_platoons_get_different_tokens(self):
        self.assertNotEqual(joinlink.sign(SECRET, 1, 0, NOW), joinlink.sign(SECRET, 2, 0, NOW))


if __name__ == "__main__":
    unittest.main()
