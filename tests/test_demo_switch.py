"""TEMPORARY demo switch (baror/ui/demo_switch.py): off unless the operator opts in; when on, a demo session works on a
separate database and the real database is never reached. Delete this file together with the feature."""
import os
import unittest
from pathlib import Path

from baror.messages_he import t
from baror.storage import db
from baror.ui import demo_switch
from tests.test_app import APP, AppCase, btn, texts      # noqa: F401
from streamlit.testing.v1 import AppTest

DEMO_PW = "baror-demo-2026"


class DemoSwitch(AppCase):
    cohort_views = False

    def setUp(self):
        super().setUp()
        saved = {k: os.environ.get(k) for k in (demo_switch.ENV_ENABLE, demo_switch.ENV_DB)}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        os.environ.pop(demo_switch.ENV_ENABLE, None)
        os.environ.pop(demo_switch.ENV_DB, None)
        self.demo_path = Path(self.path).parent / "demo.db"

    def enable(self):
        os.environ[demo_switch.ENV_ENABLE] = "1"

    def demo_on(self):
        at = self.fresh()
        at.session_state[demo_switch.MODE_KEY] = True
        return at.run()

    def demo_sign_in(self, at, name="chief"):
        at.text_input(key="login_email").set_value(f"{name}@example.com")
        at.text_input(key="login_pass").set_value(DEMO_PW)
        btn(at, t("btn_login")).click()
        return at.run()

    def test_off_by_default_no_toggle_and_no_demo_database(self):
        at = self.fresh()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.toggle), 0)
        self.assertFalse(self.demo_path.exists())

    def test_a_flag_alone_does_nothing_when_the_operator_has_not_enabled_it(self):
        at = self.demo_on()                         # session flag set, BAROR_DEMO_ENABLED unset
        self.assertEqual(len(at.toggle), 0)
        self.assertFalse(self.demo_path.exists())

    def test_enabled_shows_the_toggle_off_and_the_real_app_still_works(self):
        self.enable()
        self.cadet()
        at = self.fresh()
        self.assertEqual([w.label for w in at.toggle], [t("demo_switch_label")])
        self.assertFalse(at.toggle[0].value)
        self.assertFalse(self.demo_path.exists(), "nothing is created until the switch is turned on")
        at = self.sign_in()
        self.assertTrue(self.in_app(at))
        self.assertNotIn(t("demo_banner"), texts(at))

    def test_turning_it_on_creates_the_demo_database_and_demo_accounts_work(self):
        self.enable()
        at = self.fresh()
        at.toggle[0].set_value(True)
        at = at.run()
        self.assertFalse(at.exception)
        self.assertTrue(self.demo_path.exists())
        self.assertIn(DEMO_PW, " ".join(str(c.value) for c in at.code))
        at = self.demo_sign_in(at, "chief")
        self.assertFalse(at.exception)
        self.assertTrue(self.in_app(at))
        self.assertIn(t("demo_banner"), texts(at))

    def test_demo_mode_never_reaches_the_real_database(self):
        self.enable()
        self.cadet()                                  # a real account, real password
        real_before = self.users()
        at = self.fresh()
        at.toggle[0].set_value(True)
        at = at.run()
        at.text_input(key="login_email").set_value("dana@example.com")
        at.text_input(key="login_pass").set_value(PW_REAL)
        btn(at, t("btn_login")).click()
        at = at.run()
        self.assertFalse(self.in_app(at), "a real account must not sign in to the demo")
        at = self.demo_sign_in(self.demo_on(), "twelve")
        self.assertTrue(self.in_app(at))
        self.assertEqual(self.users(), real_before, "the real database was not touched")
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM users WHERE email LIKE '%@example.com' AND email != ?",
                                       ("dana@example.com",)).fetchone()[0], 0, "no demo person in the real database")

    def test_demo_account_cannot_sign_in_with_the_switch_off(self):
        self.enable()
        self.demo_sign_in(self.demo_on(), "chief")        # creates the demo database
        at = self.fresh()                                 # a new browser session: switch off
        at = self.demo_sign_in(at, "chief")
        self.assertFalse(self.in_app(at), "demo passwords mean nothing to the real database")

    def test_switch_refuses_to_exist_when_the_demo_database_is_the_real_one(self):
        self.enable()
        os.environ[demo_switch.ENV_DB] = self.path
        at = self.demo_on()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.toggle), 0)
        self.assertNotIn(t("demo_banner"), texts(at))

    def test_demo_database_default_sits_next_to_the_real_one(self):
        from baror import config
        cfg = config.from_env({"DB_PATH": "/data/baror.db"})
        self.assertEqual(demo_switch.demo_db_path(cfg), Path("/data/demo.db"))


PW_REAL = "correct horse battery"

if __name__ == "__main__":
    unittest.main()
