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

from baror.auth import passwords, service, sessions
from baror.messages_he import ERRORS, render_error, t
from baror.storage import allowlist, audit, db, ops, repo
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
    for kind in ("title", "header", "subheader", "markdown", "text", "error", "success", "warning", "info", "caption"):
        parts += [str(getattr(e, "value", "")) for e in getattr(at.sidebar, kind)]
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
        self.assertEqual(at.sidebar.radio(key="nav_page").options[0], t("nav_personal"))

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
        side = at.sidebar.text[0].value                     # st.text: never interpreted
        self.assertIn(name, side)

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
        self.assertEqual(at.sidebar.radio, [], "the app is not reachable before the change")
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
        self.assertTrue(at.sidebar.radio, "the app is reachable now")
        self.assertEqual(service.login(self.path, "dana@example.com", "brand new pw 1")["id"], 1)


class SessionRules(AppCase):
    def test_two_browser_sessions_never_see_each_others_data(self):
        a, b = self.cadet("a@example.com", "Alice"), self.cadet("b@example.com", "Bob")
        repo.add_result(self.path, a, test_date="2026-10-01", pushups=11)
        repo.add_result(self.path, b, test_date="2026-10-01", pushups=99)
        sa, sb = self.sign_in("a@example.com"), self.sign_in("b@example.com")
        self.assertIn("Alice", texts(sa)); self.assertNotIn("Bob", texts(sa))
        self.assertIn("Bob", texts(sb)); self.assertNotIn("Alice", texts(sb))
        hist_a = " ".join(str(v) for df in sa.dataframe for v in df.value.values.flatten())
        hist_b = " ".join(str(v) for df in sb.dataframe for v in df.value.values.flatten())
        self.assertIn("11", hist_a); self.assertNotIn("99", hist_a)
        self.assertIn("99", hist_b); self.assertNotIn("11", hist_b)
        sa.number_input(key="add_pushups").set_value(5)
        btn(sa, t("btn_save_result")).click(); sa.run()
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
            self.assertEqual(bool(at.sidebar.radio), expect_in, repr(cookie)[:30])
        sessions.revoke(self.path, good)
        with mock.patch.object(st, "context", types.SimpleNamespace(cookies={ui_session.COOKIE: good})):
            self.assertFalse(AppTest.from_file(APP, default_timeout=60).run().sidebar.radio)

    def test_logout_revokes_the_token_clears_the_cookie_and_the_next_run_is_signed_out(self):
        at = self.signed_in()
        token = self.set_cookie.call_args.args[0]
        btn(at, t("btn_logout")).click()
        at.run()
        self.assertEqual(self.clear_cookie.call_count, 1)
        self.assertIsNone(sessions.resolve(self.path, token))
        self.assertIn(t("logged_out"), texts(at))
        self.assertNotIn(ui_session.TOKEN_KEY, at.session_state)
        at.run()
        self.assertFalse(at.sidebar.radio)
        self.assertIn(t("login_title"), [tb.label for tb in at.tabs])

    def test_a_deleted_account_mid_session_gives_the_login_page_not_a_traceback(self):
        uid = self.cadet()
        at = self.sign_in()
        repo.delete_user(self.path, uid)
        at.run()
        self.assertFalse(at.exception)
        self.assertFalse(at.sidebar.radio)
        self.assertNotIn("Dana", texts(at))

    def test_deactivation_revocation_and_password_change_take_effect_on_the_next_run(self):
        uid = self.cadet()
        at = self.sign_in()
        ops.deactivate_user(self.path, uid)
        self.assertFalse(at.run().sidebar.radio)
        ops.reactivate_user(self.path, uid)
        at = self.sign_in()
        self.assertTrue(at.sidebar.radio)
        sessions.revoke_all(self.path, uid)
        self.assertFalse(at.run().sidebar.radio)

    def test_identity_is_not_taken_from_a_widget_or_the_url(self):
        a, b = self.cadet("a@example.com", "Alice"), self.cadet("b@example.com", "Bob")
        at = self.sign_in("a@example.com")
        at.query_params["user_id"] = str(b)
        at.query_params["email"] = "b@example.com"
        at.run()
        self.assertIn("Alice", texts(at))
        self.assertNotIn("Bob", texts(at))
        self.assertNotIn(ui_session.TOKEN_KEY, [w.key for w in at.sidebar.radio] + [w.key for w in at.text_input])

    def test_a_forged_navigation_value_falls_back_to_the_personal_area_without_an_error(self):
        self.cadet()
        at = self.sign_in()
        with mock.patch.object(st, "radio", return_value="no such page / " + t("nav_chief")):
            at.run()
        self.assertFalse(at.exception)
        self.assertIn(t("greeting", name="Dana"), at.title[0].value)


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
    def add(self, at, run="", pushups=None, date=None):
        at.text_input(key="add_run").set_value(run)
        if pushups is not None:
            at.number_input(key="add_pushups").set_value(pushups)
        if date is not None:
            at.date_input(key="add_date").set_value(date)
        btn(at, t("btn_save_result")).click()
        return at.run()

    def test_valid_result_is_saved_graded_per_test_and_shown_in_the_same_run(self):
        at = self.signed_in(gender="M")
        at = self.add(at, "15:40", 30)
        self.assertFalse(at.exception)
        self.assertFalse(at.error)
        self.assertEqual(at.success[0].value, t("saved"))
        body = texts(at)
        self.assertIn("ריצה: הושג הרף. להצטיינות חסרות 3 דקות ו-10 שניות", body)
        self.assertIn("שכיבות סמיכה: הושג הרף. להצטיינות חסרות 6 חזרות", body)
        rows = repo.list_results(self.path, 1)
        self.assertEqual([(r["run_tenths"], r["pushups"]) for r in rows], [(9400, 30)])
        history = at.dataframe[-1].value
        self.assertIn("15:40", history.values.flatten().tolist(), "the new row is already in the history of the same run")

    def test_failed_test_shows_the_gap_impersonally_and_there_is_no_overall_verdict(self):
        at = self.add(self.signed_in(gender="M"), "15:50", 10)
        body = texts(at)
        self.assertIn("ריצה: טרם הושג הרף — חסרות 10 שניות", body)
        self.assertIn("שכיבות סמיכה: טרם הושג הרף — חסרות 12 חזרות", body)
        for word in ("נכשל", "עובר", "סיכום כולל", "כשיר"):
            self.assertNotIn(word, body)

    def test_singular_gap_wording(self):
        body = texts(self.add(self.signed_in(gender="M"), "15:41", 21))
        self.assertIn("חסרה שנייה אחת", body)
        self.assertIn("חסרה חזרה אחת", body)

    def test_each_input_error_is_specific_and_keeps_what_was_typed(self):
        at = self.signed_in(gender="M")
        for run, push, key in [("0:00", None, "TIME_ZERO"), ("99:99", None, "TIME_SECONDS_RANGE"), ("-5:00", None, "INVALID_TIME_FORMAT"),
                               ("15:5", None, "INVALID_TIME_FORMAT"), ("5:00", None, "TIME_OUT_OF_RANGE"), ("", None, "EMPTY_RESULT")]:
            at = self.add(at, run, push)
            self.assertEqual(len(at.error), 1, run)
            self.assertIn(ERRORS[key].title, at.error[0].value, run)
            self.assertEqual(at.text_input(key="add_run").value, run, "input is kept")
        self.assertEqual(repo.list_results(self.path, 1), [])

    def test_zero_pushups_is_a_real_value_with_a_warning(self):
        at = self.add(self.signed_in(gender="M"), "", 0)
        self.assertEqual(at.warning[0].value, t("warn_zero_pushups"))
        self.assertEqual(repo.list_results(self.path, 1)[0]["pushups"], 0)

    def test_a_future_date_never_gets_stored(self):
        at = self.signed_in(gender="M")
        at.text_input(key="add_run").set_value("15:40")
        try:
            at.date_input(key="add_date").set_value(dt.date.today() + dt.timedelta(days=3))
        except Exception:      # the widget itself may already refuse a value above max_value
            return
        btn(at, t("btn_save_result")).click()
        at.run()
        today = dt.date.today().isoformat()
        self.assertTrue(all(r["test_date"] <= today for r in repo.list_results(self.path, 1)), "nothing dated in the future")

    def test_walk_track_uses_its_own_label_and_threshold(self):
        at = self.signed_in(gender="M", km=True)
        self.assertEqual(at.text_input(key="add_run").label, t("field_run_time_walk"))
        self.assertIn(t("scale_walk", gender=t("scale_male")), texts(at))
        body = texts(self.add(at, "66:01"))
        self.assertIn("הליכה: טרם הושג הרף — חסרה שנייה אחת", body)

    def test_empty_state_for_a_new_cadet(self):
        self.assertIn(t("empty_summary_title"), texts(self.signed_in()))

    def test_charts_appear_after_the_first_result(self):
        at = self.add(self.signed_in(gender="M"), "15:40", 30)
        self.assertNotIn(t("empty_summary_title"), texts(at))
        self.assertIn(t("chart_run"), texts(at))

    def test_tips_and_videos_pages_render(self):
        at = self.signed_in()
        for label in (t("nav_tips_run"), t("nav_tips_pushups"), t("nav_videos")):
            at.sidebar.radio(key="nav_page").set_value(label).run()
            self.assertFalse(at.exception, label)
            self.assertTrue(at.title, label)


class StaffOff(AppCase):
    cohort_views = False

    def test_flag_off_means_no_staff_pages_for_anyone(self):
        pa = ops.create_platoon(self.path, "A")
        for role, email in (("instructor", "i@example.com"), ("chief", "c@example.com"), ("admin", "a@example.com")):
            uid = self.cadet(email, role, role=role)
            if role == "instructor":
                ops.grant_platoon(self.path, uid, pa)
            options = self.sign_in(email).sidebar.radio(key="nav_page").options
            self.assertEqual(len(options), 4, role)


class StaffOn(AppCase):
    cohort_views = True

    def setUp(self):
        super().setUp()
        self.pa, self.pb = ops.create_platoon(self.path, "Platoon A"), ops.create_platoon(self.path, "Platoon B")
        self.ins = self.cadet("ins@example.com", "Ins", role="instructor")
        ops.grant_platoon(self.path, self.ins, self.pa)
        self.boss = self.cadet("boss@example.com", "Boss", role="chief")
        self.ca = self.cadet("ca@example.com", "Alef-A", gender="M")
        self.cb = self.cadet("cb@example.com", "Bet-B", gender="M")
        ops.set_user_platoon(self.path, self.ca, self.pa)
        ops.set_user_platoon(self.path, self.cb, self.pb)
        repo.add_result(self.path, self.ca, test_date="2026-10-01", run_tenths=9000, pushups=30)
        repo.add_result(self.path, self.cb, test_date="2026-10-01", run_tenths=9000, pushups=30)

    def go(self, at, label):
        at.sidebar.radio(key="nav_page").set_value(label).run()
        return at

    def cells(self, at):
        return " ".join(str(v) for df in at.dataframe for v in df.value.values.flatten())

    def test_cadets_and_other_roles_are_not_offered_staff_pages(self):
        self.assertEqual(len(self.sign_in("ca@example.com").sidebar.radio(key="nav_page").options), 4)
        self.assertIn(t("nav_platoon"), self.sign_in("ins@example.com").sidebar.radio(key="nav_page").options)
        self.assertNotIn(t("nav_chief"), self.sign_in("ins@example.com").sidebar.radio(key="nav_page").options)
        self.assertIn(t("nav_chief"), self.sign_in("boss@example.com").sidebar.radio(key="nav_page").options)
        self.assertNotIn(t("nav_platoon"), self.sign_in("boss@example.com").sidebar.radio(key="nav_page").options)

    def test_instructor_sees_own_platoon_only_and_every_read_is_audited(self):
        at = self.go(self.sign_in("ins@example.com"), t("nav_platoon"))
        self.assertFalse(at.exception)
        self.assertEqual(at.selectbox(key="staff_platoon").options, ["Platoon A"])
        cells = self.cells(at)
        self.assertIn("Alef-A", cells)
        self.assertNotIn("Bet-B", cells)
        with db.reader(self.path) as c:
            rows = c.execute("SELECT actor_id,action,target_user_id FROM audit_log WHERE action LIKE 'cohort.%'").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(self.ins, "cohort.summary", self.ca)])
        at.selectbox(key="staff_cadet").select(self.ca).run()
        self.assertIn(("cohort.cadet", self.ca), {(r[0], r[1]) for r in
                      db.connect(self.path).execute("SELECT action,target_user_id FROM audit_log")})

    def test_instructor_without_a_grant_sees_a_notice_not_data(self):
        self.cadet("nogrant@example.com", "NoGrant", role="instructor")
        at = self.go(self.sign_in("nogrant@example.com"), t("nav_platoon"))
        self.assertIn(t("platoon_none"), texts(at))
        self.assertNotIn("Alef-A", self.cells(at))

    def test_demotion_removes_the_staff_page_on_the_next_run(self):
        at = self.go(self.sign_in("ins@example.com"), t("nav_platoon"))
        ops.set_role(self.path, self.ins, "cadet")
        at.run()
        self.assertNotIn(t("nav_platoon"), at.sidebar.radio(key="nav_page").options)
        self.assertNotIn("Alef-A", self.cells(at))

    def test_chief_sees_aggregates_with_small_groups_hidden_and_no_names(self):
        at = self.go(self.sign_in("boss@example.com"), t("nav_chief"))
        self.assertFalse(at.exception)
        cells = self.cells(at)
        self.assertNotIn("Alef-A", cells)
        self.assertNotIn("Bet-B", cells)
        self.assertIn("—", cells)
        self.assertIn(t("group_too_small"), texts(at))

    def test_chief_drill_down_names_a_cadet_and_is_audited_and_markdown_safe(self):
        evil = self.cadet("evil@example.com", "**bold** [x](http://e.example)", gender="M")
        ops.set_user_platoon(self.path, evil, self.pa)
        at = self.go(self.sign_in("boss@example.com"), t("nav_chief"))
        at.selectbox(key="chief_platoon").select(self.pa).run()
        self.assertEqual(sorted(at.selectbox(key="chief_cadet").options), sorted(["Alef-A", "**bold** [x](http://e.example)"]))
        self.assertNotIn("Bet-B", at.selectbox(key="chief_cadet").options, "only the chosen platoon is listed")
        at.selectbox(key="chief_cadet").select(evil).run()
        self.assertFalse(at.exception)
        self.assertNotIn("**bold**", at.subheader[0].value)
        with db.reader(self.path) as c:
            got = [tuple(r) for r in c.execute("SELECT actor_id,action,target_user_id,outcome FROM audit_log WHERE action='chief.cadet'")]
        self.assertEqual(got, [(self.boss, "chief.cadet", evil, "ok")])

    def test_an_instructor_cannot_reach_the_chief_page_even_by_forcing_the_state(self):
        at = self.sign_in("ins@example.com")
        self.assertNotIn(t("nav_chief"), at.sidebar.radio(key="nav_page").options)
        with self.assertRaises(Exception):
            at.sidebar.radio(key="nav_page").set_value(t("nav_chief")).run()


if __name__ == "__main__":
    unittest.main()
