"""Structured logging for a Streamlit app running in Docker. Standard library only; never imports streamlit.

* One JSON object per line on stdout (Docker's json-file driver captures it), logger name ``baror``.
* ``configure_logging()`` is IDEMPOTENT and thread-safe: Streamlit re-executes the script on every interaction, but the
  handler is found by a marker on the logger, so it is never duplicated (not even after a module reload).
* A correlation id (``cid``) per browser session travels in a ContextVar; ``begin_run`` in ``baror.boundary`` sets it.
* Redaction is a safety net, not a licence to log personal data:
  - e-mail addresses (in free text, in extra fields, in tracebacks) become ``[email:<12 hex>]``, a KEYED hash
    (HMAC-SHA256 with a per-process salt, or ``LOG_SALT`` for stability across restarts): the same address gives the same
    token so events can be correlated, and the address cannot be recovered or confirmed without the salt;
  - values of fields whose NAME looks secret (password, token, cookie, authorization, ...) are replaced wholesale,
    recursively inside dicts / lists;
  - secret-looking VALUES in free text (``password=...``, ``Bearer ...``, 43-character session tokens, scrypt hashes) too.
* ``json.dumps`` escapes CR/LF and other control characters, and U+2028/U+2029/U+0085/U+007F are escaped here, so
  user-typed text cannot forge or split a log line.
* Logging must never break the application: ``log_event`` swallows its own failures.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import IO, Any

LOGGER_NAME = "baror"
AUDIT_LOGGER_NAME = "baror.audit"
_MARKER = "_baror_handler"
_lock = threading.Lock()

_cid: contextvars.ContextVar[str] = contextvars.ContextVar("baror_cid", default="-")

# ------------------------------------------------------------------ limits
MAX_FIELD = 500            # characters per string value
MAX_TRACE = 8000           # characters of traceback kept (the end, where the cause is)
MAX_ITEMS = 50             # entries kept from a dict / list field
MAX_DEPTH = 4              # nesting followed in a dict / list field
REDACTED = "[REDACTED]"

# ------------------------------------------------------------------ redaction patterns
# Quantifiers are BOUNDED (RFC limits): an unbounded `[\w.%+-]+@` backtracks quadratically on a long string without an
# "@", which would let a cadet stall the logger with one long input.
_EMAIL_RE = re.compile(r"[\w.%+\-]{1,64}@[\w\-]{1,63}(?:\.[\w\-]{1,63}){1,8}", re.UNICODE)
_KV_PASSWORD_RE = re.compile(r"(?i)\b(pass(?:word|phrase)?|pwd|passwd)\b(\s*[=:]\s*)[^\r\n]*")   # a passphrase may contain spaces
_KV_SECRET_RE = re.compile(
    r"(?i)\b(secret|token|api[_-]?key|authorization|cookie|credential)\b(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|\S+)")
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")
_SESSION_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{43}(?![A-Za-z0-9_\-])")   # secrets.token_urlsafe(32)
_SCRYPT_RE = re.compile(r"scrypt\$[^\s\"']+")
_IL_ID_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")                      # Israeli ID / other 9-digit numbers
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{7}(?!\d)")
_SECRET_KEY_RE = re.compile(r"(?i)pass|pwd|(?:^|_)pw(?:_|$)|secret|token|authori[sz]ation|cookie|api[_-]?key|credential"
                            r"|session[_-]?id|bearer|(?:^|_)hash$")
_EMAIL_KEY_RE = re.compile(r"(?i)e-?mail")
_PII_KEY_RE = re.compile(r"(?i)phone|mobile|id_?number|national_?id")

RESERVED_KEYS = frozenset({"ts", "level", "logger", "event", "cid", "exc", "exc_type"})   # written by the formatter
# attributes every LogRecord has; anything else came from ``extra=``
_STD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}

_SALT = os.environ.get("LOG_SALT") or uuid.uuid4().hex


def pseudonymize(value: str | None) -> str | None:
    """Stable, non-reversible token for correlating one person's events without logging the address."""
    if not value or not isinstance(value, str):
        return None
    return hmac.new(_SALT.encode(), value.strip().lower().encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:12]


def _email_token(match: "re.Match[str] | str") -> str:
    addr = match if isinstance(match, str) else match.group(0)
    return f"[email:{pseudonymize(addr)}]"


_SCAN_LIMIT = 4 * MAX_FIELD          # characters examined by the redaction patterns for one value
_TRACE_SCAN_LIMIT = 4 * MAX_TRACE


def _window(text: str, limit: int, *, tail: bool = False) -> str:
    """Bound the work the patterns do. The cut is moved to a whitespace boundary so a secret or an address is never
    split into a fragment the patterns no longer recognise (``word=hunter2`` after cutting ``pass``)."""
    if len(text) <= limit:
        return text
    if tail:
        cut = text[-limit:]
        i = next((k for k, ch in enumerate(cut) if ch.isspace()), -1)
        return "…" + (cut[i:] if i >= 0 else "")
    cut = text[:limit]
    i = max((k for k, ch in enumerate(cut) if ch.isspace()), default=-1)
    return (cut[:i] if i >= 0 else "") + "…"


def redact_text(text: str, *, tail: bool = False, limit: int = _SCAN_LIMIT) -> str:
    text = _window(text, limit, tail=tail)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _KV_PASSWORD_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _SCRYPT_RE.sub(REDACTED, text)
    text = _SESSION_TOKEN_RE.sub(REDACTED, text)
    # Swap each address for a digit-free placeholder first, so the numeric patterns below can neither see an address
    # (digits in a local part) nor damage the 12-hex token that replaces it.
    tokens: list[str] = []

    def hold(m: "re.Match[str]") -> str:
        tokens.append(_email_token(m))
        return f"\x00{len(tokens) - 1}\x00"
    text = _EMAIL_RE.sub(hold, text)
    text = _PHONE_RE.sub("[phone]", text)
    text = _IL_ID_RE.sub("[id]", text)
    for i, tok in enumerate(tokens):
        text = text.replace(f"\x00{i}\x00", tok)
    return text


def _clip(s: str, limit: int = MAX_FIELD) -> str:
    return s if len(s) <= limit else s[:limit] + "…"


def _safe_str(x: Any) -> str:
    try:
        return str(x)
    except Exception:
        return "[unprintable]"


def clean_value(key: str, value: Any, _depth: int = 0) -> Any:
    """Make one field safe to write: redact by field name, then by content, recursively, bounded in size and depth."""
    key = str(key)
    if _SECRET_KEY_RE.search(key) or _PII_KEY_RE.search(key):
        return REDACTED
    if _EMAIL_KEY_RE.search(key) and isinstance(value, str):
        return _email_token(value) if value.strip() else value
    if isinstance(value, str):
        return _clip(redact_text(value))
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        if _depth >= MAX_DEPTH:
            return "[nested]"
        items = list(value.items())[:MAX_ITEMS]
        return {_clip(redact_text(_safe_str(k)), 100): clean_value(_safe_str(k), v, _depth + 1) for k, v in items}
    if isinstance(value, (list, tuple, set, frozenset)):
        if _depth >= MAX_DEPTH:
            return "[nested]"
        return [clean_value(key, v, _depth + 1) for v in list(value)[:MAX_ITEMS]]
    try:
        text = str(value)
    except Exception:                                          # an object whose __str__ raises must not break logging
        text = "[unprintable]"
    return _clip(redact_text(text))


_LINE_BREAKS = {"\u2028": "\\u2028", "\u2029": "\\u2029", "\x85": "\\u0085", "\x7f": "\\u007f", "\x1c": "\\u001c",
                "\x1d": "\\u001d", "\x1e": "\\u001e"}


def _escape_line_separators(s: str) -> str:
    for ch, rep in _LINE_BREAKS.items():
        s = s.replace(ch, rep)
    return s


# ------------------------------------------------------------------ formatters and handler
def _format_failed(record: logging.LogRecord) -> str:
    """Fixed line used when a record cannot be formatted. logging's own fallback (``Handler.handleError``) prints the raw
    message and arguments to stderr, which would bypass every redaction rule above."""
    return json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                       "level": "ERROR", "logger": str(getattr(record, "name", LOGGER_NAME))[:60],
                       "event": "log_format_failed", "cid": _cid.get()}, separators=(",", ":"))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            return self._format(record)
        except Exception:
            return _format_failed(record)

    def _format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": _clip(redact_text(_safe_str(record.getMessage()))),
            "cid": getattr(record, "cid", "-"),
        }
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and k != "cid" and not k.startswith("_"):
                doc[f"f_{k}" if k in RESERVED_KEYS else k] = clean_value(k, v)
        if record.exc_info and record.exc_info[0] is not None:
            doc["exc_type"] = record.exc_info[0].__name__
            doc["exc"] = redact_text(self.formatException(record.exc_info), tail=True, limit=_TRACE_SCAN_LIMIT)[-MAX_TRACE:]
        return _escape_line_separators(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), default=str))


class TextFormatter(logging.Formatter):
    """key=value lines for local development (LOG_FORMAT=text). Same redaction; one line per record."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            return self._format(record)
        except Exception:
            return "log_format_failed"

    def _format(self, record: logging.LogRecord) -> str:
        msg = _clip(redact_text(_safe_str(record.getMessage())))
        extras = " ".join(f"{k}={clean_value(k, v)!r}" for k, v in record.__dict__.items()
                          if k not in _STD_ATTRS and k != "cid" and not k.startswith("_"))
        out = f'{self.formatTime(record, "%H:%M:%S")} {record.levelname:<8} cid={getattr(record, "cid", "-")} {msg} {extras}'.rstrip()
        if record.exc_info and record.exc_info[0] is not None:
            out += " | " + redact_text(self.formatException(record.exc_info), tail=True, limit=_TRACE_SCAN_LIMIT)[-MAX_TRACE:]
        return _escape_line_separators(out.replace("\r", "\\r").replace("\n", "\\n"))


class ContextFilter(logging.Filter):
    """Stamps every record with the current session correlation id."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.cid = _cid.get()
        return True


class _StdoutHandler(logging.StreamHandler):
    """Resolves sys.stdout at emit time, so a replaced stdout (tests, supervisors) is honoured."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout


def _level(level: str | int | None) -> int:
    if isinstance(level, int):
        return level
    name = str(level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    value = logging.getLevelName(name)
    return value if isinstance(value, int) else logging.INFO       # a typo in LOG_LEVEL must not stop the app


def configure_logging(level: str | int | None = None, stream: IO[str] | None = None, fmt: str | None = None) -> logging.Logger:
    """Attach exactly one handler to the ``baror`` logger. Safe to call on every rerun and from several threads."""
    lvl = _level(level)
    fmt = fmt or os.environ.get("LOG_FORMAT", "json")
    fmt = fmt if fmt in ("json", "text") else "json"
    log = logging.getLogger(LOGGER_NAME)
    signature = (lvl, fmt, id(stream) if stream is not None else "stdout")
    with _lock:
        for h in list(log.handlers):
            if getattr(h, _MARKER, None) == signature:
                return log                                     # configured identically already: no-op
            if hasattr(h, _MARKER):
                log.removeHandler(h)                           # configured differently: replace, never stack
        h = logging.StreamHandler(stream) if stream is not None else _StdoutHandler()
        setattr(h, _MARKER, signature)
        h.addFilter(ContextFilter())
        h.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
        log.addHandler(h)
        log.setLevel(lvl)
        log.propagate = False                                  # the root logger must not print it a second time
        return log


def get_logger(suffix: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME)


# ------------------------------------------------------------------ correlation and incident ids
def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


def set_correlation_id(cid: str) -> None:
    _cid.set(cid)


def get_correlation_id() -> str:
    return _cid.get()


def new_incident_id() -> str:
    """Short support code shown to the user and written to the log next to the traceback. 8 hex = 32 bits."""
    return uuid.uuid4().hex[:8].upper()


# ------------------------------------------------------------------ audit-style events
_EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_.]{0,39}$")


def log_event(name: str, /, *, level: int = logging.INFO, **fields: Any) -> None:
    """Business / security event line, e.g. ``log_event("login_failed", email=email, reason="wrong_password")``.

    * ``name`` must look like ``snake_case`` (letters, digits, ``_`` or ``.``, up to 40); otherwise ``invalid_event``.
    * Any field named like an e-mail is replaced by its keyed hash; secret-looking fields are redacted (see module doc).
    * Fields that collide with LogRecord attributes or with the keys the formatter writes (``name``, ``message``,
      ``args``, ``ts``, ``level``, ``event``, ``cid``...) are stored as ``f_<name>``; they can never overwrite them.
    * Never raises, whatever the arguments.
    """
    try:
        if not isinstance(name, str) or not _EVENT_NAME_RE.match(name):
            fields, name = {"given": str(name)[:60]}, "invalid_event"
        extra = {"audit": True}
        for k, v in fields.items():
            k = str(k)
            if k.startswith("_") or k in _STD_ATTRS or k in RESERVED_KEYS or k == "audit":
                k = f"f_{k.lstrip('_')}"
            extra[k] = v
        logging.getLogger(AUDIT_LOGGER_NAME).log(level if isinstance(level, int) else logging.INFO, name, extra=extra)
    except Exception:                                          # logging must never break a user action
        try:
            sys.stderr.write("baror: event logging failed\n")
        except Exception:
            pass
