"""Top-level error boundary for a Streamlit script (task T8.1). The only place in ``baror/`` outside ``baror/ui`` that
imports streamlit.

Contract
--------
* ``except Exception`` ONLY. In Streamlit 1.66.1 ``st.stop()`` raises ``StopException`` and ``st.rerun()`` raises
  ``RerunException``; both derive from ``ScriptControlException(BaseException)``, so they, ``KeyboardInterrupt`` and
  ``SystemExit`` pass through untouched. Never write ``except:`` or ``except BaseException`` around app code.
* ``BarorError`` (an expected condition)  -> the Hebrew text from ``baror.messages_he`` (title / why / action; the
  incident code only for errors that ask for it), logged at the error's own level.
* anything else (a bug)                    -> the generic "something went wrong" Hebrew text plus a short random incident
  id; the full traceback is logged with the same id. The exception's own text is NEVER rendered.
* Nothing here calls ``st.exception()``: it ignores ``client.showErrorDetails`` and prints the full message.
  ``.streamlit/config.toml`` keeps ``showErrorDetails = "none"`` as the second line of defence (tests check both).

Wiring (see ``run_app``)::

    boundary.run_app(main)                       # begin_run + flash message + guarded main
    @st.fragment
    @boundary.ErrorBoundary(where="history")     # fragments and dialogs need their own boundary
    def history(): ...
    st.button("x", on_click=boundary.guarded_callback(fn))   # callbacks run BEFORE the script body
"""
from __future__ import annotations

import contextlib
import functools
import logging
import types
from dataclasses import dataclass
from typing import Callable

import streamlit as st

from baror import messages_he as M
from baror.errors import CRITICAL, ERROR, INFO, WARNING, BarorError
from baror.logging_setup import (configure_logging, get_logger, new_correlation_id, new_incident_id,
                                 set_correlation_id)

FLASH_KEY = "_flash"
CID_KEY = "_cid"
_FALLBACK = M.render_error("UNEXPECTED")       # computed at import: usable even if rendering the real message fails


@dataclass(frozen=True)
class Incident:
    incident_id: str
    message_key: str
    severity: str
    expected: bool          # True for a BarorError, False for a bug
    code: str


def render_message(markdown: str, severity: str) -> None:
    """Severity -> Streamlit component. One place to change the look. ``markdown`` is catalogue text only."""
    if severity == INFO:
        st.info(markdown, icon=":material/info:")
    elif severity == WARNING:
        st.warning(markdown, icon=":material/warning:")
    else:                                   # ERROR and CRITICAL
        st.error(markdown, icon=":material/error:")


def _text_for(exc: Exception, incident_id: str) -> tuple[str, str]:
    """(markdown, severity) for what the user may see. Built ONLY from the message catalogue and numeric params."""
    if isinstance(exc, BarorError):
        # Only numbers may be formatted into catalogue text: a caller that put free text in ``params`` must not be
        # able to inject Markdown into the page.
        safe = types.SimpleNamespace(
            message_key=exc.message_key,
            params={k: v for k, v in exc.params.items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
        return M.render_exception(safe, incident_id if exc.show_incident_id else None), exc.severity
    return M.render_error("UNEXPECTED") + f"\n\n{M.INCIDENT_LABEL}: `{incident_id}`", ERROR


def handle_exception(exc: Exception, *, where: str = "main", logger: logging.Logger | None = None,
                     show: bool = True) -> Incident:
    """Classify, log (with traceback and incident id) and optionally render."""
    log = logger or get_logger("boundary")
    incident_id = new_incident_id()
    if isinstance(exc, BarorError):
        inc = Incident(incident_id, exc.message_key, exc.severity, True, exc.code)
        extra = {"incident": incident_id, "code": exc.code, "msg_key": exc.message_key, "field": exc.field,
                 "where": where, "detail": exc.detail}
        # tracebacks only for operational failures; a user's typo is one line
        log.log(exc.log_level, "domain_error", extra=extra, exc_info=exc if exc.log_level >= logging.ERROR else None)
    else:
        inc = Incident(incident_id, "UNEXPECTED", ERROR, False, "E-UNEXPECTED")
        log.error("unhandled_exception", exc_info=exc,
                  extra={"incident": incident_id, "code": inc.code, "where": where, "exc_class": type(exc).__name__})
    if show:
        text, severity = _text_for(exc, incident_id)
        render_message(text, severity)
    return inc


class ErrorBoundary(contextlib.ContextDecorator):
    """``with ErrorBoundary(): main()`` or ``@ErrorBoundary(where="save")``. ``stop=True`` ends the run after rendering."""

    def __init__(self, *, where: str = "main", stop: bool = False, logger: logging.Logger | None = None):
        self.where, self.stop, self.logger = where, stop, logger

    def __enter__(self) -> "ErrorBoundary":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            return False
        if not isinstance(exc, Exception):   # StopException, RerunException, KeyboardInterrupt, SystemExit ...
            return False
        try:
            handle_exception(exc, where=self.where, logger=self.logger)
        except Exception:                    # the handler itself failed: last resort, never loop
            with contextlib.suppress(Exception):
                get_logger("boundary").exception("boundary_failure")
            with contextlib.suppress(Exception):
                st.error(_FALLBACK)
        if self.stop:
            st.stop()                        # raises StopException out of __exit__ (intended)
        return True                          # swallow the original exception


def guarded_callback(fn: Callable, *, where: str = "callback") -> Callable:
    """Wrap an ``on_click`` / ``on_change`` / form callback. Callbacks run BEFORE the script body, so anything they
    render is wiped by the rerun that follows: the message is stashed in ``session_state`` and ``show_flash`` (called
    by ``run_app``) renders it on the next run."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as exc:             # Stop/Rerun are BaseException -> untouched
            try:
                inc = handle_exception(exc, where=where, show=False)
                st.session_state[FLASH_KEY] = _text_for(exc, inc.incident_id)
            except Exception:
                with contextlib.suppress(Exception):
                    get_logger("boundary").exception("boundary_failure")
                    st.session_state[FLASH_KEY] = (_FALLBACK, ERROR)
    return wrapper


def show_flash() -> None:
    """Render and clear a message stashed by ``guarded_callback``."""
    flash = st.session_state.pop(FLASH_KEY, None)
    if flash:
        render_message(*flash)


def begin_run() -> str:
    """First thing in every script run: idempotent logging setup + this browser session's correlation id."""
    configure_logging()
    cid = st.session_state.setdefault(CID_KEY, new_correlation_id())
    set_correlation_id(cid)
    return cid


def run_app(main: Callable[[], None]) -> None:
    """``begin_run``, the flash message of a failed callback, then ``main`` inside the boundary."""
    begin_run()
    with ErrorBoundary(where="main"):
        show_flash()
        main()
