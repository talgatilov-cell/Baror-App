"""T3: the Streamlit app end to end under AppTest (no browser). Abuse cases: refusal wording, enumeration, isolation
between two sessions, revoked/deleted/demoted accounts mid-session, markdown injection, role gating of staff screens."""
import datetime as dt
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import streamlit as st
from streamlit.testing.v1 import AppTest

from baror import config
from baror.auth import passwords, service, sessions
from baror.errors import AuthError
from baror.ui import accom, personal, profile as profile_ui
from baror.messages_he import ERRORS, render_error, t
from baror.storage import accommodations, allowlist, audit, db, ops, repo
from baror.ui import guards
from baror.ui import session as ui_session
from baror.ui.text import esc

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
PW = "correct horse battery"


def texts(at) -> str:
    parts = []
    for kind in ("title", "header", "subheader", "markdown", "text", "error", "success", "warning", "info", "caption"):
        parts += [str(getattr(e, "value", "")) for e in getattr(at, kind)]
    parts += [str(e.value) for e in at.html if not str(e.value).lstrip().startswith(("<script", "<style"))]   # field errors, chips
    return "\n".join(parts)


def btn(at, label):
    return [b for b in at.button if b.label == label][0]


class AppCase(unittest.TestCase):
    cohort_views = False

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "app.db")
        saved = {k: os.environ.get(k) for k in ("DB_PATH", "BAROR_COHORT_VIEWS", "SESSION_DAYS", "BAROR_BOT_MIN_SECONDS",
                                                "TRUSTED_PROXIES", "FORWARDED_ALLOW_IPS")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        os.environ["DB_PATH"] = self.path
        os.environ.pop("SESSION_DAYS", None)
        os.environ.pop("TRUSTED_PROXIES", None)
        os.environ.pop("FORWARDED_ALLOW_IPS", None)
        os.environ["BAROR_BOT_MIN_SECONDS"] = "0"        # AppTest submits instantly; tests/test_abuse_limits.py covers timing
        guards.limiter().__init__(limits=guards.LIMITS)     # fresh counters per test (process-wide singleton)
        os.environ["BAROR_COHORT_VIEWS"] = "1" if self.cohort_views else "0"
        db.init_db(self.path)
        service.reset_unknown_email_backoff()
        patcher = mock.patch.object(ui_session, "set_cookie")
        self.set_cookie = patcher.start()
        self.addCleanup(patcher.stop)
        clear = mock.patch.object(ui_session, "clear_cookie")
        self.clear_cookie = clear.start()
        self.addCleanup(clear.stop)

    # fixtures
    def cadet(self, email="dana@example.com", name="Dana", gender="F", km=False, password=PW, role="cadet", must_change=False):
        return repo.create_user(self.path, email=email, name=name, gender=gender, is_km=km, role=role,
                                pw_hash=passwords.hash_password(password), must_change_pw=must_change)

    def fresh(self):
        return AppTest.from_file(APP, default_timeout=60).run()

    def sign_in(self, email="dana@example.com", password=PW):
        at = self.fresh()
        at.text_input(key="login_email").set_value(email)
        at.text_input(key="login_pass").set_value(password)
        btn(at, t("btn_login")).click()
        return at.run()

    def signed_in(self, **kw):
        self.cadet(**kw)
        return self.sign_in(kw.get("email", "dana@example.com"), kw.get("password", PW))

    def register(self, email, name="Noa", gender=None, password="long enough 1", confirm=None, km=False, at=None):
        at = at or self.fresh()
        at.text_input(key="reg_name").set_value(name)
        at.text_input(key="reg_email").set_value(email)
        if gender:
            at.selectbox(key="reg_gender").select(t(gender))
        at.checkbox(key="reg_km").set_value(km)
        at.text_input(key="reg_pass").set_value(password)
        at.text_input(key="reg_pass2").set_value(password if confirm is None else confirm)
        btn(at, t("btn_register")).click()
        return at.run()

    def in_app(self, at) -> bool:
        """True when the signed-in shell (the bottom bar) is on the page."""
        return any(w.key == "nav" for w in at.segmented_control)

    def profile(self, at):
        """Open More -> Profile."""
        at = self.go(at, "more")
        at.segmented_control(key="more_tab").set_value("profile")
        return at.run()

    def go(self, at, destination):
        """Tap a destination in the bottom bar (summary, add, history, more, platoon, chief)."""
        at.segmented_control(key="nav").set_value(destination)
        return at.run()

    def users(self):
        with db.reader(self.path) as c:
            return c.execute("SELECT count(*) FROM users").fetchone()[0]


class SignIn(AppCase):
    def test_sign_in_page_has_no_data_and_no_invite_code_field(self):
        at = self.fresh()
        self.assertFalse(at.exception)
        labels = [w.label for w in at.text_input]
        self.assertEqual(labels.count(t("honeypot_label")), 2, "one honeypot per form")
        labels = [l for l in labels if l != t("honeypot_label")]
        self.assertEqual(labels, [t("field_email"), t("field_password"), t("field_name"), t("field_email"),
                                  t("field_password"), t("field_password_confirm")])
        self.assertFalse([l for l in labels if "קוד" in l], "registration is by allowlist, not by invite code")
        self.assertIn(t("app_title"), texts(at))

    def test_successful_sign_in_shows_the_app_and_sets_the_cookie_once(self):
        self.cadet()
        at = self.sign_in()
        self.assertFalse(at.exception)
        self.assertIn("Dana", texts(at))
        self.assertEqual(self.set_cookie.call_count, 1)
        token, days = self.set_cookie.call_args.args
        self.assertRegex(token, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(days, 14)
        self.assertEqual(sessions.resolve(self.path, token)["email"], "dana@example.com")
        self.assertTrue(self.in_app(at))
        self.assertEqual(at.segmented_control(key="nav").value, "summary")

    def test_wrong_password_unknown_and_malformed_email_look_identical(self):
        self.cadet()
        shown = set()
        for email, pw in [("dana@example.com", "nope"), ("ghost@example.com", "nope"), ("not-an-email", "nope"), ("", "")]:
            at = self.sign_in(email, pw)
            self.assertEqual(len(at.error), 1)
            shown.add(at.error[0].value)
            self.assertNotIn("Dana", texts(at))
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown.pop(), render_error("AUTH_FAILED"))
        self.assertEqual(self.set_cookie.call_count, 0)

    def test_lock_out_message_is_the_same_for_a_real_and_an_unknown_account(self):
        self.cadet()
        outcomes = []
        for email in ("dana@example.com", "ghost@example.com"):
            at = None
            for _ in range(7):
                at = self.sign_in(email, "nope")
            outcomes.append(at.error[0].value)
        import re
        normalised = [re.sub(r"\d+|שנייה אחת|שתי שניות|שתי דקות|דקה אחת", "N", o) for o in outcomes]   # the wait may differ by a second
        self.assertEqual(normalised[0], normalised[1])
        self.assertIn(ERRORS["RATE_LIMITED"].title, outcomes[0])
        self.assertIn(ERRORS["RATE_LIMITED"].title, outcomes[1])

    def test_names_with_markdown_and_html_are_shown_literally(self):
        name = "**bold** [x](http://e.example) :red[hi] $$x$$ <img onerror=alert(1)> `c`"
        self.assertLessEqual(len(name), 80)
        at = self.signed_in(name=name)
        title = at.title[0].value
        self.assertNotIn("**bold**", title)
        self.assertIn(esc(name), title)
        shown = [e.value for e in self.profile(at).text]    # st.text: never interpreted
        self.assertIn(name, shown)

    def test_escape_is_reversible_markdown_escaping_of_every_ascii_punctuation(self):
        import string
        import re
        sample = string.punctuation + " abc אבג"
        e = esc(sample)
        for ch in string.punctuation:
            self.assertIn("\\" + ch, e)
        self.assertEqual(re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", e), sample)
        self.assertEqual(esc("a\nb\r"), "a b ")


class Registration(AppCase):
    def test_listed_email_registers_and_is_signed_in_with_the_listed_platoon(self):
        pid = ops.create_platoon(self.path, "A")
        allowlist.add_entry(self.path, "noa@example.com", platoon_id=pid)
        at = self.register("  Noa@Example.com ", gender="gender_female")
        self.assertFalse(at.exception)
        self.assertFalse(at.error)
        self.assertIn("Noa", texts(at))
        self.assertEqual(self.set_cookie.call_count, 1)
        u = repo.get_user(self.path, repo.get_login_record(self.path, "noa@example.com")["id"])
        self.assertEqual((u["role"], u["platoon_id"], u["gender"]), ("cadet", pid, "F"))
        self.assertEqual(allowlist.list_entries(self.path, status="used")[0]["email"], "noa@example.com")

    def test_every_refusal_shows_the_same_single_message_and_creates_nothing(self):
        allowlist.add_entry(self.path, "used@example.com")
        service.register(self.path, email="used@example.com", name="U", gender="M", is_km=False, password=PW)
        allowlist.add_entry(self.path, "listed@example.com")
        before = self.users()
        shown = set()
        for email in ("unlisted@example.com", "used@example.com", "not-an-email", "", "x" * 300 + "@example.com"):
            at = self.register(email, gender="gender_male")
            self.assertEqual(len(at.error), 1, email[:20])
            shown.add(at.error[0].value)
        self.assertEqual(shown, {render_error("REGISTRATION_DENIED")})
        self.assertEqual(self.users(), before)
        for word in ("רשימ", "מורשה", "היתר", "קיים"):
            self.assertNotIn(word, next(iter(shown)))
        self.assertEqual(self.set_cookie.call_count, 0)

    def test_password_rules_are_per_field_and_do_not_consume_the_allowlist_entry(self):
        allowlist.add_entry(self.path, "noa@example.com")
        short = self.register("noa@example.com", gender="gender_female", password="1234567")
        self.assertEqual([e.value for e in short.error], [render_error("INVALID_PASSWORD")])
        mismatch = self.register("noa@example.com", gender="gender_female", password="12345678", confirm="12345679")
        self.assertEqual([e.value for e in mismatch.error], [t("password_confirm_mismatch")])
        nogender = self.register("noa@example.com", password="12345678")
        self.assertEqual([e.value for e in nogender.error], [render_error("INVALID_GENDER")])
        self.assertEqual(self.users(), 0)
        self.assertEqual(len(allowlist.list_entries(self.path, status="pending")), 1)
        ok = self.register("noa@example.com", gender="gender_female", password="12345678")
        self.assertFalse(ok.error)
        self.assertEqual(self.users(), 1)

    def test_names_are_letters_only_and_a_refused_name_does_not_consume_the_allowlist_entry(self):
        allowlist.add_entry(self.path, "noa@example.com")
        for name in ("Noa1", "נועה <b>x</b>", "נועה [x](http://e.example)", "*נועה*", "נועה :smile:"):
            at = self.register("noa@example.com", name=name, gender="gender_female", password="12345678")
            self.assertEqual([e.value for e in at.error], [render_error("INVALID_NAME")], name)
        self.assertEqual(self.users(), 0)
        self.assertEqual(len(allowlist.list_entries(self.path, status="pending")), 1)
        ok = self.register("noa@example.com", name="נועה בר-אור", gender="gender_female", password="12345678")
        self.assertFalse(ok.error)
        self.assertEqual(self.users(), 1)

    def test_a_listed_instructor_entry_yields_an_instructor_and_never_a_chief_or_admin(self):
        allowlist.add_entry(self.path, "ins@example.com", role="instructor")
        self.register("ins@example.com", gender="gender_male", password="12345678")
        rec = repo.get_login_record(self.path, "ins@example.com")
        self.assertEqual(repo.get_user(self.path, rec["id"])["role"], "instructor")


class ForcedPasswordChange(AppCase):
    def test_flow(self):
        self.cadet(password="short", must_change=True)
        at = self.sign_in(password="short")
        self.assertEqual(self.set_cookie.call_count, 1)
        self.assertIn(t("pw_change_title"), texts(at))
        self.assertFalse(self.in_app(at), "the app is not reachable before the change")
        def change(at, cur, new, conf):
            at.text_input(key="pw_current").set_value(cur)
            at.text_input(key="pw_new").set_value(new)
            at.text_input(key="pw_new2").set_value(conf)
            btn(at, t("btn_change_password")).click()
            return at.run()
        at = change(at, "wrong", "brand new pw 1", "brand new pw 1")
        self.assertEqual(at.error[0].value, render_error("AUTH_FAILED"))
        at = change(at, "short", "1234567", "1234567")
        self.assertEqual(at.error[0].value, render_error("INVALID_PASSWORD"))
        at = change(at, "short", "short", "short")
        self.assertTrue(at.error)
        at = change(at, "short", "brand new pw 1", "different")
        self.assertEqual(at.error[0].value, t("password_confirm_mismatch"))
        self.assertTrue(repo.get_user(self.path, 1)["must_change_pw"])
        at = change(at, "short", "brand new pw 1", "brand new pw 1")
        self.assertFalse(at.exception)
        self.assertFalse(at.error)
        self.assertEqual(self.set_cookie.call_count, 2, "a new session cookie replaces the revoked one")
        self.assertFalse(repo.get_user(self.path, 1)["must_change_pw"])
        self.assertTrue(self.in_app(at), "the app is reachable now")
        self.assertEqual(service.login(self.path, "dana@example.com", "brand new pw 1")["id"], 1)


class SessionRules(AppCase):
    def test_two_browser_sessions_never_see_each_others_data(self):
        a, b = self.cadet("a@example.com", "Alice"), self.cadet("b@example.com", "Bob")
        repo.add_result(self.path, a, test_date="2026-10-01", pushups=11)
        repo.add_result(self.path, b, test_date="2026-10-01", pushups=99)
        sa, sb = self.sign_in("a@example.com"), self.sign_in("b@example.com")
        self.assertIn("Alice", texts(sa)); self.assertNotIn("Bob", texts(sa))
        self.assertIn("Bob", texts(sb)); self.assertNotIn("Alice", texts(sb))
        hist_a, hist_b = texts(self.go(sa, "history")), texts(self.go(sb, "history"))
        self.assertIn("11", hist_a); self.assertNotIn("99", hist_a)
        self.assertIn("99", hist_b); self.assertNotIn("11", hist_b)
        self.go(sa, "add")
        sa.selectbox(key="add_push").set_value(5)
        sa.button(key="save_btn").click(); sa.run()
        sb.run()
        self.assertEqual([r["pushups"] for r in repo.list_results(self.path, b)], [99])

    def test_a_new_session_without_a_cookie_is_signed_out(self):
        self.signed_in()
        self.assertIn(t("login_title"), [tb.label for tb in self.fresh().tabs])

    def test_cookie_resumes_a_session_after_reload_and_a_bad_cookie_does_not(self):
        uid = self.cadet()
        good = sessions.issue(self.path, uid)
        for cookie, expect_in in [(good, True), ("x" * 43, False), ("'; DROP TABLE users;--", False), (None, False),
                                  (good[:-1] + ("A" if good[-1] != "A" else "B"), False)]:
            jar = {} if cookie is None else {ui_session.COOKIE: cookie}
            with mock.patch.object(st, "context", types.SimpleNamespace(cookies=jar)):
                at = AppTest.from_file(APP, default_timeout=60).run()
            self.assertFalse(at.exception)
            self.assertEqual(self.in_app(at), expect_in, repr(cookie)[:30])
        sessions.revoke(self.path, good)
        with mock.patch.object(st, "context", types.SimpleNamespace(cookies={ui_session.COOKIE: good})):
            self.assertFalse(self.in_app(AppTest.from_file(APP, default_timeout=60).run()))

    def test_logout_revokes_the_token_clears_the_cookie_and_the_next_run_is_signed_out(self):
        at = self.signed_in()
        token = self.set_cookie.call_args.args[0]
        at = self.profile(at)
        btn(at, t("btn_logout")).click()
        at.run()
        self.assertEqual(self.clear_cookie.call_count, 1)
        self.assertIsNone(sessions.resolve(self.path, token))
        self.assertIn(t("logged_out"), texts(at))
        self.assertNotIn(ui_session.TOKEN_KEY, at.session_state)
        at.run()
        self.assertFalse(self.in_app(at))
        self.assertIn(t("login_title"), [tb.label for tb in at.tabs])

    def test_a_deleted_account_mid_session_gives_the_login_page_not_a_traceback(self):
        uid = self.cadet()
        at = self.sign_in()
        repo.delete_user(self.path, uid)
        at.run()
        self.assertFalse(at.exception)
        self.assertFalse(self.in_app(at))
        self.assertNotIn("Dana", texts(at))

    def test_deactivation_revocation_and_password_change_take_effect_on_the_next_run(self):
        uid = self.cadet()
        at = self.sign_in()
        ops.deactivate_user(self.path, uid)
        self.assertFalse(self.in_app(at.run()))
        ops.reactivate_user(self.path, uid)
        at = self.sign_in()
        self.assertTrue(self.in_app(at))
        sessions.revoke_all(self.path, uid)
        self.assertFalse(self.in_app(at.run()))

    def test_identity_is_not_taken_from_a_widget_or_the_url(self):
        a, b = self.cadet("a@example.com", "Alice"), self.cadet("b@example.com", "Bob")
        at = self.sign_in("a@example.com")
        at.query_params["user_id"] = str(b)
        at.query_params["email"] = "b@example.com"
        at.run()
        self.assertIn("Alice", texts(at))
        self.assertNotIn("Bob", texts(at))
        self.assertNotIn(ui_session.TOKEN_KEY, [w.key for w in at.segmented_control] + [w.key for w in at.text_input])

    def test_a_forged_navigation_value_falls_back_to_the_personal_area_without_an_error(self):
        self.cadet()
        at = self.sign_in()
        for forged in ("chief", "platoon", "no such page"):          # a destination the account is not offered
            at.session_state["nav"] = forged
            at.run()
            self.assertFalse(at.exception, forged)
            self.assertIn(t("greeting", name="Dana"), at.title[0].value, forged)
            self.assertEqual(at.segmented_control(key="nav").value, "summary")


class RealCookieHelper(unittest.TestCase):
    """The un-mocked helper: only a validated token and a bounded integer can ever reach the script."""

    def test_rejects_anything_that_is_not_a_token(self):
        for bad in ("short", "x" * 44, "x" * 42 + "'", "x" * 42 + ";", "x" * 42 + "<", "", None, 123, "x" * 43 + "\n"):
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                ui_session.set_cookie(bad, 14)
        for days in (0, 91, -1, True, "14", 14.0):
            with self.assertRaises(ValueError, msg=repr(days)):
                ui_session.set_cookie("A" * 43, days)


class PersonalArea(AppCase):
    def add(self, at, minutes=None, seconds=None, pushups=None, date=None, go=True):
        """Fill the add form and press save. minutes/seconds None = left empty."""
        if go:
            at = self.go(at, "add")
        for key, value in (("add_min", minutes), ("add_sec", seconds), ("add_push", pushups)):
            if value is not None:
                at.selectbox(key=key).set_value(value)
        if date is not None:
            at.date_input(key="add_date").set_value(date)
        at.button(key="save_btn").click()
        return at.run()

    def test_valid_result_is_saved_graded_per_test_and_shown_as_an_inline_card(self):
        at = self.add(self.signed_in(gender="M"), 15, 40, 30)
        self.assertFalse(at.exception)
        self.assertFalse(at.error)
        body = texts(at)
        self.assertIn(t("saved"), body)
        self.assertIn(at_iso(dt.date.today()), body)
        self.assertIn(t("status_pass"), body)
        self.assertIn("להצטיינות חסרות 3 דקות ו-10 שניות", body)
        self.assertIn("להצטיינות חסרות 6 חזרות", body)
        rows = repo.list_results(self.path, 1)
        self.assertEqual([(r["run_tenths"], r["pushups"]) for r in rows], [(9400, 30)])
        self.assertTrue(at.button(key="again") and at.button(key="to_summary"))

    def test_failed_test_shows_the_gap_impersonally_and_there_is_no_overall_verdict(self):
        at = self.add(self.signed_in(gender="M"), 15, 50, 10)
        body = texts(at)
        self.assertIn("טרם הושג הרף — חסרות 10 שניות", body)
        self.assertIn("טרם הושג הרף — חסרות 12 חזרות", body)
        for word in ("נכשל", "עובר", "סיכום כולל", "כשיר"):
            self.assertNotIn(word, body)

    def test_singular_gap_wording(self):
        body = texts(self.add(self.signed_in(gender="M"), 15, 41, 21))
        self.assertIn("חסרה שנייה אחת", body)
        self.assertIn("חסרה חזרה אחת", body)

    def test_change_against_the_previous_test_and_personal_best(self):
        self.cadet(gender="M")
        repo.add_result(self.path, 1, test_date="2026-09-01", run_tenths=9600, pushups=20)
        at = self.add(self.sign_in(), 15, 40, 25)
        body = texts(at)
        self.assertIn(t("vs_prev_better", gap="20 שניות"), body)
        self.assertIn(t("vs_prev_better", gap="5 חזרות"), body)
        self.assertIn(t("new_personal_best"), body)
        at = self.add(at.button(key="again").click().run(), 15, 41, 25, go=False)
        self.assertIn(t("vs_prev_worse", gap="שנייה אחת"), texts(at))

    def test_each_input_problem_is_shown_beside_its_field_and_keeps_what_was_chosen(self):
        at = self.signed_in(gender="M")
        cases = [((None, None, None), "err_none"), ((15, None, None), "err_seconds_missing"),
                 ((None, 40, None), "err_minutes_missing")]
        for (m, s_, p), key in cases:
            at = self.go(at, "add")
            for k in ("add_min", "add_sec", "add_push"):
                at.selectbox(key=k).set_value(None)
            at = self.add(at, m, s_, p, go=False)
            self.assertFalse(at.error)
            self.assertIn(t(key), texts(at), key)
            self.assertEqual(at.selectbox(key="add_min").value, m, "input is kept")
        self.assertEqual(repo.list_results(self.path, 1), [])

    def test_the_drop_downs_only_offer_values_inside_the_plausible_range(self):
        at = self.go(self.signed_in(gender="M"), "add")
        self.assertEqual((at.selectbox(key="add_min").options[0], at.selectbox(key="add_min").options[-1]), ("7", "60"))
        self.assertEqual(len(at.selectbox(key="add_sec").options), 60)
        self.assertEqual(at.selectbox(key="add_sec").options[:2], ["00", "01"])
        self.assertEqual((at.selectbox(key="add_push").options[0], at.selectbox(key="add_push").options[-1]), ("0", "200"))
        for key in ("add_min", "add_sec", "add_push"):
            self.assertIsNone(at.selectbox(key=key).value, "empty until chosen")

    def test_walk_track_offers_25_to_180_minutes(self):
        at = self.go(self.signed_in(gender="M", km=True), "add")
        self.assertEqual((at.selectbox(key="add_min").options[0], at.selectbox(key="add_min").options[-1]), ("25", "180"))

    def test_server_side_validation_still_refuses_impossible_times_that_bypass_the_drop_down(self):
        today = dt.date(2026, 11, 3)
        for minutes, seconds, key in ((0, 0, "err_time_zero"), (5, 0, "err_time_implausible"), (61, 0, "err_time_implausible")):
            entry, errs = personal.validate_form(day=today, minutes=minutes, seconds=seconds, pushups=None, today=today,
                                                 is_km=False)
            self.assertIsNone(entry)
            self.assertEqual(list(errs), ["time"], (minutes, seconds))
            self.assertIn(t(key, time="x").split("x")[0].split("{")[0][:6], errs["time"])

    def test_zero_pushups_waits_for_confirmation_and_is_then_a_real_value(self):
        at = self.add(self.signed_in(gender="M"), None, None, 0)
        self.assertEqual(at.warning[0].value, t("warn_zero_pushups"))
        self.assertEqual(repo.list_results(self.path, 1), [], "nothing is stored before the confirmation")
        at.button(key="confirm_save").click()
        at.run()
        self.assertEqual(repo.list_results(self.path, 1)[0]["pushups"], 0)
        self.assertTrue(at.button(key="to_summary"))

    def test_correcting_instead_of_confirming_stores_nothing(self):
        at = self.add(self.signed_in(gender="M"), None, None, 0)
        at.button(key="fix_entry").click()
        at.run()
        self.assertEqual(repo.list_results(self.path, 1), [])
        self.assertTrue(at.button(key="save_btn"))
        self.assertEqual(at.selectbox(key="add_push").value, 0, "input is kept")

    def test_an_identical_result_asks_before_it_is_stored_twice(self):
        at = self.add(self.signed_in(gender="M"), 15, 40, 30)
        at = self.add(at.button(key="again").click().run(), 15, 40, 30, go=False)
        self.assertEqual(at.warning[0].value, t("warn_duplicate"))
        self.assertEqual(len(repo.list_results(self.path, 1)), 1)
        at.button(key="confirm_save").click()
        at.run()
        self.assertEqual(len(repo.list_results(self.path, 1)), 2)

    def test_a_future_date_never_gets_stored(self):
        at = self.go(self.signed_in(gender="M"), "add")
        try:
            at.date_input(key="add_date").set_value(dt.date.today() + dt.timedelta(days=3))
        except Exception:      # the widget itself may already refuse a value above max_value
            return
        at = self.add(at, 15, 40, None, go=False)
        today = dt.date.today().isoformat()
        self.assertTrue(all(r["test_date"] <= today for r in repo.list_results(self.path, 1)), "nothing dated in the future")

    def test_walk_track_uses_its_own_label_threshold_and_singular_gap(self):
        at = self.go(self.signed_in(gender="M", km=True), "add")
        self.assertIn(t("section_walk"), texts(at))
        at = self.add(at, 66, 1, go=False)
        self.assertIn("טרם הושג הרף — חסרה שנייה אחת", texts(at))
        self.assertIn(t("test_walk"), texts(at))

    def test_target_chips_are_shown_before_entry(self):
        body = texts(self.go(self.signed_in(gender="M"), "add"))
        for chip in ("עד 15:40", "עד 12:30", "מ-22 חזרות", "מ-36 חזרות"):
            self.assertIn(chip, body)

    def test_the_form_is_cleared_after_saving(self):
        at = self.add(self.signed_in(gender="M"), 15, 40, 30)
        at = at.button(key="again").click().run()
        self.assertIsNone(at.selectbox(key="add_min").value)
        self.assertIsNone(at.selectbox(key="add_push").value)

    def test_empty_state_for_a_new_cadet_points_to_the_add_screen(self):
        at = self.signed_in()
        self.assertIn(t("empty_summary_title"), texts(at))
        at.button(key="empty_cta").click()
        at.run()
        self.assertEqual(at.segmented_control(key="nav").value, "add")

    def test_summary_shows_kpis_and_a_status_badge_per_test(self):
        self.cadet(gender="M")
        repo.add_result(self.path, 1, test_date="2026-09-01", run_tenths=9700, pushups=20)
        repo.add_result(self.path, 1, test_date="2026-10-01", run_tenths=9400, pushups=30)
        at = self.sign_in()
        self.assertEqual(len(at.metric), 3)
        run_kpi = at.metric[0]
        self.assertEqual((run_kpi.value, run_kpi.delta), ("15:40", "00:30"))
        self.assertEqual(at.metric[1].value, "15:40")
        self.assertEqual((at.metric[2].value, at.metric[2].delta), ("30", "10"))
        body = texts(at)
        self.assertEqual(body.count(t("status_pass")), 2)
        self.assertNotIn(t("empty_summary_title"), body)


def at_iso(d: dt.date) -> str:
    """The date as the result card writes it (dd/mm/yyyy inside Unicode isolates)."""
    return "\u2068" + d.strftime("%d/%m/%Y") + "\u2069"


class History(AppCase):
    def setUp(self):
        super().setUp()
        self.cadet(gender="M")
        for day, run, push in (("2026-10-01", 9400, 30), ("2026-10-08", 9300, None), ("2026-10-15", None, 25)):
            repo.add_result(self.path, 1, test_date=day, run_tenths=run, pushups=push)

    def test_newest_first_with_status_per_test_and_edit_delete_buttons(self):
        at = self.go(self.sign_in(), "history")
        self.assertFalse(at.exception)
        ids = [r["id"] for r in sorted(repo.list_results(self.path, 1), key=lambda r: r["test_date"], reverse=True)]
        self.assertEqual([w.key for w in at.button if w.key.startswith("edit_")], [f"edit_{i}" for i in ids])
        self.assertEqual([w.key for w in at.button if w.key.startswith("delete_")], [f"delete_{i}" for i in ids])
        self.assertIn(t("status_pass"), texts(at))

    def test_empty_history_has_a_designed_state(self):
        self.cadet("x@example.com", "Xi", gender="F")
        at = self.go(self.sign_in("x@example.com"), "history")
        self.assertEqual(at.info[0].value, t("empty_history"))

    def test_older_results_are_loaded_in_steps(self):
        for i in range(8):
            repo.add_result(self.path, 1, test_date=f"2026-09-{i + 1:02d}", pushups=20 + i)
        at = self.go(self.sign_in(), "history")
        self.assertEqual(len([w for w in at.button if w.key.startswith("edit_")]), 5)
        at.button(key="hist_more").click()
        at.run()
        self.assertEqual(len([w for w in at.button if w.key.startswith("edit_")]), 10)

    def test_delete_and_edit_buttons_open_a_dialog_and_change_nothing_by_themselves(self):
        at = self.go(self.sign_in(), "history")
        victim = repo.list_results(self.path, 1)[0]["id"]
        for key in (f"delete_{victim}", f"edit_{victim}"):
            at.button(key=key).click()
            at.run()
            self.assertFalse(at.exception, key)
            self.assertEqual(len(repo.list_results(self.path, 1)), 3, "opening a dialog changes nothing")
        # (AppTest cannot press a button INSIDE a dialog: the dialog is only rebuilt on the click that opens it;
        # the dialogs call apply_delete / apply_edit, which are tested below, and were exercised in a real browser.)

    def test_apply_delete_removes_only_that_result(self):
        victim = repo.list_results(self.path, 1)[0]["id"]
        personal.apply_delete(self.cfg(), self.user(), victim)
        self.assertEqual(len(repo.list_results(self.path, 1)), 2)
        self.assertNotIn(victim, [r["id"] for r in repo.list_results(self.path, 1)])
        personal.apply_delete(self.cfg(), self.user(), victim)       # twice: no error

    def test_apply_edit_validates_before_it_stores_and_keeps_the_field_names(self):
        rid = [r for r in repo.list_results(self.path, 1) if r["test_date"] == "2026-10-01"][0]["id"]
        day = dt.date(2026, 10, 1)
        errs = personal.apply_edit(self.cfg(), self.user(), rid, day=day, minutes=5, seconds=40, pushups=30)
        self.assertEqual(list(errs), ["time"])
        errs = personal.apply_edit(self.cfg(), self.user(), rid, day=day, minutes=15, seconds=None, pushups=30)
        self.assertEqual(errs, {"time": t("err_seconds_missing")})
        self.assertEqual([r["run_tenths"] for r in repo.list_results(self.path, 1) if r["id"] == rid], [9400])
        self.assertEqual(personal.apply_edit(self.cfg(), self.user(), rid, day=day, minutes=15, seconds=20, pushups=31), {})
        row = [r for r in repo.list_results(self.path, 1) if r["id"] == rid][0]
        self.assertEqual((row["run_tenths"], row["pushups"]), (9200, 31))

    def test_apply_edit_cannot_reach_another_users_result(self):
        other = self.cadet("o@example.com", "Other")
        repo.add_result(self.path, other, test_date="2026-10-01", pushups=50)
        theirs = repo.list_results(self.path, other)[0]["id"]
        errs = personal.apply_edit(self.cfg(), self.user(), theirs, day=dt.date(2026, 10, 1), minutes=None, seconds=None, pushups=1)
        self.assertEqual(list(errs), ["form"])
        self.assertEqual(repo.list_results(self.path, other)[0]["pushups"], 50)
        personal.apply_delete(self.cfg(), self.user(), theirs)
        self.assertEqual(len(repo.list_results(self.path, other)), 1)

    def cfg(self):
        return config.from_env({"DB_PATH": self.path})

    def user(self):
        return repo.get_user(self.path, 1)


class ExitEverywhere(AppCase):
    """Sign-out is in the top bar of EVERY page of EVERY account, not only in the profile."""

    def test_every_cadet_destination_has_the_exit_button_and_it_signs_out(self):
        self.cadet()
        for destination in ("summary", "add", "history", "more"):
            at = self.go(self.sign_in(), destination)
            self.assertIn("logout_top", [b.key for b in at.button], destination)
            self.assertEqual(at.button(key="logout_top").label, t("btn_logout"))
            before = self.clear_cookie.call_count
            at.button(key="logout_top").click()
            at.run()
            self.assertEqual(self.clear_cookie.call_count, before + 1, destination)
            self.assertIn(t("logged_out"), texts(at))
            self.assertFalse(self.in_app(at.run()), destination)

    def test_the_exit_button_is_not_a_destination_of_the_bottom_bar(self):
        at = self.signed_in()
        self.assertNotIn(t("btn_logout"), at.segmented_control(key="nav").options)

    def test_every_more_tab_has_it_too(self):
        at = self.signed_in()
        at = self.go(at, "more")
        for tab in ("tips", "videos", "accommodations", "profile"):
            at.segmented_control(key="more_tab").set_value(tab)
            at.run()
            self.assertIn("logout_top", [b.key for b in at.button], tab)


class CadetAccommodations(AppCase):
    def tab(self, at=None):
        at = at or self.signed_in(gender="M")
        at = self.go(at, "more")
        at.segmented_control(key="more_tab").set_value("accommodations")
        return at.run()

    def cfg(self):
        return config.from_env({"DB_PATH": self.path})

    def test_the_warning_comes_first_and_says_staff_will_check_and_a_record_cannot_be_removed_by_the_cadet(self):
        at = self.tab()
        self.assertFalse(at.exception)
        self.assertIn(t("acc_cadet_warning"), texts(at))
        children = [type(e).__name__ for e in at.main]
        self.assertLess(texts(at).index(t("acc_cadet_warning")), texts(at).index(t("acc_none")))
        self.assertEqual(len(at.segmented_control(key="acc_kind").options), 2)
        self.assertEqual(len(at.segmented_control(key="acc_scope").options), 3)
        self.assertEqual(at.text_input(key="acc_note").max_chars, 100)
        self.assertIn(t("acc_none"), texts(at))
        self.assertTrue(children)

    def test_a_cadet_records_one_for_themselves_and_it_is_labelled_as_theirs(self):
        at = self.tab()
        at.segmented_control(key="acc_kind").set_value("exemption")
        at.segmented_control(key="acc_scope").set_value("run")
        at.text_input(key="acc_note").set_value("Ankle sprain")
        at.button(key="acc_save").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.success[0].value, t("acc_added"))
        rows = accommodations.list_own(self.path, 1)
        self.assertEqual([(r["kind"], r["scope"], r["source"], r["note"]) for r in rows], [("exemption", "run", "cadet", "Ankle sprain")])
        self.assertIn(t("acc_source_cadet"), texts(self.tab(at)) + "\n".join(str(m.value) for m in at.markdown))

    def test_the_form_is_emptied_after_saving_and_the_list_is_read_only(self):
        at = self.tab()
        at.segmented_control(key="acc_kind").set_value("relief")
        at.segmented_control(key="acc_scope").set_value("both")
        at.button(key="acc_save").click()
        at.run()
        self.assertIsNone(at.segmented_control(key="acc_kind").value)
        self.assertEqual(at.text_input(key="acc_note").value, "")
        labels = [b.label for b in at.button]
        for forbidden in (t("btn_delete"), t("btn_edit"), t("btn_request_delete"), t("btn_delete_record")):
            self.assertNotIn(forbidden, labels)

    def test_missing_choices_are_refused_with_a_message_and_nothing_is_stored(self):
        at = self.tab()
        at.button(key="acc_save").click()
        at.run()
        self.assertTrue(at.error)
        self.assertEqual(accommodations.list_own(self.path, 1), [])

    def test_bad_dates_and_notes_are_refused_by_storage_and_shown(self):
        at = self.tab()
        at.segmented_control(key="acc_kind").set_value("relief")
        at.segmented_control(key="acc_scope").set_value("run")
        at.date_input(key="acc_end").set_value(dt.date.today() - dt.timedelta(days=5))
        at.button(key="acc_save").click()
        at.run()
        self.assertIn(render_error("ACCOMMODATION_DATES"), [e.value for e in at.error])
        self.assertEqual(accommodations.list_own(self.path, 1), [])

    def test_a_cadet_cannot_record_for_somebody_else_even_by_calling_the_helper(self):
        self.cadet("x@example.com", "Xi", gender="F")
        self.signed_in(gender="M")
        problem = accom.apply_add(self.cfg(), 1, 2, kind="relief", scope="run", start=dt.date.today(),
                                  end=dt.date.today() + dt.timedelta(days=5), note=None)
        self.assertIsNotNone(problem)
        self.assertEqual(accommodations.list_own(self.path, 2), [])


class StaffDoNotSeeThePersonalTab(AppCase):
    cohort_views = True

    def test_staff_accounts_have_no_accommodations_tab(self):
        pa = ops.create_platoon(self.path, "A")
        uid = self.cadet("i@example.com", "Ins", role="instructor")
        ops.grant_platoon(self.path, uid, pa)
        at = self.go(self.sign_in("i@example.com"), "more")
        self.assertNotIn("accommodations", at.segmented_control(key="more_tab").options)


class Profile(AppCase):
    def test_export_is_a_pdf_with_the_users_own_data_and_no_json_anywhere(self):
        self.cadet(gender="M")
        repo.add_result(self.path, 1, test_date="2026-10-01", pushups=30)
        at = self.profile(self.sign_in())
        button = at.get("download_button")[0]
        self.assertEqual(button.proto.label, t("btn_export"))
        self.assertIn("PDF", t("btn_export"))
        data = profile_ui.export_bytes(self.cfg(), repo.get_user(self.path, 1))
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertNotIn(b"pw_hash", data)
        for text in (t("btn_export"), t("export_body")):
            self.assertNotRegex(text, "(?i)json")

    def cfg(self):
        return config.from_env({"DB_PATH": self.path})

    def test_export_is_the_users_own_data_without_the_password_hash(self):
        self.cadet(gender="M")
        repo.add_result(self.path, 1, test_date="2026-10-01", pushups=30)
        at = self.profile(self.sign_in())
        self.assertFalse(at.exception)
        self.assertEqual(len(at.get("download_button")), 1)

    def test_cadet_notice_is_shown_only_when_instructor_views_are_on(self):
        self.assertNotIn(t("cadet_view_notice"), texts(self.profile(self.signed_in())))

    def test_delete_account_needs_the_password_and_removes_everything(self):
        self.cadet(gender="M")
        repo.add_result(self.path, 1, test_date="2026-10-01", pushups=30)
        at = self.profile(self.sign_in())
        at.button(key="delete_account").click()
        at.run()
        self.assertFalse(at.exception)                               # the dialog opens
        cfg = config.from_env({"DB_PATH": self.path})
        with self.assertRaises(AuthError):
            profile_ui.delete_account(cfg, repo.get_user(self.path, 1), "wrong password")
        self.assertIsNotNone(repo.get_user(self.path, 1), "a wrong password deletes nothing")
        self.assertEqual(len(repo.list_results(self.path, 1)), 1)
        profile_ui.delete_account(cfg, repo.get_user(self.path, 1), PW)
        self.assertIsNone(repo.get_user(self.path, 1))
        self.assertEqual(repo.list_results(self.path, 1), [])


class ProfileWithViews(AppCase):
    cohort_views = True

    def test_cadet_notice_is_shown_on_the_profile_and_before_registering(self):
        self.assertIn(t("cadet_view_notice"), texts(self.profile(self.signed_in())))
        self.assertIn(t("cadet_view_notice"), texts(self.fresh()))
