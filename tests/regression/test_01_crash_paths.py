"""BR-01 .. BR-06, BR-18, BR-28: crash paths. Each test asserts the CORRECT behaviour
(no uncaught exception, user sees a Hebrew message); on the original app they FAIL."""
import json, unittest
from helpers import *


class CorruptStore(unittest.TestCase):
    """BR-01: any unreadable users.json takes the whole app (even the login page) down."""
    def _run(self, **kw):
        make_users_file(**kw)
        at = new_app(); at.run()
        return exc(at)

    def test_empty_file(self):                       # e.g. crash after open('w'), fresh volume
        self.assertEqual(self._run(raw=b""), [])
    def test_truncated_json(self):
        self.assertEqual(self._run(raw=b'{"a@b.c": {"name": "x", "gen'), [])
    def test_non_utf8(self):
        self.assertEqual(self._run(raw="{\"a\": 1}".encode("utf-16")), [])
    def test_top_level_list(self):
        self.assertEqual(self._run(content=["a@b.c"]), [])
    def test_top_level_null(self):
        self.assertEqual(self._run(raw=b"null"), [])
    def test_user_entry_null(self):                  # {"a@b.c": null}
        self.assertEqual(self._run(content={"a@b.c": None}), [])
    def test_user_entry_string(self):
        self.assertEqual(self._run(content={"a@b.c": "oops"}), [])


class MissingKeys(unittest.TestCase):
    """BR-06: hand-edited / older-schema users crash login instead of showing an error."""
    def test_no_password_key_crashes_login_for_that_email(self):
        make_users_file({"a@b.c": {"name": "x", "gender": "זכר"}})
        at = new_app(); login(at, "a@b.c", "whatever")
        self.assertEqual(exc(at), [], "login with a user lacking 'password' must show an error, not a traceback")
    def test_no_name_key_after_correct_password(self):
        make_users_file({"a@b.c": {"gender": "זכר", "password": "pw"}})
        at = new_app(); login(at, "a@b.c", "pw")
        self.assertEqual(exc(at), [])
    def test_no_gender_key_after_correct_password(self):
        make_users_file({"a@b.c": {"name": "x", "password": "pw"}})
        at = new_app(); login(at, "a@b.c", "pw")
        self.assertEqual(exc(at), [])


class BadHistory(unittest.TestCase):
    """BR-04: one malformed record permanently bricks that user's dashboard (the entry form
    is below the chart code, so the user can never add a corrected record)."""
    def _dash(self, history):
        make_users_file({"a@b.c": user(history=history)})
        at = new_app(); login(at, "a@b.c", "pw")
        return at
    def test_record_missing_run_seconds(self):
        at = self._dash([{"date": "2026-01-01", "pushups": 3}])
        self.assertEqual(exc(at), [])
    def test_record_empty_dict(self):
        at = self._dash([{}])
        self.assertEqual(exc(at), [])
    def test_record_run_seconds_string(self):
        at = self._dash([{"date": "2026-01-01", "run_seconds": "15:40", "pushups": 3}])
        self.assertEqual(exc(at), [])
    def test_history_not_a_list(self):
        make_users_file({"a@b.c": {**user(), "history": None}})
        at = new_app(); login(at, "a@b.c", "pw")
        self.assertEqual(exc(at), [])
    def test_save_when_history_is_null(self):
        p = make_users_file({"a@b.c": {**user(), "history": None}})
        at = new_app(); login(at, "a@b.c", "pw")
        submit_result(at, "15:00", 20)
        self.assertEqual(exc(at), [], "saving a result for a user whose history is null must not crash")
    def test_history_is_a_dict(self):
        make_users_file({"a@b.c": {**user(), "history": {"x": 1}}})
        at = new_app(); login(at, "a@b.c", "pw")
        self.assertEqual(exc(at), [])
    def test_history_items_not_dicts(self):
        at = self._dash([1, 2, 3])
        self.assertEqual(exc(at), [])


class Overflow(unittest.TestCase):
    """BR-18: absurd numbers are accepted, saved, then the dashboard is rendered from them."""
    def test_huge_minutes_do_not_brick_dashboard(self):
        make_users_file({"a@b.c": user()})
        at = new_app(); login(at, "a@b.c", "pw")
        submit_result(at, "99999999999999999999999999:00", 5)
        at = new_app(); login(at, "a@b.c", "pw")           # fresh session, same user
        self.assertEqual(exc(at), [], "dashboard must still render after a silly value was saved")
    def test_4300_digit_minutes(self):
        make_users_file({"a@b.c": user()})
        at = new_app(); login(at, "a@b.c", "pw")
        submit_result(at, "9" * 5000 + ":00", 5)
        self.assertEqual(exc(at), [])
        self.assertTrue(len(at.error) >= 1)                 # a friendly message


class SaveFailures(unittest.TestCase):
    """BR-28: save_users() errors are not handled -> raw traceback, no message."""
    def test_register_when_data_dir_missing(self):
        d = tempfile.mkdtemp()
        os.environ["USERS_FILE"] = os.path.join(d, "nope", "users.json")   # dir does not exist
        at = new_app(); register(at, "x", "x@y.z", "זכר", "pw")
        self.assertEqual(exc(at), [])
    def test_register_when_readonly(self):
        p = make_users_file({})
        os.chmod(p, 0o444)
        if os.geteuid() == 0:
            self.skipTest("running as root: chmod does not block writes")
        at = new_app(); register(at, "x", "x@y.z", "זכר", "pw")
        self.assertEqual(exc(at), [])


import os, tempfile
if __name__ == "__main__":
    unittest.main(verbosity=2)
