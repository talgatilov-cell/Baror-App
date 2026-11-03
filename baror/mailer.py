"""Sending the stored period reports by e-mail to the active chief accounts. Standard library only (smtplib, ssl,
email.message); never imports streamlit; lives outside ``baror.domain``.

* OFF BY DEFAULT: sends only while the chief's switch (table ``report_settings``) is on, and then only reports generated
  after it was turned on (no backlog).
* TLS IS REQUIRED: ``starttls`` or ``ssl`` with the default context (certificate AND host name verified). There is no plain
  mode (``baror.config.mail_from_env`` refuses it), and a remote server needs a login.
* NEVER ONCE MORE: a delivery (one report, one chief account) is marked sent right after the server accepts it, and only
  unsent deliveries of the last ``RETRY_DAYS`` days are tried again. A crash between "accepted" and "marked" can repeat one
  mail; that is the price of not losing one. Do not run two copies of the script at the same time.
* A FAILURE NEVER TOUCHES THE REPORT: it stays stored and downloadable; the delivery row keeps a short code
  (``smtp_auth``, ``tls``, ``network`` ...), never exception text, which may contain addresses.
* LOGS carry counts and codes only: no address, no password, no exception message.
* HEADER SAFETY: the recipient comes from the database but is validated again (one plain address, no CR/LF) and the
  message is built with ``email.message.EmailMessage``, which refuses line breaks in headers. The attachment name is
  ASCII (``baror-weekly-2026-11-05.pdf``).
"""
from __future__ import annotations

import datetime as dt
import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import Any

from baror import config
from baror.logging_setup import log_event
from baror.messages_he import month_label, t
from baror.storage import reports

__all__ = ["MailError", "build_message", "send", "deliver_pending", "attachment_name"]

TIMEOUT_SECONDS = 30


class MailError(Exception):
    """A send failed. ``code`` is a short lower-case code; the message is the code, so it is safe to log."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def attachment_name(kind: str, end: dt.date) -> str:
    return f"baror-{'weekly' if kind == 'weekly' else 'monthly'}-{end.isoformat()}.pdf"


def build_message(mail: config.MailConfig, to: str, kind: str, start: dt.date, end: dt.date, pdf: bytes) -> EmailMessage:
    """Plain-text Hebrew message with the PDF attached. MailError('invalid_message') for an unsafe address."""
    if not config.valid_address(to) or not config.valid_address(mail.sender):
        raise MailError("invalid_message")
    if kind == "weekly":
        subject = t("report_mail_subject_weekly", start=start.strftime("%d/%m/%Y"), end=end.strftime("%d/%m/%Y"))
        body = t("report_mail_body_weekly", start=start.strftime("%d/%m/%Y"), end=end.strftime("%d/%m/%Y"))
    else:
        subject = t("report_mail_subject_monthly", label=month_label(start))
        body = t("report_mail_body_monthly", label=month_label(start), end=end.strftime("%d/%m/%Y"))
    msg = EmailMessage()
    try:
        msg["From"], msg["To"], msg["Subject"] = mail.sender, to, subject
    except ValueError:
        raise MailError("invalid_message") from None
    msg.set_content(body)
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=attachment_name(kind, end))
    return msg


def _code(exc: BaseException) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "smtp_auth"
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
        return "smtp_refused"
    if isinstance(exc, (ssl.SSLError, ssl.CertificateError)):
        return "tls"
    if isinstance(exc, smtplib.SMTPException):
        return "smtp_error"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, OSError):
        return "network"
    return "unknown"


def send(mail: config.MailConfig, msg: EmailMessage) -> None:
    """Deliver one message over TLS. Raises ``MailError`` with a short code on any failure."""
    context = ssl.create_default_context()            # verifies the certificate chain and the host name
    try:
        if mail.mode == "ssl":
            with smtplib.SMTP_SSL(mail.host, mail.port, timeout=TIMEOUT_SECONDS, context=context) as server:
                if mail.user and mail.password:
                    server.login(mail.user, mail.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(mail.host, mail.port, timeout=TIMEOUT_SECONDS) as server:
                server.starttls(context=context)       # refuses to continue in clear text if the server cannot
                if mail.user and mail.password:
                    server.login(mail.user, mail.password)
                server.send_message(msg)
    except MailError:
        raise
    except Exception as exc:                          # noqa: BLE001 - mapped to a code; the text is dropped on purpose
        raise MailError(_code(exc)) from None


def deliver_pending(db_path, mail: config.MailConfig | None, *, now: dt.datetime | None = None,
                    dry_run: bool = False) -> dict[str, Any]:
    """Try every unsent delivery to an active chief account. -> counts only:
    {'enabled','pending','sent','failed','configured'}. Switch OFF (the default, set by the chief on the screen): nothing is
    sent. ``mail=None`` (not configured): nothing is sent, the deliveries stay unsent and a later run with mail configured
    sends them (within the retry window, and never reports from before the switch was turned on). ``dry_run`` sends nothing."""
    enabled = reports.mail_state(db_path)["enabled"]      # read from the database on every run
    todo = reports.pending_deliveries(db_path, now=now)   # [] while the switch is off
    out = {"enabled": enabled, "pending": len(todo), "sent": 0, "failed": 0, "configured": mail is not None}
    if not enabled:
        log_event("report_mail_off")
        return out
    if mail is None:
        log_event("report_mail_not_configured", pending=len(todo))
        return out
    if dry_run:
        return out
    pdfs: dict[int, bytes] = {}
    for d in todo:
        start, end = dt.date.fromisoformat(d["start"]), dt.date.fromisoformat(d["end"])
        try:
            if d["report_id"] not in pdfs:
                pdfs[d["report_id"]] = reports.report_pdf(db_path, d["report_id"])
            send(mail, build_message(mail, d["email"], d["kind"], start, end, pdfs[d["report_id"]]))
        except MailError as exc:
            reports.mark_failed(db_path, d["report_id"], d["user_id"], exc.code)
            out["failed"] += 1
            log_event("report_mail_failed", report_id=d["report_id"], code=exc.code)
            continue
        reports.mark_sent(db_path, d["report_id"], d["user_id"], now=now)
        out["sent"] += 1
        log_event("report_mail_sent", report_id=d["report_id"])
    return out
