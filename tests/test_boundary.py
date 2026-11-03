"""T8.1: the error boundary under AppTest. Abuse cases: secrets / HTML / Markdown in exception text never reach the page,
Streamlit control flow passes through, the handler itself failing, huge messages, fragments / dialogs / form handlers /
callbacks, log forging through an exception message."""
import contextlib
import io
import json
import re
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import streamlit as st
from streamlit.runtime.scriptrunner_utils.exceptions import RerunData, RerunException, StopException
from streamlit.testing.v1 import AppTest

from baror import boundary
from baror import logging_setup as L
from baror import messages_he as M
from baror.errors import (ConfigError, CorruptedDataError, MaintenanceError, RateLimitedError, StorageError, ValidationError)

ROOT = Path(__file__).resolve().parent.parent
HEAD = ("import streamlit as st\nfrom baror import boundary, errors\nfrom baror.boundary import ErrorBoundary\n"
        "boundary.begin_run()\n")
# password= redacts to the end of the line, so it comes last
SECRET_TEXT = ("<script>alert(1)</script> **bold** [x](http://evil.example) dana.cohen@example.com token=secret-token-xyz "
               "scrypt$32768$8$3$c2FsdA==$ZGs= password=hunter2")


def all_text(at) -> str:
    """Every string the page would show: element values and labels, recursively, plus any exception element."""
    out = []

    def walk(block):
        for child in block.children.values():
            for attr in ("value", "label", "body"):
                v = getattr(child, attr, None)
                if isinstance(v, str):
                    out.append(v)
            if hasattr(child, "children"):
                walk(child)
    walk(at.main)
    walk(at.sidebar)
    out += [str(getattr(e, "value", "")) for e in at.exception]
    return "\n".join(out)


class BoundaryCase(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        patcher = mock.patch("sys.stdout", self.out)          # the stdout handler resolves sys.stdout at emit time
        patcher.start()
        self.addCleanup(patcher.stop)
        log = L.logging.getLogger(L.LOGGER_NAME)
        for h in list(log.handlers):
            log.removeHandler(h)
        self.addCleanup(lambda: [log.removeHandler(h) for h in list(log.handlers)])

    def run_script(self, body: str, **kw) -> AppTest:
        return AppTest.from_string(HEAD + body, default_timeout=30).run(**kw)

    def logged(self) -> list[dict]:
        return [json.loads(ln) for ln in self.out.getvalue().split("\n") if ln.startswith("{")]


class UnexpectedExceptions(BoundaryCase):
    def test_a_bug_shows_the_generic_text_and_an_incident_id_and_nothing_of_the_exception(self):
        at = self.run_script(f"def main():\n    raise RuntimeError({SECRET_TEXT!r})\nboundary.run_app(main)\n")
        self.assertFalse(at.exception, "no Streamlit exception element")
        page = all_text(at)
        self.assertIn(M.ERRORS["UNEXPECTED"].title, page)
        self.assertEqual(len(at.error), 1)
        incident = re.search(r"`([0-9A-F]{8})`", page).group(1)
        for secret in ("script", "bold", "evil.example", "hunter2", "dana", "example.com", "secret-token", "RuntimeError",
                       "Traceback", "scrypt", "c2FsdA"):
            self.assertNotIn(secret, page, secret)
        (rec,) = [d for d in self.logged() if d["event"] == "unhandled_exception"]
        self.assertEqual(rec["incident"], incident, "the page and the log carry the same incident id")
        self.assertEqual((rec["exc_type"], rec["where"], rec["level"]), ("RuntimeError", "main", "ERROR"))
        self.assertIn("Traceback", rec["exc"])
        for secret in ("hunter2", "dana", "secret-token", "c2FsdA", "example.com"):
            self.assertNotIn(secret, self.out.getvalue(), f"{secret} leaked into the log")
        self.assertRegex(self.out.getvalue(), r"\[email:[0-9a-f]{12}\]")

    def test_two_failures_get_two_different_incident_ids(self):
        ids = set()
        for _ in range(3):
            at = self.run_script("def main():\n    raise ValueError('x')\nboundary.run_app(main)\n")
            ids.add(re.search(r"`([0-9A-F]{8})`", all_text(at)).group(1))
        self.assertEqual(len(ids), 3)

    def test_a_huge_exception_message_cannot_flood_the_page_or_the_log(self):
        at = self.run_script("def main():\n    raise RuntimeError('x' * 2_000_000)\nboundary.run_app(main)\n")
        self.assertLess(len(all_text(at)), 1500)
        self.assertLess(max(len(ln) for ln in self.out.getvalue().split("\n")), 20_000)

    def test_a_chained_exception_is_logged_redacted_and_not_shown(self):
        body = ("def main():\n    try:\n        raise KeyError('password=hunter2')\n"
                "    except KeyError as e:\n        raise RuntimeError('wrapped') from e\nboundary.run_app(main)\n")
        at = self.run_script(body)
        self.assertNotIn("hunter2", all_text(at))
        self.assertNotIn("hunter2", self.out.getvalue())
        self.assertIn("KeyError", self.logged()[-1]["exc"])

    def test_log_forging_through_an_exception_message_is_one_line(self):
        at = self.run_script("def main():\n    raise RuntimeError('boom\\n{\"level\":\"CRITICAL\",\"event\":\"forged\"}\\u2028x')\n"
                             "boundary.run_app(main)\n")
        self.assertFalse(at.exception)
        events = [d["event"] for d in self.logged()]
        self.assertNotIn("forged", events)
        self.assertEqual(sum(1 for ln in self.out.getvalue().split("\n") if ln.startswith("{")), len(self.logged()))

    def test_st_exception_is_never_used_and_error_details_stay_off(self):
        import ast
        for f in [ROOT / "app.py", *sorted((ROOT / "baror").rglob("*.py"))]:
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "exception":
                    self.assertNotEqual(getattr(node.func.value, "id", ""), "st", f"{f.name}:{node.lineno} calls st.exception()")
        cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["client"]["showErrorDetails"], "none")


class ExpectedErrors(BoundaryCase):
    def page(self, expr: str) -> AppTest:
        return self.run_script(f"def main():\n    raise {expr}\nboundary.run_app(main)\n")

    def test_severity_decides_the_component_and_the_text_comes_from_the_catalogue(self):
        at = self.page("errors.ValidationError('TIME_ZERO')")
        self.assertEqual(len(at.warning), 1)
        self.assertEqual(at.warning[0].value, M.render_error("TIME_ZERO"))
        at = self.page("errors.MaintenanceError()")
        self.assertEqual(at.info[0].value, M.render_error("MAINTENANCE"))
        at = self.page("errors.SessionExpiredError()")
        self.assertEqual(at.info[0].value, M.render_error("SESSION_EXPIRED"))
        for expr, key in (("errors.StorageError(detail='disk full')", "STORAGE_UNAVAILABLE"),
                          ("errors.CorruptedDataError(detail='x')", "DATA_CORRUPTED"),
                          ("errors.ConfigError(detail='x')", "SERVICE_UNAVAILABLE")):
            at = self.page(expr)
            self.assertEqual(len(at.error), 1, key)
            self.assertIn(M.ERRORS[key].title, at.error[0].value)
            self.assertRegex(at.error[0].value, r"`[0-9A-F]{8}`", "operational errors carry an incident code")

    def test_user_mistakes_show_no_incident_code_and_log_one_line_without_a_traceback(self):
        at = self.page("errors.ValidationError('TIME_SECONDS_RANGE', field='run_time', detail='seconds=99')")
        self.assertNotRegex(at.warning[0].value, r"[0-9A-F]{8}")
        (rec,) = [d for d in self.logged() if d["event"] == "domain_error"]
        self.assertEqual((rec["level"], rec["msg_key"], rec["field"]), ("INFO", "TIME_SECONDS_RANGE", "run_time"))
        self.assertNotIn("exc", rec)

    def test_operational_errors_log_the_internal_detail_with_a_traceback_but_redacted(self):
        self.page("errors.StorageError(detail='cannot write /data/baror.db for dana@example.com password=x')")
        rec = [d for d in self.logged() if d["event"] == "domain_error"][0]
        self.assertEqual((rec["level"], rec["code"]), ("ERROR", "E-STORAGE"))
        self.assertIn("/data/baror.db", rec["detail"])
        self.assertNotIn("dana", self.out.getvalue())
        self.assertIn("Traceback", rec["exc"])

    def test_rate_limit_wait_is_a_phrase_and_params_that_are_not_numbers_are_dropped(self):
        at = self.page("errors.RateLimitedError(params={'seconds': 130})")
        self.assertIn("3 דקות", all_text(at))
        at = self.page("errors.ValidationError('TIME_OUT_OF_RANGE', params={'lo': '**evil**', 'hi': '<b>x</b>'})")
        page = all_text(at)
        self.assertNotIn("evil", page)
        self.assertNotIn("<b>", page)
        self.assertFalse(at.exception)

    def test_unknown_message_key_degrades_to_the_generic_text(self):
        at = self.page("errors.BarorError('NO_SUCH_KEY', detail='x')")
        self.assertIn(M.ERRORS["UNEXPECTED"].title, all_text(at))
        self.assertFalse(at.exception)

    def test_the_detail_of_a_domain_error_never_reaches_the_page(self):
        at = self.page("errors.ValidationError('INVALID_INPUT', detail='SECRET-DETAIL password=hunter2')")
        self.assertNotIn("SECRET-DETAIL", all_text(at))
        self.assertNotIn("hunter2", all_text(at))


class ControlFlow(BoundaryCase):
    def test_st_stop_inside_the_boundary_ends_the_run_without_a_message(self):
        at = self.run_script("with ErrorBoundary():\n    st.write('before')\n    st.stop()\nst.write('after')\n")
        page = all_text(at)
        self.assertIn("before", page)
        self.assertNotIn("after", page)
        self.assertFalse(at.error or at.exception)

    def test_st_rerun_inside_the_boundary_reruns(self):
        at = self.run_script("st.session_state.setdefault('n', 0)\nwith ErrorBoundary():\n    if st.session_state.n < 2:\n"
                             "        st.session_state.n += 1\n        st.rerun()\nst.write(f'n={st.session_state.n}')\n")
        self.assertIn("n=2", all_text(at))
        self.assertFalse(at.error or at.exception)

    def test_control_flow_and_process_exceptions_pass_through_untouched(self):
        for exc in (StopException(), RerunException(RerunData()), KeyboardInterrupt(), SystemExit(3), GeneratorExit()):
            with self.assertRaises(type(exc), msg=repr(exc)):
                with boundary.ErrorBoundary():
                    raise exc
        self.assertEqual(self.logged(), [])

    def test_stop_option_renders_the_message_then_ends_the_run(self):
        at = self.run_script("with ErrorBoundary(stop=True):\n    raise RuntimeError('x')\nst.write('after')\n")
        self.assertEqual(len(at.error), 1)
        self.assertNotIn("after", all_text(at))

    def test_normal_code_is_untouched_and_the_boundary_is_reusable(self):
        at = self.run_script("b = ErrorBoundary()\nfor i in range(3):\n    with b:\n        st.write(f'ok{i}')\n")
        self.assertEqual([m.value for m in at.markdown], ["ok0", "ok1", "ok2"])


class WhereItRuns(BoundaryCase):
    def test_a_fragment_with_its_own_boundary_fails_alone(self):
        body = ("@st.fragment\n@ErrorBoundary(where='history')\ndef history():\n    raise RuntimeError('frag boom')\n"
                "st.write('top')\nhistory()\nst.write('bottom')\n")
        at = self.run_script(body)
        page = all_text(at)
        self.assertFalse(at.exception)
        self.assertIn("top", page)
        self.assertIn("bottom", page)
        self.assertIn(M.ERRORS["UNEXPECTED"].title, page)
        self.assertNotIn("frag boom", page)
        self.assertEqual([d["where"] for d in self.logged() if d["event"] == "unhandled_exception"], ["history"])

    def test_a_dialog_with_its_own_boundary(self):
        body = ("@st.dialog('d')\n@ErrorBoundary(where='dialog')\ndef dlg():\n    raise RuntimeError('dialog boom')\n"
                "if st.button('open'):\n    dlg()\nst.write('page')\n")
        at = AppTest.from_string(HEAD + body, default_timeout=30).run()
        at.button[0].click().run()
        self.assertFalse(at.exception)
        self.assertNotIn("dialog boom", all_text(at))
        self.assertIn("page", all_text(at))
        failed = [d for d in self.logged() if d["event"] == "unhandled_exception"]
        self.assertEqual([d["where"] for d in failed], ["dialog"], "the dialog body ran inside the boundary")

    def test_a_form_submit_handler_keeps_the_typed_values(self):
        body = ("with ErrorBoundary(where='save'):\n    with st.form('f'):\n        v = st.text_input('v', key='v')\n"
                "        go = st.form_submit_button('go')\n    if go:\n        raise RuntimeError('save boom ' + v)\n")
        at = AppTest.from_string(HEAD + body, default_timeout=30).run()
        at.text_input(key="v").set_value("typed value password=hunter2")
        at.button[0].click().run()
        self.assertEqual(at.text_input(key="v").value, "typed value password=hunter2", "input is kept")
        self.assertNotIn("hunter2", all_text(at).replace("typed value password=hunter2", ""))
        self.assertEqual(len(at.error), 1)
        self.assertNotIn("hunter2", self.out.getvalue())

    def test_a_failing_callback_is_shown_on_the_next_run_and_only_once(self):
        body = ("def bad():\n    raise RuntimeError('cb boom password=hunter2')\n"
                "def main():\n    st.button('go', on_click=boundary.guarded_callback(bad, where='cb'))\n    st.write('rendered')\n"
                "boundary.run_app(main)\n")
        at = AppTest.from_string(HEAD + body, default_timeout=30).run()
        self.assertFalse(at.error)
        at.button[0].click().run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.error), 1)
        self.assertRegex(at.error[0].value, r"`[0-9A-F]{8}`")
        self.assertNotIn("cb boom", all_text(at))
        at.run()
        self.assertFalse(at.error, "the flash message is cleared after it was shown")
        self.assertEqual([d["where"] for d in self.logged() if d["event"] == "unhandled_exception"], ["cb"])

    def test_a_failing_domain_callback_keeps_its_own_severity(self):
        body = ("def bad():\n    raise errors.ValidationError('TIME_ZERO')\n"
                "def main():\n    st.button('go', on_click=boundary.guarded_callback(bad))\nboundary.run_app(main)\n")
        at = AppTest.from_string(HEAD + body, default_timeout=30).run()
        at.button[0].click().run()
        self.assertEqual(len(at.warning), 1)
        self.assertEqual(at.warning[0].value, M.render_error("TIME_ZERO"))


class HandlerFailure(BoundaryCase):
    def test_if_rendering_the_message_fails_a_fixed_fallback_is_shown_and_nothing_loops(self):
        real = boundary.render_message
        calls = []

        def flaky(markdown, severity):
            calls.append(markdown)
            raise RuntimeError("cannot render")
        body = "def main():\n    raise RuntimeError('x')\nboundary.run_app(main)\n"
        with mock.patch.object(boundary, "render_message", flaky):
            at = AppTest.from_string(HEAD + body, default_timeout=30).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(calls), 1)
        self.assertEqual(at.error[0].value, M.render_error("UNEXPECTED"))
        self.assertTrue([d for d in self.logged() if d["event"] == "boundary_failure"])
        self.assertIs(boundary.render_message, real)

    def test_a_failure_while_logging_does_not_escape_either(self):
        body = "def main():\n    raise RuntimeError('x')\nboundary.run_app(main)\n"
        with mock.patch.object(boundary, "handle_exception", side_effect=RuntimeError("log down")):
            at = AppTest.from_string(HEAD + body, default_timeout=30).run()
        self.assertFalse(at.exception)
        self.assertEqual(at.error[0].value, M.render_error("UNEXPECTED"))


class Correlation(BoundaryCase):
    def test_a_session_keeps_one_correlation_id_across_runs_and_sessions_differ(self):
        body = ("def main():\n    st.button('b')\n    errors.log_event if False else None\n"
                "    from baror import logging_setup\n    logging_setup.get_logger('t').info('run')\nboundary.run_app(main)\n")
        a = AppTest.from_string(HEAD + body, default_timeout=30).run()
        a.button[0].click().run()
        b = AppTest.from_string(HEAD + body, default_timeout=30).run()
        cids = [d["cid"] for d in self.logged() if d["event"] == "run"]
        self.assertEqual(len(cids), 3)
        self.assertEqual(cids[0], cids[1], "same browser session")
        self.assertNotEqual(cids[0], cids[2], "another browser session")
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{12}", c) for c in cids))

    def test_begin_run_is_idempotent_for_the_handler(self):
        body = "boundary.begin_run()\nboundary.begin_run()\nst.write(len(__import__('logging').getLogger('baror').handlers))\n"
        at = AppTest.from_string(HEAD + body, default_timeout=30).run()
        at.run()
        self.assertEqual(at.markdown[0].value, "`1`")


class HandleException(BoundaryCase):
    def test_return_value_and_flags(self):
        L.configure_logging("DEBUG", io.StringIO())
        inc = boundary.handle_exception(ValueError("x"), show=False)
        self.assertEqual((inc.expected, inc.code, inc.message_key), (False, "E-UNEXPECTED", "UNEXPECTED"))
        self.assertRegex(inc.incident_id, r"^[0-9A-F]{8}$")
        inc = boundary.handle_exception(ConfigError(detail="x"), show=False)
        self.assertEqual((inc.expected, inc.code, inc.severity), (True, "E-CONFIG", "critical"))


if __name__ == "__main__":
    unittest.main()
