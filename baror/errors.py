"""Domain exception hierarchy for Baror App.

Rules
-----
* Pure stdlib, no ``streamlit`` import (so it is unit-testable and importable by
  the healthcheck / entrypoint, which run outside Streamlit).
* ``str(exc)`` is INTERNAL text (goes to the log only). What the cadet sees is
  looked up in ``messages_he`` through ``message_key`` + ``params``.
* ``params`` may only hold values that are safe to show (numbers, fixed labels),
  never user-typed free text (Markdown injection) and never PII.
* Chain causes with ``raise X(...) from original`` so the log keeps the real traceback.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

# UI severity -> which st.* component the boundary uses (see boundary.render_message)
INFO, WARNING, ERROR, CRITICAL = "info", "warning", "error", "critical"


class BarorError(Exception):
    """Base class. Anything that is a BarorError is an EXPECTED condition."""

    code = "E-GENERIC"            # stable identifier for support docs / log queries
    severity = ERROR              # UI severity
    log_level = logging.ERROR     # what the boundary logs it at
    show_incident_id = False      # show a support code to the user?
    message_key = "UNEXPECTED"    # default key in messages_he.MESSAGES

    def __init__(
        self,
        message_key: str | None = None,
        *,
        params: Mapping[str, Any] | None = None,
        field: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.message_key = message_key or type(self).message_key
        self.params = dict(params or {})
        self.field = field            # name of the offending form field, if any
        self.detail = detail          # internal-only explanation
        super().__init__(detail or self.message_key)


class ValidationError(BarorError):
    """User input is not acceptable. Recoverable, user's next action fixes it."""
    code, severity, log_level = "E-VALIDATION", WARNING, logging.INFO
    message_key = "INVALID_INPUT"


class AuthError(BarorError):
    """Wrong credentials. Message must NOT reveal which part was wrong."""
    code, severity, log_level = "E-AUTH", ERROR, logging.WARNING
    message_key = "AUTH_FAILED"


class SessionExpiredError(AuthError):
    code, severity, log_level = "E-SESSION", INFO, logging.INFO
    message_key = "SESSION_EXPIRED"


class RateLimitedError(AuthError):
    code, severity, log_level = "E-RATE", WARNING, logging.WARNING
    message_key = "RATE_LIMITED"


class NotFoundError(BarorError):
    code, severity, log_level = "E-NOTFOUND", WARNING, logging.WARNING
    message_key = "ACCOUNT_NOT_FOUND"


class ConflictError(BarorError):
    """State conflict, e.g. duplicate e-mail."""
    code, severity, log_level = "E-CONFLICT", WARNING, logging.INFO
    message_key = "DUPLICATE_EMAIL"


class RegistrationDeniedError(BarorError):
    """Registration refused. ONE class and ONE message for 'account already exists' (and 'released address') and
    'malformed e-mail', so the form is not an account-existence oracle. The reason goes to ``detail`` (server log only,
    never to the user, and never contains the e-mail)."""
    code, severity, log_level = "E-REGISTER", WARNING, logging.INFO
    message_key = "REGISTRATION_DENIED"


class SignupBusyError(BarorError):
    """The hourly sign-up cap (``BAROR_SIGNUP_CAP_PER_HOUR``) is used up, or another sign-up won the last place. Says
    nothing about the e-mail or any account, so it is safe to show; the person tries again later."""
    code, severity, log_level = "E-SIGNUP-BUSY", WARNING, logging.WARNING
    message_key = "SIGNUP_BUSY"


class RegistrationClosedError(BarorError):
    """The operator closed registration (``BAROR_REGISTRATION_OPEN=0``, the kill switch)."""
    code, severity, log_level = "E-REG-CLOSED", INFO, logging.INFO
    message_key = "REGISTRATION_CLOSED"


class StorageError(BarorError):
    """The data store cannot be read or written (permissions, disk full, I/O)."""
    code, severity, log_level = "E-STORAGE", ERROR, logging.ERROR
    show_incident_id = True
    message_key = "STORAGE_UNAVAILABLE"


class CorruptedDataError(StorageError):
    """The data file exists but cannot be parsed. NEVER auto-overwrite it."""
    code, severity, log_level = "E-CORRUPT", CRITICAL, logging.CRITICAL
    message_key = "DATA_CORRUPTED"


class ConfigError(BarorError):
    """Deployment/environment problem found at startup."""
    code, severity, log_level = "E-CONFIG", CRITICAL, logging.CRITICAL
    show_incident_id = True
    message_key = "SERVICE_UNAVAILABLE"


class MaintenanceError(BarorError):
    code, severity, log_level = "E-MAINT", INFO, logging.INFO
    message_key = "MAINTENANCE"


ALL_ERRORS = (
    BarorError, ValidationError, AuthError, SessionExpiredError, RateLimitedError,
    NotFoundError, ConflictError, RegistrationDeniedError, SignupBusyError, RegistrationClosedError, StorageError,
    CorruptedDataError, ConfigError, MaintenanceError,
)
