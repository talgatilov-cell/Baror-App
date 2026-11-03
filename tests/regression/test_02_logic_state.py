"""Logic / state bugs. Tests assert the CORRECT behaviour; original app fails them."""
import datetime as _dt, json, os, time, unittest
from unittest import mock
from helpers import *


class SessionUserDeleted(unittest.TestCase):
    """BR-05: user removed from users.json while their session is open -> KeyError (app.py:192)."""
    def test_deleted_user_with_live_session(self):
        p = make_users_file({"a@b.c": user(), "z@z.z": user("זד")})
        at = new_app(); login(at, "a@b.c", "pw")
        self.assertEqual(exc(at), [])
        data = read(p); del data["a@b.c"]
        with open(p, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False)
        at.run()                                  # any interaction -> rerun
        self.assertEqual(exc(at), [], "should fall back to the login screen")


class Registration(unittest.TestCase):
    """BR-07: empty / blank identifiers are accepted."""
    def test_empty_email_rejected(self):
        p = make_users_file({})
        at = new_app(); register(at, "name", "", "זכר", "pw")
        self.assertNotIn("", read(p), "empty email must not create an account")
    def test_blank_password_rejected(self):
        p = make_users_file({})
        at = new_app(); register(at, "name", "a@b.c", "זכר", "   ")
        self.assertEqual(read(p), {})
    def test_blank_name_rejected(self):
        p = make_users_file({})
        at = new_app(); register(at, "   ", "a@b.c", "זכר", "pw")
        self.assertEqual(read(p), {})
    def test_email_without_at_rejected(self):
        p = make_users_file({})
        at = new_app(); register(at, "n", "not-an-email", "זכר", "pw")
        self.assertEqual(read(p), {})
    def test_double_click_after_success_not_an_error(self):   # BR-21
        make_users_file({})
        at = new_app(); register(at, "n", "a@b.c", "זכר", "pw")
        self.assertEqual(len(at.success), 1)
        btn(at, "הרשם").click(); at.run()
        self.assertEqual(len(at.error), 0, "second click right after success shows 'email exists'")


class StateHygiene(unittest.TestCase):
    """BR-20: logout leaves identity in session_state; login widgets keep the password."""
    def test_logout_clears_identity(self):
        make_users_file({"a@b.c": user()})
        at = new_app(); login(at, "a@b.c", "pw")
        btn(at, "התנתק מהמערכת").click(); at.run()
        self.assertFalse(at.session_state["logged_in"])
        leftovers = {k: at.session_state[k] for k in ("current_user", "email", "gender", "is_km")
                     if k in at.session_state and at.session_state[k] not in (None, False)}
        self.assertEqual(leftovers, {}, "identity should be wiped on logout")
    def test_login_password_not_kept_in_session_state(self):
        make_users_file({"a@b.c": user()})
        at = new_app(); login(at, "a@b.c", "pw")
        self.assertNotIn("login_pass", [k for k in ("login_pass",) if k in at.session_state])
    def test_login_password_not_kept_after_logout(self):
        make_users_file({"a@b.c": user()})
        at = new_app(); login(at, "a@b.c", "pw")
        btn(at, "התנתק מהמערכת").click(); at.run()
        self.assertEqual(at.text_input(key="login_pass").value, "")


class Grading(unittest.TestCase):
    """BR-15/16/17: grade thresholds characterised via the real app; odd gender values."""
    def _grade(self, gender, t, pushups=0, km=False):
        make_users_file({"a@b.c": user(gender=gender, is_km=km)})
        at = new_app(); login(at, "a@b.c", "pw")
        submit_result(at, t, pushups)
        w = [m.value for m in at.markdown]
        run = [x for x in w if "סטטוס אירובי" in x]; pu = [x for x in w if "סטטוס שכיבות" in x]
        return (run[0].split(":** ")[1] if run else None, pu[0].split(":** ")[1] if pu else None, exc(at))

    def test_unknown_gender_gets_female_scale(self):          # BR-15
        # 15 push-ups is a FAIL for a male (<22) but a PASS on the female scale (>=11)
        male_ok = self._grade("זכר", "20:00", 15)[1]
        typo = self._grade("זכר ", "20:00", 15)[1]            # trailing space from a hand edit
        self.assertEqual(male_ok, "נכשל ❌")
        self.assertNotEqual(typo, "עובר ✅", "unknown gender must not silently get the other (female) scale")

    def test_zero_time_gets_top_grade(self):                   # BR-17
        self.assertNotEqual(self._grade("זכר", "0:00", 40)[0], "מצטיין 🏆")
    def test_negative_time_gets_top_grade(self):
        self.assertNotEqual(self._grade("זכר", "-5:00", 40)[0], "מצטיין 🏆")
    def test_python_int_quirks_accepted(self):
        make_users_file({"a@b.c": user()})
        at = new_app(); login(at, "a@b.c", "pw")
        accepted = []
        for s in ("1_5:4_0", "١٥:٤٠", "+15:+40", " 15 : 40 ", "15:-40", "００:３０"):
            at = new_app(); login(at, "a@b.c", "pw"); submit_result(at, s, 5)
            accepted.append((s, len(at.success) == 1))
        self.assertEqual([a for a in accepted if a[1]], [], "non-canonical strings were accepted")

    def test_boundaries_documented(self):                      # BR-16 (characterisation, passes)
        got = {}
        for g, ts in (("זכר", (750, 751, 940, 941)), ("נקבה", (880, 881, 1180, 1181))):
            for t in ts:
                got[(g, t)] = self._grade(g, f"{t//60}:{t%60:02d}")[0]
        self.assertEqual(got[("זכר", 750)], "מצטיין 🏆");  self.assertEqual(got[("זכר", 751)], "עובר ✅")
        self.assertEqual(got[("זכר", 940)], "עובר ✅");   self.assertEqual(got[("זכר", 941)], "נכשל ❌")
        self.assertEqual(got[("נקבה", 880)], "מצטיינת 🏆"); self.assertEqual(got[("נקבה", 881)], "עובר ✅")
        self.assertEqual(got[("נקבה", 1180)], "עובר ✅");  self.assertEqual(got[("נקבה", 1181)], "נכשל ❌")
        self.assertEqual(self._grade("זכר", "66:00", km=True)[0], "עובר ✅ (כ.מ)")
        self.assertEqual(self._grade("זכר", "66:01", km=True)[0], "נכשל ❌ (כ.מ)")


class Dates(unittest.TestCase):
    """BR-09 default date uses the *server* timezone; BR-19b future dates."""
    def test_default_date_is_israel_date_when_server_is_utc(self):
        # Instant: 2026-11-01 23:30 UTC == 2026-11-02 01:30 in Israel (UTC+2, DST ended 2026-10-25).
        # (Patching datetime.datetime globally segfaults pandas' C ext, so we shim only the app's
        #  `from datetime import datetime` line in a temp copy.)
        import sys
        d = app_copy()
        (d / "baror_fake_clock.py").write_text(
            "import datetime as _d\n"
            "_I = _d.datetime(2026, 11, 1, 23, 30, tzinfo=_d.timezone.utc).timestamp()\n"
            "class datetime(_d.datetime):\n"
            "    @classmethod\n"
            "    def today(cls): return cls.fromtimestamp(_I)\n"
            "    @classmethod\n"
            "    def now(cls, tz=None): return cls.fromtimestamp(_I, tz)\n")
        src = (d / "app.py").read_text(encoding="utf-8")
        self.assertIn("from datetime import datetime", src)
        (d / "app.py").write_text(src.replace("from datetime import datetime", "from baror_fake_clock import datetime"), encoding="utf-8")
        sys.path.insert(0, str(d))
        old = os.environ.get("TZ"); os.environ["TZ"] = "UTC"; time.tzset()      # what python:3.12-slim does by default
        try:
            make_users_file({"a@b.c": user()})
            at = new_app(d); login(at, "a@b.c", "pw")
            self.assertEqual(exc(at), [])
            self.assertEqual(at.date_input[0].value, _dt.date(2026, 11, 2),
                             f"cadet in Israel at 01:30 on 2 Nov sees default {at.date_input[0].value}")
        finally:
            sys.path.remove(str(d)); sys.modules.pop("baror_fake_clock", None)
            if old is None: os.environ.pop("TZ", None)
            else: os.environ["TZ"] = old
            time.tzset()

    def test_future_date_rejected(self):
        p = make_users_file({"a@b.c": user()})
        at = new_app(); login(at, "a@b.c", "pw")
        submit_result(at, "15:00", 20, date=_dt.date.today() + _dt.timedelta(days=3650))
        stored = [r["date"] for r in read(p)["a@b.c"]["history"]]
        self.assertTrue(all(d <= _dt.date.today().isoformat() for d in stored), f"future-dated record stored: {stored}")


class RerunOrdering(unittest.TestCase):
    """BR-10 / BR-11: chart is drawn before the save handler, so it is stale in the run that saved."""
    H = [{"date": "2026-09-01", "run_seconds": 1100, "pushups": 8}]
    def test_chart_includes_new_record_immediately(self):
        make_users_file({"a@b.c": user(history=list(self.H))})
        at = new_app(); login(at, "a@b.c", "pw")
        self.assertEqual(chart_points(at), [1, 1])
        submit_result(at, "15:00", 20)
        self.assertEqual(len(at.success), 1)
        self.assertEqual(chart_points(at), [2, 2], "chart still shows 1 point right after a successful save")
    def test_first_ever_save_shows_chart(self):
        make_users_file({"a@b.c": user()})
        at = new_app(); login(at, "a@b.c", "pw")
        submit_result(at, "15:00", 20)
        self.assertEqual(chart_points(at), [1, 1], "first save: no chart at all until a manual reload")
    def test_reload_button_behaviour(self):                    # BR-11 characterisation
        make_users_file({"a@b.c": user()})
        at = new_app(); login(at, "a@b.c", "pw")
        submit_result(at, "15:00", 20)
        if "טען גרפים מחדש 🔄" not in [b.label for b in at.button]:
            self.skipTest("reload button removed (not needed once the chart is drawn after the save handler)")
        reload_btn = btn(at, "טען גרפים מחדש 🔄"); reload_btn.click(); at.run()
        self.assertEqual(exc(at), [])
        self.assertEqual(chart_points(at), [1, 1])             # works, but only because ANY click reruns
        self.assertEqual(len(at.success), 0)                   # ...and the success/status text vanishes


if __name__ == "__main__":
    unittest.main(verbosity=2)
