"""T8.2: JSON logging, redaction and correlation. Abuse cases: log forging, secrets and addresses in every position
(message, extra fields, nested values, tracebacks), oversized input, concurrency, duplicate handlers."""
import hashlib
import importlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from baror import logging_setup as L
from baror.auth import passwords, service, sessions
from baror.errors import AuthError, BarorError
from baror.storage import db, repo

ROOT = Path(__file__).resolve().parent.parent
EMAIL = "Dana.Cohen+test@Example.com"


def reset_logger():
    log = logging.getLogger(L.LOGGER_NAME)
    for h in list(log.handlers):
        log.removeHandler(h)


class LogCase(unittest.TestCase):
    def setUp(self):
        reset_logger()
        self.addCleanup(reset_logger)
        self.buf = io.StringIO()
        self.log = L.configure_logging("DEBUG", self.buf)

    def lines(self):
        return [ln for ln in self.buf.getvalue().split("\n") if ln]

    def docs(self):
        return [json.loads(ln) for ln in self.lines()]


class Configuration(LogCase):
    def test_exactly_one_handler_however_often_it_is_configured(self):
        for _ in range(50):
            L.configure_logging("DEBUG", self.buf)
        self.assertEqual(len(self.log.handlers), 1)

    def test_reconfiguring_differently_replaces_never_stacks(self):
        L.configure_logging("WARNING", self.buf)
        L.configure_logging("ERROR", self.buf)
        self.assertEqual(len(self.log.handlers), 1)
        self.assertEqual(self.log.level, logging.ERROR)
        L.configure_logging("DEBUG", io.StringIO())
        self.assertEqual(len(self.log.handlers), 1)

    def test_a_module_reload_does_not_duplicate_the_handler(self):
        importlib.reload(L)
        L.configure_logging("DEBUG", self.buf)
        self.assertEqual(len(logging.getLogger("baror").handlers), 1)
        logging.getLogger("baror.x").info("after reload")
        self.assertEqual(len(self.lines()), 1)

    def test_concurrent_configure_calls_leave_one_handler(self):
        ts = [threading.Thread(target=lambda: L.configure_logging("INFO", None)) for _ in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(self.log.handlers), 1)

    def test_a_record_is_written_once_not_twice(self):
        root_buf = io.StringIO()
        root_handler = logging.StreamHandler(root_buf)
        logging.getLogger().addHandler(root_handler)
        self.addCleanup(lambda: logging.getLogger().removeHandler(root_handler))
        L.get_logger("t").info("once")
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(root_buf.getvalue(), "", "propagate must be off so the root logger does not print it again")

    def test_default_stream_is_the_current_stdout(self):
        reset_logger()
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            L.configure_logging("INFO")
            L.get_logger("t").info("to stdout")
        self.assertEqual(json.loads(out.getvalue())["event"], "to stdout")

    def test_a_typo_in_log_level_or_format_does_not_stop_the_app(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "LOUD", "LOG_FORMAT": "xml"}):
            log = L.configure_logging(stream=io.StringIO())
        self.assertEqual(log.level, logging.INFO)

    def test_streamlits_own_log_level_option_does_not_touch_the_baror_logger(self):
        code = ("import io, logging; from baror import logging_setup as L; L.configure_logging('INFO', io.StringIO()); "
                "import streamlit.logger as sl; sl.set_log_level('error'); "
                "lg = logging.getLogger('baror'); print(lg.level, len(lg.handlers), lg.propagate)")
        out = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); " + code],
                             capture_output=True, text=True, cwd=ROOT).stdout.strip().splitlines()[-1]
        self.assertEqual(out, f"{logging.INFO} 1 False")


class Format(LogCase):
    def test_one_json_object_per_line_with_the_expected_fields(self):
        L.set_correlation_id("abc123")
        L.get_logger("t").warning("hello", extra={"where": "x", "n": 3})
        (doc,) = self.docs()
        self.assertEqual((doc["level"], doc["logger"], doc["event"], doc["cid"], doc["where"], doc["n"]),
                         ("WARNING", "baror.t", "hello", "abc123", "x", 3))
        self.assertRegex(doc["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_hebrew_is_written_as_is(self):
        L.get_logger("t").info("שלום עולם")
        self.assertIn("שלום עולם", self.buf.getvalue())

    def test_a_record_that_cannot_be_formatted_becomes_a_fixed_line_and_never_reaches_stderr_raw(self):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            L.get_logger("t").info("two placeholders %s %s", "only one argument password=hunter2 dana@example.com")
            L.get_logger("t").info("%d", "not a number hunter2")
        self.assertEqual(err.getvalue(), "", "logging's default error report prints the raw message and arguments")
        docs = self.docs()
        self.assertEqual([d["event"] for d in docs], ["log_format_failed", "log_format_failed"])
        self.assertNotIn("hunter2", self.buf.getvalue())
        reset_logger()
        buf = io.StringIO()
        L.configure_logging("DEBUG", buf, fmt="text")
        with mock.patch.object(sys, "stderr", err):
            L.get_logger("t").info("%s %s", "x password=hunter2")
        self.assertEqual((err.getvalue(), buf.getvalue().strip()), ("", "log_format_failed"))

    def test_text_format_is_single_line_too(self):
        reset_logger()
        buf = io.StringIO()
        L.configure_logging("DEBUG", buf, fmt="text")
        L.get_logger("t").info("a\nb password=hunter2", extra={"token": "xyz", "note": "line1\r\nline2"})
        out = buf.getvalue()
        self.assertEqual(out.count("\n"), 1)
        self.assertNotIn("hunter2", out)
        self.assertNotIn("xyz", out)


class LogForging(LogCase):
    PAYLOADS = ['x\n{"level":"CRITICAL","event":"forged"}', "a\r\nb", "a\u2028b\u2029c", "a\x85b", "a\x1cb\x1db\x1e", "\x00\x07\x1b[31m",
                'q"uote', "tab\tsep", "\\n literal"]

    def test_no_payload_can_add_or_split_a_line(self):
        for p in self.PAYLOADS:
            self.buf.truncate(0)
            self.buf.seek(0)
            L.get_logger("t").info(p, extra={"field": p, "nested": {"k": p, p: "v"}, "lst": [p]})
            out = self.buf.getvalue()
            self.assertEqual(len(out.splitlines()), 1, repr(p))          # str.splitlines also breaks on \x85 \u2028 ...
            self.assertEqual(out.count("\n"), 1, repr(p))
            doc = json.loads(out)
            self.assertEqual(doc["level"], "INFO", "a forged level must not replace the real one")

    def test_user_text_in_an_exception_cannot_forge_lines(self):
        try:
            raise ValueError('boom\n{"level":"CRITICAL"}\u2028second')
        except ValueError:
            L.get_logger("t").error("failed", exc_info=True)
        self.assertEqual(len(self.buf.getvalue().splitlines()), 1)

    def test_extra_fields_on_a_plain_logger_cannot_overwrite_the_keys_the_formatter_writes(self):
        L.set_correlation_id("real-cid")
        L.get_logger("t").warning("real event", extra={"ts": "forged", "level": "CRITICAL", "event": "forged", "logger": "forged",
                                                      "exc": "forged", "exc_type": "forged"})
        (d,) = self.docs()
        self.assertEqual((d["level"], d["event"], d["logger"], d["cid"]), ("WARNING", "real event", "baror.t", "real-cid"))
        self.assertRegex(d["ts"], r"^\d{4}-")
        self.assertEqual({k: d[k] for k in ("f_ts", "f_event", "f_logger", "f_exc", "f_exc_type")}, {k: "forged" for k in
                         ("f_ts", "f_event", "f_logger", "f_exc", "f_exc_type")})
        self.assertEqual(d["f_level"], "CRITICAL", "the caller's value is kept, under a prefix")

    def test_log_event_fields_cannot_overwrite_the_record(self):
        L.log_event("login_failed", level="CRITICAL", event="forged", cid="forged", ts="forged")
        for d in self.docs():
            self.assertNotEqual(d["cid"], "forged")
            self.assertNotEqual(d["ts"], "forged")


class Redaction(LogCase):
    SECRETS = [
        ("password=hunter2 next", "hunter2"), ("pwd: 'my pass phrase'", "my pass"), ('Password="Tr0ub4dor&3"', "Tr0ub4dor"),
        ("token=abc.def.ghi", "abc.def"), ("Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi"),
        ("cookie: baror_session=xyz123", "xyz123"), ("api_key=sk-12345", "sk-12345"),
        ("session " + "A" * 43 + " end", "A" * 43), ("hash scrypt$32768$8$3$c2FsdA==$ZGs= end", "c2FsdA"),
        ("id 123456789 phone 054-1234567", "123456789"), ("tel 0541234567", "0541234567"),
    ]

    def test_secret_looking_values_in_free_text(self):
        for text, secret in self.SECRETS:
            self.buf.truncate(0)
            self.buf.seek(0)
            L.get_logger("t").info(text, extra={"detail": text})
            self.assertNotIn(secret, self.buf.getvalue(), text)

    def test_a_passphrase_with_spaces_is_redacted_to_the_end_of_the_line(self):
        L.get_logger("t").info("login failed password=correct horse battery staple for x")
        out = self.buf.getvalue()
        for word in ("correct", "horse", "battery", "staple"):
            self.assertNotIn(word, out)

    def test_one_long_string_without_an_at_sign_cannot_stall_the_logger(self):
        import time
        start = time.perf_counter()
        L.get_logger("t").info("a" * 2_000_000, extra={"f": "b" * 2_000_000 + "@", "g": ("x." * 1_000_000)})
        self.assertLess(time.perf_counter() - start, 2.0, "redaction work must be bounded")

    def test_a_secret_is_never_split_by_the_size_limit(self):
        # the cut moves to whitespace, so "password=..." cannot lose its label and leak the value
        text = "x " * 900 + "password=hunter2 " + "y" * 3000
        L.get_logger("t").info("m", extra={"f": text})
        self.assertNotIn("hunter2", self.buf.getvalue())
        tail = "z" * 10 + " " + "w" * 4000
        try:
            raise RuntimeError("a" * (L.MAX_TRACE * 4) + " password=hunter2 " + tail)
        except RuntimeError:
            L.get_logger("t").error("bug", exc_info=True)
        self.assertNotIn("hunter2", self.buf.getvalue())

    def test_secret_looking_field_names_are_redacted_whatever_the_value(self):
        fields = dict(password="p1", pw_hash="h1", session_token="t1", Authorization="a1", cookie="c1", api_key="k1",
                      user_password_confirm="p2", PASSWD="p3", client_secret="s1", session_id="sid")
        L.get_logger("t").info("x", extra=fields)
        out = self.buf.getvalue()
        for v in ("p1", "h1", "t1", "a1", "c1", "k1", "p2", "p3", "s1", "sid"):
            self.assertNotIn(f'"{v}"', out)
        self.assertEqual(out.count(L.REDACTED), len(fields))

    def test_nested_dicts_lists_and_tuples_are_cleaned_at_every_level(self):
        payload = {"user": {"password": "deep-pw", "email": EMAIL, "items": [{"token": "deep-token"}, f"mail {EMAIL}", ("x", {"cookie": "deep-c"})]},
                   "note": f"call {EMAIL}"}
        L.get_logger("t").info("x", extra={"payload": payload})
        out = self.buf.getvalue()
        for s in ("deep-pw", "deep-token", "deep-c", "Dana", "Example.com", "example.com"):
            self.assertNotIn(s, out)
        self.assertEqual(len(self.docs()), 1)

    def test_depth_and_size_of_structured_fields_are_bounded(self):
        deep = cur = {}
        for _ in range(50):
            cur["n"] = {}
            cur = cur["n"]
        L.get_logger("t").info("x", extra={"deep": deep, "wide": {str(i): i for i in range(10_000)}, "long": list(range(10_000))})
        doc = self.docs()[0]
        self.assertLessEqual(len(doc["wide"]), L.MAX_ITEMS)
        self.assertLessEqual(len(doc["long"]), L.MAX_ITEMS)
        self.assertLess(len(self.buf.getvalue()), 20_000)

    def test_emails_become_a_stable_keyed_token_and_never_appear(self):
        for text in (f"user {EMAIL} failed", f"x{EMAIL.lower()}y", f"<{EMAIL.upper()}>"):
            self.buf.truncate(0)
            self.buf.seek(0)
            L.get_logger("t").info(text, extra={"email": EMAIL, "contact": text})
            out = self.buf.getvalue()
            for part in ("dana", "cohen", "example.com"):
                self.assertNotIn(part, out.lower(), text)
            self.assertRegex(out, r"\[email:[0-9a-f]{12}\]")

    def test_the_token_correlates_the_same_address_and_separates_different_ones(self):
        a1, a2, b = L.pseudonymize(EMAIL), L.pseudonymize("  " + EMAIL.upper() + " "), L.pseudonymize("other@example.com")
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, b)
        self.assertRegex(a1, r"^[0-9a-f]{12}$")

    def test_the_token_is_keyed_not_a_plain_hash_and_depends_on_the_salt(self):
        plain = hashlib.sha256(EMAIL.lower().encode()).hexdigest()[:12]
        self.assertNotEqual(L.pseudonymize(EMAIL), plain, "a plain hash of an address can be confirmed by guessing")
        before = L.pseudonymize(EMAIL)
        with mock.patch.object(L, "_SALT", "another-salt"):
            self.assertNotEqual(L.pseudonymize(EMAIL), before)

    def test_a_nine_digit_run_inside_the_token_is_not_mangled(self):
        # the token is 12 hex digits: about 1 in 16 of them contain nine digits in a row, which the ID pattern would eat
        found = 0
        for i in range(2000):
            addr = f"user{i}@example.com"
            tok = L.pseudonymize(addr)
            if any(ch.isdigit() for ch in tok) and sum(c.isdigit() for c in tok) >= 9:
                found += 1
                self.assertIn(f"[email:{tok}]", L.redact_text(f"mail {addr}"))
        self.assertGreater(found, 0, "the sample must contain at least one token with many digits")

    def test_numbers_and_booleans_pass_through(self):
        L.get_logger("t").info("x", extra={"n": 7, "f": 1.5, "b": True, "none": None})
        d = self.docs()[0]
        self.assertEqual((d["n"], d["f"], d["b"], d["none"]), (7, 1.5, True, None))

    def test_tracebacks_are_redacted_and_bounded(self):
        def f():
            raise RuntimeError(f"failed for {EMAIL} password=hunter2 " + "x" * 50_000)
        try:
            f()
        except RuntimeError:
            L.get_logger("t").error("bug", exc_info=True)
        out = self.buf.getvalue()
        self.assertNotIn("hunter2", out)
        self.assertNotIn("Dana", out)
        doc = self.docs()[0]
        self.assertEqual(doc["exc_type"], "RuntimeError")
        self.assertLessEqual(len(doc["exc"]), L.MAX_TRACE + 10)
        self.assertLess(len(out), L.MAX_TRACE + 2000)

    def test_oversized_message_and_fields_are_clipped(self):
        L.get_logger("t").info("m" * 1_000_000, extra={"big": "b" * 1_000_000, "obj": object()})
        doc = self.docs()[0]
        self.assertLessEqual(len(doc["event"]), L.MAX_FIELD + 1)
        self.assertLessEqual(len(doc["big"]), L.MAX_FIELD + 1)
        self.assertLess(len(self.buf.getvalue()), 4000)

    def test_objects_that_cannot_be_printed_do_not_break_logging(self):
        class Bad:
            def __str__(self):
                raise RuntimeError("no")
        L.get_logger("t").info("x", extra={"bad": Bad(), "d": {Bad(): 1}})
        self.assertEqual(self.docs()[0]["bad"], "[unprintable]")


class Correlation(LogCase):
    def test_default_and_set(self):
        L.set_correlation_id("-")
        L.get_logger("t").info("x")
        self.assertEqual(self.docs()[0]["cid"], "-")

    def test_ids_have_the_documented_shape(self):
        self.assertRegex(L.new_correlation_id(), r"^[0-9a-f]{12}$")
        ids = {L.new_incident_id() for _ in range(500)}
        self.assertEqual(len(ids), 500)
        self.assertTrue(all(len(i) == 8 and i == i.upper() for i in ids))

    def test_concurrent_threads_keep_their_own_correlation_id_and_their_lines_intact(self):
        n_threads, per = 8, 200

        def work(k):
            L.set_correlation_id(f"cid{k}")
            for i in range(per):
                L.get_logger("t").info(f"thread {k} msg {i}", extra={"k": k, "i": i, "email": f"u{k}@example.com"})
        ts = [threading.Thread(target=work, args=(k,)) for k in range(n_threads)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        docs = self.docs()                      # every line must parse: no interleaving inside a line
        self.assertEqual(len(docs), n_threads * per)
        for d in docs:
            self.assertEqual(d["cid"], f"cid{d['k']}")
            self.assertEqual(d["event"], f"thread {d['k']} msg {d['i']}")
        self.assertEqual(len({(d["k"], d["i"]) for d in docs}), n_threads * per)


class LogEvent(LogCase):
    def test_a_normal_event(self):
        L.log_event("result_added", user=7, run_tenths=9400, email=EMAIL)
        (d,) = self.docs()
        self.assertEqual((d["logger"], d["event"], d["audit"], d["user"], d["run_tenths"]), ("baror.audit", "result_added", True, 7, 9400))
        self.assertRegex(d["email"], r"^\[email:[0-9a-f]{12}\]$")
        self.assertNotIn("Dana", self.buf.getvalue())

    def test_event_names_are_validated_not_trusted(self):
        for bad in ("Bad Name", "", "x" * 100, None, 5, "a\nb", "1abc", "UPPER", 'q"'):
            self.buf.truncate(0)
            self.buf.seek(0)
            L.log_event(bad)
            (d,) = self.docs()
            self.assertEqual(d["event"], "invalid_event", repr(bad))
            self.assertEqual(len(self.buf.getvalue().splitlines()), 1)

    def test_fields_that_collide_with_record_attributes_are_kept_under_a_prefix_and_never_raise(self):
        L.log_event("login_failed", name="n", message="m", args="a", msg="g", exc_info="e", levelname="l", cid="c", audit="x",
                    _private="p", lineno=3)
        (d,) = self.docs()
        for k in ("f_name", "f_message", "f_args", "f_msg", "f_exc_info", "f_levelname", "f_cid", "f_audit", "f_private", "f_lineno"):
            self.assertIn(k, d)
        self.assertEqual(d["event"], "login_failed")

    def test_it_never_raises_even_when_logging_itself_fails(self):
        with mock.patch.object(logging.Logger, "log", side_effect=RuntimeError("disk full")):
            with mock.patch.object(sys, "stderr", io.StringIO()):
                L.log_event("login_ok", user=1)
        L.log_event("login_ok", level="not a level", **{"x": object()})

    def test_level_is_honoured(self):
        L.log_event("rate_limited", level=logging.WARNING, seconds=30)
        self.assertEqual(self.docs()[0]["level"], "WARNING")


class FullLoginFlow(LogCase):
    """What the app wiring will do: log the events of a registration, a failed and a successful sign-in, a session and
    an unexpected failure, passing the sensitive values the way a careless call site might. Nothing sensitive may
    reach the output, from any module."""

    def test_no_password_token_hash_or_raw_address_in_any_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            db.init_db(path)
            service.reset_unknown_email_backoff()
            email, pw, pw2 = "noa.levi@Example.com", "correct horse battery", "wrong password 1"
            user = service.register(path, email=email, name="Noa Levi", gender="F", is_km=False, password=pw)
            L.log_event("register", email=email, password=pw, user=user["id"])
            try:
                service.login(path, email, pw2)
            except AuthError as exc:
                L.log_event("login_failed", email=email, password=pw2, reason=exc.message_key)
                L.get_logger("t").warning(f"login failed for {email} with password={pw2}", exc_info=True,
                                          extra={"detail": f"{email} password={pw2}", "payload": {"password": pw2, "email": email}})
            u = service.login(path, email, pw)
            token = sessions.issue(path, u["id"])
            record = repo.get_login_record(path, email)
            L.log_event("login_ok", email=email, user=u["id"], session_token=token, cookie=f"baror_session={token}",
                        note=f"token {token} for {email}", pw_hash=record["pw_hash"], detail=record["pw_hash"])
            try:
                # A secret that is NOT labelled cannot be recognised by any pattern, so call sites must pass secrets as
                # named fields (always redacted). Labelled forms are caught, including a passphrase with spaces:
                raise RuntimeError(f"crash while handling {email}, password={pw}")
            except RuntimeError:
                L.get_logger("t").error("unhandled_exception", exc_info=True)
            out = self.buf.getvalue()
            self.assertGreater(len(self.docs()), 4)
            forbidden = [pw, pw2, token, record["pw_hash"], record["pw_hash"].split("$")[4], email, email.lower(), "noa.levi", "Example.com",
                         "example.com", "Noa Levi"]
            for secret in forbidden:
                self.assertNotIn(secret, out, f"{secret[:12]!r} leaked")
            tokens = {d["email"] for d in self.docs() if "email" in d}
            self.assertEqual(len(tokens), 1, "the same person gets the same token in every event")


if __name__ == "__main__":
    unittest.main()
