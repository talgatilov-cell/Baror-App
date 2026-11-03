"""T8 wired into the app: bugs never reach the page, a damaged or unwritable data directory gives a readable Hebrew
message (and the file is left alone), and a complete sign-in / registration / logout flow writes audit events that carry
no raw e-mail, password or token."""
import json
import logging
import os
import stat
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

from baror.auth import sessions
from baror.logging_setup import JsonFormatter
from baror.messages_he import ERRORS, t
from baror.storage import allowlist, db, repo
from baror.ui import personal
from tests.test_app import APP, PW, AppCase, btn, texts


class BugsNeverReachThePage(AppCase):
    def test_an_unexpected_exception_shows_a_generic_message_with_an_incident_code_and_logs_the_traceback(self):
        self.cadet()
        at = self.sign_in()
        secret = "secret-xyz password=hunter2 token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        with mock.patch.object(personal, "personal_page", side_effect=RuntimeError(secret)), \
                self.assertLogs("baror", level="ERROR") as logs:
            at.run()
        self.assertFalse(at.exception, "no Streamlit exception element: the boundary caught it")
        shown = texts(at)
        self.assertIn(ERRORS["UNEXPECTED"].title, shown)
        for leak in ("secret-xyz", "hunter2", "Traceback", "RuntimeError", "AAAAAAAA"):
            self.assertNotIn(leak, shown)
        code = [w for w in shown.split("`") if len(w) >= 6 and w.isalnum()]
        self.assertTrue(code, "an incident code is shown")
        record = JsonFormatter().format(logs.records[-1])
        self.assertIn(code[0], record, "the same code is in the log line")
        self.assertIn("Traceback", record)
        self.assertNotIn("hunter2", record)
        self.assertNotIn("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", record)

    def test_stop_and_rerun_still_pass_through_the_boundary(self):
        at = self.signed_in()
        btn(at, t("btn_logout")).click()
        at.run()
        self.assertFalse(at.exception)
        self.assertIn(t("logged_out"), texts(at))             # st.stop() inside the page ended the run quietly
        self.assertNotIn(ERRORS["UNEXPECTED"].title, texts(at))

    def test_the_streamlit_error_details_switch_is_off(self):
        import tomllib
        cfg = tomllib.loads((Path(APP).parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["client"]["showErrorDetails"], "none")


class StartupProblems(AppCase):
    def test_a_corrupt_database_file_gives_a_readable_message_and_is_left_byte_identical(self):
        junk = b"this is not a database " * 200
        Path(self.path).unlink(missing_ok=True)
        Path(self.path).write_bytes(junk)
        at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertFalse(at.exception)
        self.assertIn(ERRORS["DATA_CORRUPTED"].title, texts(at))
        self.assertEqual(Path(self.path).read_bytes(), junk, "never overwritten")
        self.assertFalse(at.text_input, "no sign-in form is offered on top of a broken store")

    @unittest.skipIf(os.geteuid() == 0, "root can write anywhere")
    def test_an_unwritable_data_directory_gives_a_readable_message_not_a_traceback(self):
        d = Path(self._tmp.name) / "ro"
        d.mkdir()
        os.environ["DB_PATH"] = str(d / "x.db")
        d.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            at = AppTest.from_file(APP, default_timeout=60).run()
        finally:
            d.chmod(stat.S_IRWXU)
        self.assertFalse(at.exception)
        self.assertIn(ERRORS["SERVICE_UNAVAILABLE"].title, texts(at))
        self.assertNotIn("Traceback", texts(at))
        self.assertFalse((d / "x.db").exists())

    def test_a_missing_data_directory_is_reported_not_created(self):
        os.environ["DB_PATH"] = str(Path(self._tmp.name) / "nope" / "x.db")
        at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertFalse(at.exception)
        self.assertIn(ERRORS["SERVICE_UNAVAILABLE"].title, texts(at))
        self.assertFalse((Path(self._tmp.name) / "nope").exists())

    def test_an_invalid_setting_is_reported_not_a_crash(self):
        os.environ["SESSION_DAYS"] = "0"
        at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertFalse(at.exception)
        self.assertIn(ERRORS["SERVICE_UNAVAILABLE"].title, texts(at))


class AuditEventsCarryNoSecrets(AppCase):
    def test_a_whole_flow_leaves_events_without_raw_email_password_or_token(self):
        allowlist.add_entry(self.path, "noa@example.com")
        self.cadet("dana@example.com", "Dana")
        records = []

        class Grab(logging.Handler):
            def emit(self, record):
                records.append(JsonFormatter().format(record))
        grab = Grab(level=logging.DEBUG)
        audit = logging.getLogger("baror.audit")
        old_level = audit.level
        audit.addHandler(grab)
        audit.setLevel(logging.DEBUG)
        self.addCleanup(lambda: (audit.removeHandler(grab), audit.setLevel(old_level)))

        self.sign_in("dana@example.com", "wrong password")
        at = self.sign_in("dana@example.com", PW)
        token = self.set_cookie.call_args.args[0]
        self.register("unlisted@example.com", gender="gender_male", password="a secret pw 123")
        self.register("noa@example.com", gender="gender_female", password="another secret 99")
        btn(at, t("btn_logout")).click()
        at.run()

        lines = [json.loads(r) for r in records]
        events = [l.get("event") or l.get("message") for l in lines]
        for name in ("login_failed", "login_ok", "register_refused", "register_ok", "logout"):
            self.assertIn(name, events, f"{name} not logged: {events}")
        blob = "\n".join(records)
        for secret in ("dana@example.com", "unlisted@example.com", "noa@example.com", "wrong password", PW,
                       "a secret pw 123", "another secret 99", token, "example.com"):
            self.assertNotIn(secret, blob, f"{secret!r} appears in the audit log")
        refused = [l for l in lines if (l.get("event") or l.get("message")) == "register_refused"][0]
        self.assertEqual(refused.get("f_reason") or refused.get("reason"), "not_listed", "the operator can see why")
        failed = [l for l in lines if (l.get("event") or l.get("message")) == "login_failed"][0]
        self.assertTrue(failed.get("email"), "a keyed token of the e-mail is kept so repeated failures can be correlated")
        self.assertNotIn("@", failed["email"])

    def test_logging_is_configured_once_across_reruns(self):
        at = self.fresh()
        for _ in range(3):
            at.run()
        self.assertEqual(len([h for h in logging.getLogger("baror").handlers if type(h).__name__ == "_StdoutHandler"]), 1)


if __name__ == "__main__":
    unittest.main()
