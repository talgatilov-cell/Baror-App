"""T4c: per-IP limiter and bot check wired into sign-in and registration. Abuse matrix: address spoofing, blocked
attempts never reach scrypt, a whole platoon behind one address is never locked out, honeypot, fill time, token reuse."""
import math
import os
import tempfile
import types
import unittest
from unittest import mock

import streamlit as st
from streamlit.testing.v1 import AppTest

from baror import config
from baror.auth import passwords, service
from baror.errors import ConfigError
from baror.messages_he import ERRORS, render_error, t
from baror.storage import allowlist, db, repo
from baror.ui import guards
from baror.ui import session as ui_session
from tests.test_app import APP, PW, AppCase, btn


def ctx(ip=None, xff=None, cookies=None):
    headers = {} if xff is None else {"X-Forwarded-For": xff}
    return types.SimpleNamespace(ip_address=ip, headers=headers, cookies=cookies or {})


class Cfg(unittest.TestCase):
    def test_trusted_proxies_are_exact_addresses_only(self):
        self.assertEqual(config.from_env({"TRUSTED_PROXIES": " 172.18.0.1 , ::1,[2001:db8::1]"}).trusted_proxies,
                         ("172.18.0.1", "::1", "2001:db8::1"))
        self.assertEqual(config.from_env({}).trusted_proxies, ())
        for bad in ("*", "0.0.0.0/0", "10.0.0.0/8", "proxy.example.com", "172.18.0.1,*", "999.1.1.1", "1.2.3"):
            with self.assertRaises(ConfigError, msg=bad):
                config.from_env({"TRUSTED_PROXIES": bad})

    def test_forwarded_allow_ips_star_is_refused_at_startup(self):
        for bad in ("*", "127.0.0.1,*", " * "):
            with self.assertRaises(ConfigError, msg=bad) as cm:
                config.from_env({"FORWARDED_ALLOW_IPS": bad})
            self.assertIn("TRUSTED_PROXIES", cm.exception.detail)
        config.from_env({"FORWARDED_ALLOW_IPS": "127.0.0.1"})
        config.from_env({"FORWARDED_ALLOW_IPS": "172.18.0.1"})

    def test_the_cache_key_changes_with_the_new_variables(self):
        a, b = config.env_key({"TRUSTED_PROXIES": "1.2.3.4"}), config.env_key({"TRUSTED_PROXIES": "1.2.3.5"})
        self.assertNotEqual(a, b)
        self.assertNotEqual(config.env_key({}), config.env_key({"FORWARDED_ALLOW_IPS": "*"}))

    def test_bot_min_seconds_is_bounded(self):
        self.assertEqual(config.from_env({}).bot_min_seconds, 2.0)
        self.assertEqual(config.from_env({"BAROR_BOT_MIN_SECONDS": "0"}).bot_min_seconds, 0.0)
        for bad in ("-1", "61", "x", "nan"):
            with self.assertRaises(ConfigError, msg=bad):
                config.from_env({"BAROR_BOT_MIN_SECONDS": bad})


class ClientAddress(AppCase):
    """guards.client_ip: the peer, and X-Forwarded-For only from a configured proxy (right-most untrusted hop)."""

    def resolve(self, proxies, **kw):
        cfg = config.from_env({"TRUSTED_PROXIES": proxies})
        with mock.patch.object(st, "context", ctx(**kw)):
            return guards.client_ip(cfg)

    def test_without_proxies_the_header_is_ignored_completely(self):
        for xff in ("1.1.1.1", "1.1.1.1, 2.2.2.2", "garbage", "", "::1"):
            self.assertEqual(self.resolve("", ip="203.0.113.9", xff=xff), "203.0.113.9")

    def test_an_untrusted_peer_cannot_borrow_trust_by_sending_the_header(self):
        self.assertEqual(self.resolve("172.18.0.1", ip="198.51.100.7", xff="10.9.9.9"), "198.51.100.7")

    def test_a_trusted_proxy_gives_the_right_most_untrusted_hop_and_a_forged_left_part_is_ignored(self):
        self.assertEqual(self.resolve("172.18.0.1", ip="172.18.0.1", xff="203.0.113.5"), "203.0.113.5")
        self.assertEqual(self.resolve("172.18.0.1", ip="172.18.0.1", xff="6.6.6.6, 203.0.113.5"), "203.0.113.5",
                         "the client's own claim (left) never wins over what the proxy appended (right)")
        self.assertEqual(self.resolve("172.18.0.1,10.0.0.2", ip="172.18.0.1", xff="203.0.113.5, 10.0.0.2"), "203.0.113.5")

    def test_garbage_or_missing_header_from_a_trusted_proxy_falls_back_to_the_peer(self):
        self.assertEqual(self.resolve("172.18.0.1", ip="172.18.0.1", xff="not-an-ip"), "172.18.0.1")
        self.assertEqual(self.resolve("172.18.0.1", ip="172.18.0.1", xff=None), "172.18.0.1")

    def test_operator_is_warned_once_when_a_proxy_seems_untrusted_and_the_warning_has_no_client_data(self):
        guards._warned_proxy = False
        with self.assertLogs("baror.guards", "WARNING") as cm:
            self.resolve("", ip="172.18.0.1", xff="203.0.113.5")
            self.resolve("", ip="172.18.0.1", xff="203.0.113.6")
        self.assertEqual(len(cm.output), 1, "once per process")
        self.assertIn("TRUSTED_PROXIES", cm.output[0])
        for secret in ("203.0.113", "172.18.0.1"):
            self.assertNotIn(secret, cm.output[0])
        guards._warned_proxy = False
        with self.assertNoLogs("baror.guards", "WARNING"):
            self.resolve("172.18.0.1", ip="172.18.0.1", xff="203.0.113.5")     # configured: no warning
            self.resolve("", ip=None, xff="203.0.113.5")                         # loopback peer: no warning
            self.resolve("", ip="172.18.0.1", xff=None)                          # no header: no warning

    def test_loopback_is_reported_as_none_by_streamlit_and_is_handled(self):
        self.assertEqual(self.resolve("", ip=None), "127.0.0.1")


LOGIN_LIMIT = guards.LIMITS["login"][0]        # the limit is exceeded by the NEXT failure, so LIMIT + 1 failures
REGISTER_LIMIT = guards.LIMITS["register"][0]  # are allowed and counted, and the attempt after that is refused


class IpLimitWired(AppCase):
    def setUp(self):
        super().setUp()
        self.cadet()
        verify = mock.patch.object(passwords, "verify_password", wraps=passwords.verify_password)
        self.verify = verify.start()
        self.addCleanup(verify.stop)

    def attempt(self, email="dana@example.com", pw="nope", ip="203.0.113.9", xff=None):
        with mock.patch.object(st, "context", ctx(ip=ip, xff=xff)):
            return self.sign_in(email, pw)

    def hashes(self):
        return self.verify.call_count          # every scrypt goes through verify_password (the dummy one too)

    def test_the_attempt_after_the_limit_is_refused_before_any_hashing(self):
        for i in range(LOGIN_LIMIT + 1):
            self.attempt(email=f"user{i}@example.com")
        done = self.hashes()
        self.assertEqual(done, LOGIN_LIMIT + 1)
        at = self.attempt(email="dana@example.com", pw=PW)                 # even the RIGHT password is refused now
        self.assertEqual(self.hashes(), done, "a blocked attempt must not cost a scrypt")
        self.assertIn(ERRORS["RATE_LIMITED"].title, at.error[0].value)
        self.assertFalse(self.in_app(at))
        self.assertEqual(self.set_cookie.call_count, 0)

    def test_the_message_is_the_same_whatever_the_account(self):
        for i in range(LOGIN_LIMIT + 1):
            self.attempt(email=f"user{i}@example.com")
        import re
        shown = {re.sub(r"\d+|שנייה אחת|שתי שניות|שתי דקות|דקה אחת", "N", self.attempt(email=e).error[0].value)
                 for e in ("dana@example.com", "ghost@example.com", "not-an-email", "")}
        self.assertEqual(len(shown), 1)

    def test_other_addresses_are_not_affected(self):
        for i in range(LOGIN_LIMIT + 1):
            self.attempt(email=f"user{i}@example.com")
        at = self.attempt(email="dana@example.com", pw=PW, ip="198.51.100.20")
        self.assertTrue(self.in_app(at))

    def test_a_spoofed_header_does_not_dodge_the_limit_when_no_proxy_is_trusted(self):
        for i in range(LOGIN_LIMIT + 1):
            self.attempt(email=f"user{i}@example.com", xff=f"10.0.{i}.1")        # a fresh fake address every time
        at = self.attempt(email="dana@example.com", pw=PW, xff="10.99.99.99")
        self.assertIn(ERRORS["RATE_LIMITED"].title, at.error[0].value)

    def test_with_a_trusted_proxy_each_real_client_has_their_own_bucket(self):
        os.environ["TRUSTED_PROXIES"] = "172.18.0.1"
        for i in range(LOGIN_LIMIT + 1):
            self.attempt(email=f"user{i}@example.com", ip="172.18.0.1", xff="203.0.113.5")
        self.assertIn(ERRORS["RATE_LIMITED"].title,
                      self.attempt(pw=PW, ip="172.18.0.1", xff="203.0.113.5").error[0].value)
        self.assertTrue(self.in_app(self.attempt(pw=PW, ip="172.18.0.1", xff="203.0.113.6")),
                        "another client behind the same proxy is unaffected")

    def test_successful_sign_ins_do_not_count_against_the_address(self):
        for _ in range(80):
            self.assertTrue(self.in_app(self.attempt(pw=PW)))

    def test_a_platoon_behind_one_address_is_never_locked_out(self):
        """45 cadets on one NAT address: each registers, makes one typo, signs in. Nobody is refused."""
        pid = 0
        for i in range(45):
            allowlist.add_entry(self.path, f"c{i}@example.com")
        for i in range(45):
            with mock.patch.object(st, "context", ctx(ip="203.0.113.9")):
                at = self.register(f"c{i}@example.com", name="Cadet " + chr(ord("A") + i % 26) + chr(ord("A") + i // 26), gender="gender_male", password=PW)
            self.assertFalse(at.error, f"cadet {i} was refused at registration")
        for i in range(45):
            bad = self.attempt(email=f"c{i}@example.com", pw="typo")
            self.assertEqual(ERRORS["AUTH_FAILED"].title in bad.error[0].value, True, f"cadet {i}")
            good = self.attempt(email=f"c{i}@example.com", pw=PW)
            self.assertTrue(self.in_app(good), f"cadet {i} could not sign in after one typo")
        self.assertEqual(pid, 0)

    def test_registration_attempts_are_limited_per_address_before_hashing(self):
        for i in range(REGISTER_LIMIT + 1):
            with mock.patch.object(st, "context", ctx(ip="203.0.113.9")):
                self.register(f"nobody{i}@example.com", gender="gender_male", password="long enough 1")
        before = self.hashes() + 0
        with mock.patch.object(st, "context", ctx(ip="203.0.113.9")), mock.patch.object(service, "register") as reg:
            at = self.register("another@example.com", gender="gender_male", password="long enough 1")
        reg.assert_not_called()
        self.assertIn(ERRORS["RATE_LIMITED"].title, at.error[0].value)
        with mock.patch.object(st, "context", ctx(ip="198.51.100.1")):
            at = self.register("another@example.com", gender="gender_male", password="long enough 1")
        self.assertEqual(at.error[0].value, render_error("REGISTRATION_DENIED"), "another address is still served")
        self.assertEqual(before, self.hashes())

    def test_limiter_memory_stays_bounded_under_a_flood_of_addresses(self):
        lim = guards.limiter()
        for i in range(2000):
            lim.record_login_failure(f"10.{i // 256}.{i % 256}.1", f"u{i}@example.com")
        self.assertLessEqual(lim.size(), 3 * 2000)
        small = guards.IpLimiter(max_keys=100, limits=guards.LIMITS)
        for i in range(5000):
            small.record_login_failure(f"10.{i // 256}.{i % 256}.1", f"u{i}@example.com")
        self.assertLessEqual(small.size(), 200)


class BotCheckWired(AppCase):
    def setUp(self):
        super().setUp()
        os.environ["BAROR_BOT_MIN_SECONDS"] = "2"
        self.cadet()
        self.clock = [1_000_000.0]
        patcher = mock.patch.object(guards, "_now", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        verify = mock.patch.object(service, "login", wraps=service.login)
        self.login = verify.start()
        self.addCleanup(verify.stop)

    def open(self):
        return AppTest.from_file(APP, default_timeout=60).run()

    def submit(self, at, email="dana@example.com", pw=PW, honeypot=""):
        at.text_input(key="login_email").set_value(email)
        at.text_input(key="login_pass").set_value(pw)
        at.text_input(key="hp_login_value").set_value(honeypot)
        btn(at, t("btn_login")).click()
        return at.run()

    def test_too_fast_is_refused_without_hashing_then_a_retry_after_the_minimum_works(self):
        at = self.open()
        self.clock[0] += 0.5
        at = self.submit(at)
        self.assertEqual(at.error[0].value, t("bot_check_failed"))
        self.assertEqual(self.login.call_count, 0, "refused before the password hash")
        self.assertFalse(self.in_app(at))
        self.clock[0] += 5                                   # the token was renewed at the refusal: wait out its minimum
        at = self.submit(at)
        self.assertTrue(self.in_app(at))
        self.assertEqual(self.login.call_count, 1)

    def test_a_filled_honeypot_is_refused_even_with_the_right_password_and_plenty_of_time(self):
        at = self.open()
        self.clock[0] += 30
        at = self.submit(at, honeypot="http://spam.example")
        self.assertEqual(at.error[0].value, t("bot_check_failed"))
        self.assertEqual(self.login.call_count, 0)
        self.assertFalse(self.in_app(at))

    def test_the_refusal_text_does_not_say_which_check_failed(self):
        a = self.open(); self.clock[0] += 0.1; texts_fast = self.submit(a).error[0].value
        b = self.open(); self.clock[0] += 30; texts_hp = self.submit(b, honeypot="x").error[0].value
        self.assertEqual(texts_fast, texts_hp)
        for word in ("honeypot", "מהר", "שדה", "בוט", "זמן"):
            self.assertNotIn(word, texts_fast)

    def test_a_token_is_single_use_a_second_submit_in_the_same_second_fails(self):
        at = self.open()
        self.clock[0] += 5
        at = self.submit(at, pw="wrong")                     # passes the bot check, wrong password
        self.assertEqual(at.error[0].value, render_error("AUTH_FAILED"))
        at = self.submit(at, pw=PW)                          # new token issued just now: still inside the minimum
        self.assertEqual(at.error[0].value, t("bot_check_failed"))
        self.clock[0] += 3
        self.assertTrue(self.in_app(self.submit(at, pw=PW)))

    def test_a_token_expires_after_an_hour_and_the_form_recovers(self):
        at = self.open()
        self.clock[0] += 4000
        at = self.submit(at)
        self.assertEqual(at.error[0].value, t("bot_check_failed"))
        self.clock[0] += 3
        self.assertTrue(self.in_app(self.submit(at)))

    def test_a_token_belongs_to_one_browser_session(self):
        a, b = self.open(), self.open()
        self.clock[0] += 5
        stolen = a.session_state["_bot_login"]
        b.session_state["_bot_login"] = stolen               # B presents A's token
        at = self.submit(b)
        self.assertEqual(at.error[0].value, t("bot_check_failed"))

    def test_registration_has_the_same_protection(self):
        allowlist.add_entry(self.path, "noa@example.com")
        at = self.open()
        self.clock[0] += 0.2
        at = self.register("noa@example.com", gender="gender_female", password="12345678", at=at)
        self.assertEqual(at.error[0].value, t("bot_check_failed"))
        self.assertEqual(len(allowlist.list_entries(self.path, status="pending")), 1)
        self.clock[0] += 5
        at.text_input(key="hp_register_value").set_value("bot")
        btn(at, t("btn_register")).click()
        at = at.run()
        self.assertEqual(at.error[0].value, t("bot_check_failed"))
        self.clock[0] += 5
        at.text_input(key="hp_register_value").set_value("")
        btn(at, t("btn_register")).click()
        at = at.run()
        self.assertFalse(at.error)
        self.assertEqual(self.users(), 2)

    def test_repeated_bot_failures_slow_the_address_down(self):
        for _ in range(LOGIN_LIMIT + 1):
            at = self.open()
            self.clock[0] += 0.01
            self.submit(at)
        at = self.open()
        self.clock[0] += 10
        at = self.submit(at)
        self.assertIn(ERRORS["RATE_LIMITED"].title, at.error[0].value)

    def test_the_honeypot_is_hidden_by_css_and_labelled(self):
        from baror.ui import theme
        self.assertIn(".st-key-hp_login", theme.css_text())
        self.assertIn(".st-key-hp_register", theme.css_text())
        self.assertIn("-10000px", theme.css_text())


if __name__ == "__main__":
    unittest.main()
