"""E-mailing the stored reports (smtplib is ALWAYS mocked: nothing here can send anything), the on/off switch and its
backlog rule, the mail settings, and scripts/generate_reports.py."""
import datetime as dt
import importlib
import io
import json
import smtplib
import ssl
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from baror import config, mailer, messages_he
from baror.domain.grading import grade_stored_value
from baror.storage import db, ops, repo, reports
from tests.v2base import V2Case

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
script = importlib.import_module("generate_reports")      # scripts/generate_reports.py

G = grade_stored_value
UTC = dt.timezone.utc
THU = dt.date(2026, 11, 5)
T0 = dt.datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
PASSWORD = "S3cret-smtp-pw"
MAIL = config.MailConfig(host="smtp.example.test", port=587, mode="starttls", sender="reports@example.test",
                         user="mailer-user", password=PASSWORD)
FAKE_PDF = b"%PDF-1.7 fake"
render = lambda data: FAKE_PDF      # noqa: E731


class MailCase(V2Case):
    def setUp(self):
        super().setUp()
        self.boss = self.staff("boss.rina@example.com", "chief", name="Boss")
        self.deputy = self.staff("deputy.dan@example.com", "chief", name="Deputy")
        self.gone = self.staff("gone.old@example.com", "chief", name="Gone")
        ops.deactivate_user(self.path, self.gone)
        self.ins = self.staff("ins.x@example.com", "instructor", name="Ins")
        p = self.platoon("A")
        for i in range(6):
            uid = self.cadet(f"m{i}@example.com", p, name=f"M{i}")
            repo.add_result(self.path, uid, test_date="2026-11-02", run_tenths=9000, pushups=30)

    def make(self, today=THU, now=T0):
        return reports.generate_due(self.path, today, grade=G, render=render, now=now)

    def on(self, now):
        reports.set_mail_enabled(self.path, self.boss, True, now=now)

    def deliveries(self):
        with db.reader(self.path) as c:
            return [tuple(r) for r in c.execute("SELECT user_id, sent_at IS NOT NULL, attempts, last_error "
                                                "FROM report_deliveries ORDER BY report_id, user_id")]

    def deliver(self, mail=MAIL, now=T0 + dt.timedelta(hours=5), **kw):
        """Run the sender with smtplib replaced; returns (counts, SMTP class mock, server mock)."""
        with mock.patch("baror.mailer.smtplib.SMTP") as smtp, mock.patch("baror.mailer.smtplib.SMTP_SSL"):
            server = smtp.return_value.__enter__.return_value
            counts = mailer.deliver_pending(self.path, mail, now=now, **kw)
        return counts, smtp, server


class DefaultOff(MailCase):
    def test_a_fresh_database_and_the_migration_leave_it_off_and_nothing_is_sent(self):
        self.assertEqual(reports.mail_state(self.path), {"enabled": False, "changed_at": None})
        self.make()
        counts, smtp, server = self.deliver()
        self.assertEqual((counts["enabled"], counts["sent"], counts["pending"]), (False, 0, 0))
        smtp.assert_not_called()
        server.send_message.assert_not_called()
        self.assertTrue(all(not d[1] for d in self.deliveries()))

    def test_off_still_stores_the_report_for_download(self):
        self.make()
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM reports").fetchone()[0], 1)

    def test_the_script_logs_only_that_auto_send_is_off(self):
        self.make()
        with self.assertLogs("baror.audit", level="INFO") as log:
            self.deliver()
        self.assertEqual([r.getMessage() for r in log.records], ["report_mail_off"])


class On(MailCase):
    def test_on_sends_once_to_every_active_chief_with_the_stored_pdf_attached(self):
        self.on(T0 - dt.timedelta(hours=1))
        self.make()
        counts, smtp, server = self.deliver()
        self.assertEqual((counts["sent"], counts["failed"], counts["pending"]), (2, 0, 2))
        sent = [c.args[0] for c in server.send_message.call_args_list]
        self.assertEqual(sorted(m["To"] for m in sent), ["boss.rina@example.com", "deputy.dan@example.com"])
        for m in sent:
            self.assertEqual(m["From"], MAIL.sender)
            self.assertEqual(m.get_body(("plain",)).get_content().strip(), messages_he.t(
                "report_mail_body_weekly", start="30/10/2026", end="05/11/2026"))
            att = list(m.iter_attachments())
            self.assertEqual(len(att), 1)
            self.assertEqual(att[0].get_content(), FAKE_PDF)
            self.assertEqual(att[0].get_content_type(), "application/pdf")
            self.assertEqual(att[0].get_filename(), "baror-weekly-2026-11-05.pdf")
            self.assertEqual(str(m["Subject"]), messages_he.t("report_mail_subject_weekly", start="30/10/2026", end="05/11/2026"))
        self.assertNotIn("gone.old@example.com", [m["To"] for m in sent], "a deactivated chief gets nothing")
        self.assertNotIn("ins.x@example.com", [m["To"] for m in sent])
        self.assertEqual(len(self.audit_rows(action="report.mail")), 2)

    def test_a_second_run_never_sends_the_same_report_to_the_same_person_again(self):
        self.on(T0 - dt.timedelta(hours=1))
        self.make()
        self.deliver()
        counts, smtp, server = self.deliver()
        self.assertEqual((counts["pending"], counts["sent"]), (0, 0))
        server.send_message.assert_not_called()
        self.assertEqual(self.make()[0]["id"], None, "and running the generation again creates no duplicate either")
        counts, _, server = self.deliver()
        server.send_message.assert_not_called()

    def test_turning_it_on_does_not_send_the_backlog_only_later_reports(self):
        self.make(now=T0)                                          # generated while OFF
        self.on(T0 + dt.timedelta(hours=1))
        counts, smtp, server = self.deliver(now=T0 + dt.timedelta(hours=2))
        self.assertEqual((counts["pending"], counts["sent"]), (0, 0))
        server.send_message.assert_not_called()
        later = dt.datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
        self.make(dt.date(2026, 11, 12), later)                    # generated after the switch: sent
        counts, _, server = self.deliver(now=later + dt.timedelta(hours=1))
        self.assertEqual(counts["sent"], 2)
        self.assertEqual({str(c.args[0]["To"]) for c in server.send_message.call_args_list},
                         {"boss.rina@example.com", "deputy.dan@example.com"})

    def test_off_on_off_on_moves_the_cutoff_each_time(self):
        self.on(T0 - dt.timedelta(hours=3))
        reports.set_mail_enabled(self.path, self.boss, False, now=T0 - dt.timedelta(hours=2))
        self.make(now=T0)                                          # generated while off
        self.on(T0 + dt.timedelta(hours=1))
        counts, _, server = self.deliver(now=T0 + dt.timedelta(hours=2))
        self.assertEqual(counts["pending"], 0)
        server.send_message.assert_not_called()

    def test_the_switch_is_read_from_the_database_on_every_run(self):
        self.on(T0 - dt.timedelta(hours=1))
        self.make()
        reports.set_mail_enabled(self.path, self.deputy, False, now=T0 + dt.timedelta(hours=1))
        _, _, server = self.deliver()
        server.send_message.assert_not_called()
        reports.set_mail_enabled(self.path, self.deputy, True, now=T0 + dt.timedelta(hours=2))
        self.assertEqual(self.deliver(now=T0 + dt.timedelta(hours=3))[0]["pending"], 0, "the report is older than the new cut-off")

    def test_reports_older_than_the_retry_window_are_not_retried(self):
        self.on(T0 - dt.timedelta(hours=1))
        self.make()
        counts, _, _ = self.deliver(now=T0 + dt.timedelta(days=reports.RETRY_DAYS + 1))
        self.assertEqual(counts["pending"], 0)

    def test_dry_run_sends_nothing_and_reports_counts_and_the_state(self):
        self.on(T0 - dt.timedelta(hours=1))
        self.make()
        counts, smtp, server = self.deliver(dry_run=True)
        self.assertEqual((counts["enabled"], counts["pending"], counts["sent"]), (True, 2, 0))
        smtp.assert_not_called()
        self.assertTrue(all(not d[1] for d in self.deliveries()))


class Failures(MailCase):
    def setUp(self):
        super().setUp()
        self.on(T0 - dt.timedelta(hours=1))
        self.make()

    def test_a_failure_keeps_the_report_and_records_only_a_code_then_only_the_unsent_are_retried(self):
        calls = []

        def flaky(msg):
            calls.append(msg["To"])
            if msg["To"] == "boss.rina@example.com":
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials for boss.rina@example.com")

        with mock.patch("baror.mailer.smtplib.SMTP") as smtp, self.assertLogs("baror.audit", level="INFO") as log:
            smtp.return_value.__enter__.return_value.send_message.side_effect = flaky
            counts = mailer.deliver_pending(self.path, MAIL, now=T0 + dt.timedelta(hours=1))
        self.assertEqual((counts["sent"], counts["failed"]), (1, 1))
        rows = {d[0]: d for d in self.deliveries()}
        self.assertEqual(rows[self.boss][1:], (0, 1, "smtp_auth"))
        self.assertEqual(rows[self.deputy][1:], (1, 1, None))
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM reports").fetchone()[0], 1, "the report is still stored")
        shown = "\n".join(str(r.__dict__) + r.getMessage() for r in log.records)
        for secret in ("boss.rina", "deputy.dan", "@example.com", PASSWORD, "bad credentials"):
            self.assertNotIn(secret, shown)
        # second run: only the failed one is retried
        with mock.patch("baror.mailer.smtplib.SMTP") as smtp:
            server = smtp.return_value.__enter__.return_value
            counts = mailer.deliver_pending(self.path, MAIL, now=T0 + dt.timedelta(hours=2))
        self.assertEqual((counts["pending"], counts["sent"]), (1, 1))
        self.assertEqual([c.args[0]["To"] for c in server.send_message.call_args_list], ["boss.rina@example.com"])
        self.assertEqual(self.deliveries()[0][1], 1)

    def test_error_codes_never_carry_exception_text(self):
        cases = {smtplib.SMTPAuthenticationError(535, b"x"): "smtp_auth", smtplib.SMTPRecipientsRefused({"a@b.co": (550, b"no")}):
                 "smtp_refused", ssl.SSLCertVerificationError("hostname mismatch for smtp.example.test"): "tls",
                 smtplib.SMTPServerDisconnected("gone"): "smtp_error", TimeoutError("slow"): "timeout",
                 ConnectionRefusedError("refused 10.0.0.1"): "network", KeyError("boom"): "unknown"}
        for exc, code in cases.items():
            with mock.patch("baror.mailer.smtplib.SMTP") as smtp:
                smtp.return_value.__enter__.return_value.send_message.side_effect = exc
                with self.assertRaises(mailer.MailError) as ctx:
                    mailer.send(MAIL, mailer.build_message(MAIL, "a@example.com", "weekly", dt.date(2026, 10, 30), THU, b"x"))
            self.assertEqual((ctx.exception.code, str(ctx.exception)), (code, code))

    def test_a_connection_error_is_recorded_and_the_script_goes_on(self):
        with mock.patch("baror.mailer.smtplib.SMTP", side_effect=ConnectionRefusedError("10.1.2.3")):
            counts = mailer.deliver_pending(self.path, MAIL, now=T0 + dt.timedelta(hours=1))
        self.assertEqual((counts["sent"], counts["failed"]), (0, 2))
        self.assertEqual({d[3] for d in self.deliveries()}, {"network"})

    def test_the_code_column_refuses_free_text(self):
        with self.assertRaises(Exception):
            with db.transaction(self.path) as c:
                c.execute("UPDATE report_deliveries SET last_error='boss.rina@example.com'")
        reports.mark_failed(self.path, 1, self.boss, "Some Text With Spaces")
        self.assertEqual(self.deliveries()[0][3], "unknown")


class NotConfiguredAndTls(MailCase):
    def test_not_configured_logs_without_an_address_sends_nothing_and_keeps_the_deliveries(self):
        self.on(T0 - dt.timedelta(hours=1))
        self.make()
        with self.assertLogs("baror.audit", level="INFO") as log, mock.patch("baror.mailer.smtplib.SMTP") as smtp:
            counts = mailer.deliver_pending(self.path, None, now=T0 + dt.timedelta(hours=1))
        smtp.assert_not_called()
        self.assertEqual((counts["configured"], counts["sent"], counts["pending"]), (False, 0, 2))
        self.assertEqual([r.getMessage() for r in log.records], ["report_mail_not_configured"])
        self.assertNotIn("@", "".join(str(r.__dict__) for r in log.records))
        self.assertTrue(all(not d[1] for d in self.deliveries()))

    def test_starttls_is_required_and_verifies_the_certificate_and_host_name(self):
        with mock.patch("baror.mailer.smtplib.SMTP") as smtp:
            server = smtp.return_value.__enter__.return_value
            mailer.send(MAIL, mailer.build_message(MAIL, "a@example.com", "weekly", dt.date(2026, 10, 30), THU, b"x"))
        ctx = server.starttls.call_args.kwargs["context"]
        self.assertEqual((ctx.verify_mode, ctx.check_hostname), (ssl.CERT_REQUIRED, True))
        names = [c[0] for c in server.method_calls]
        self.assertLess(names.index("starttls"), names.index("login"), "no login before TLS")
        server.login.assert_called_once_with("mailer-user", PASSWORD)
        self.assertEqual(smtp.call_args.kwargs["timeout"], mailer.TIMEOUT_SECONDS)

    def test_ssl_mode_uses_a_verifying_context(self):
        ssl_mail = config.MailConfig(host="smtp.example.test", port=465, mode="ssl", sender="r@example.test",
                                     user="u", password="p")
        with mock.patch("baror.mailer.smtplib.SMTP_SSL") as smtp_ssl, mock.patch("baror.mailer.smtplib.SMTP") as plain:
            mailer.send(ssl_mail, mailer.build_message(ssl_mail, "a@example.com", "monthly", dt.date(2026, 11, 1),
                                                       dt.date(2026, 11, 30), b"x"))
        plain.assert_not_called()
        ctx = smtp_ssl.call_args.kwargs["context"]
        self.assertEqual((ctx.verify_mode, ctx.check_hostname), (ssl.CERT_REQUIRED, True))

    def test_header_injection_is_refused(self):
        for bad in ("a@example.com\nBcc: x@example.com", "a@example.com\r\nSubject: x", "a@example.com, b@example.com",
                    "Name <a@example.com>", "a b@example.com", ""):
            with self.assertRaises(mailer.MailError) as ctx:
                mailer.build_message(MAIL, bad, "weekly", dt.date(2026, 10, 30), THU, b"x")
            self.assertEqual(ctx.exception.code, "invalid_message", repr(bad))

    def test_a_monthly_message_names_the_month_and_the_data_until_date(self):
        m = mailer.build_message(MAIL, "a@example.com", "monthly", dt.date(2026, 10, 1), dt.date(2026, 10, 29), b"x")
        self.assertIn("אוקטובר 2026", str(m["Subject"]))
        self.assertIn("29/10/2026", m.get_body(("plain",)).get_content())
        self.assertEqual(next(m.iter_attachments()).get_filename(), "baror-monthly-2026-10-29.pdf")
        self.assertTrue(next(m.iter_attachments()).get_filename().isascii())


class Settings(unittest.TestCase):
    OK = {"BAROR_SMTP_HOST": "smtp.example.test", "BAROR_SMTP_USER": "u", "BAROR_SMTP_PASSWORD": PASSWORD,
          "BAROR_SMTP_FROM": "reports@example.test"}

    def test_unset_means_not_configured(self):
        self.assertIsNone(config.mail_from_env({}))
        self.assertIsNone(config.mail_from_env({"BAROR_SMTP_HOST": "  "}))

    def test_defaults_ports_and_mode(self):
        self.assertEqual((config.mail_from_env(self.OK).mode, config.mail_from_env(self.OK).port), ("starttls", 587))
        c = config.mail_from_env({**self.OK, "BAROR_SMTP_MODE": "SSL"})
        self.assertEqual((c.mode, c.port), ("ssl", 465))

    def test_plain_mode_and_unauthenticated_remote_hosts_are_refused(self):
        for bad in ({"BAROR_SMTP_MODE": "plain"}, {"BAROR_SMTP_MODE": "none"}, {"BAROR_SMTP_USER": ""}, {"BAROR_SMTP_PASSWORD": ""},
                    {"BAROR_SMTP_FROM": ""}, {"BAROR_SMTP_FROM": "a@b.co\nBcc: x@y.z"}, {"BAROR_SMTP_PORT": "x"},
                    {"BAROR_SMTP_PORT": "0"}, {"BAROR_SMTP_HOST": "smtp.example.test\nx"},
                    {"BAROR_SMTP_PASSWORD": "pw\nRCPT TO:<x@y.z>"}):
            with self.assertRaises(config.ConfigError, msg=str(bad)):
                config.mail_from_env({**self.OK, **bad})

    def test_a_local_relay_needs_no_login_but_still_tls(self):
        c = config.mail_from_env({"BAROR_SMTP_HOST": "localhost", "BAROR_SMTP_FROM": "r@example.test"})
        self.assertEqual((c.user, c.mode), (None, "starttls"))

    def test_the_password_is_not_in_the_repr_or_in_an_error_detail(self):
        c = config.mail_from_env(self.OK)
        self.assertNotIn(PASSWORD, repr(c))
        with self.assertRaises(config.ConfigError) as ctx:
            config.mail_from_env({**self.OK, "BAROR_SMTP_MODE": "plain"})
        self.assertNotIn(PASSWORD, str(ctx.exception.detail) + repr(ctx.exception))

    def test_the_mail_variables_are_not_part_of_the_app_cache_key(self):
        self.assertFalse([k for k in config.ENV_KEYS if "SMTP" in k])


class Script(MailCase):
    def run_script(self, *args, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", env or {}, clear=False), redirect_stdout(out), redirect_stderr(err), \
                mock.patch("baror.mailer.smtplib.SMTP") as smtp:
            code = script.main(["--db", self.path, *args])
        return code, out.getvalue(), err.getvalue(), smtp

    def env_off(self):
        return {k: "" for k in ("BAROR_SMTP_HOST", "BAROR_SMTP_USER", "BAROR_SMTP_PASSWORD", "BAROR_SMTP_FROM")}

    def count(self):
        with db.reader(self.path) as c:
            return c.execute("SELECT count(*) FROM reports").fetchone()[0]

    def test_nothing_due_exits_zero_and_stores_nothing(self):
        code, out, _, _ = self.run_script("--date", "2026-11-06", env=self.env_off())          # a Friday
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["due"], [])
        self.assertEqual(self.count(), 0)

    def test_thursday_stores_a_real_pdf_and_a_rerun_the_same_day_does_not_duplicate(self):
        code, out, _, _ = self.run_script("--date", "2026-11-05", env=self.env_off())
        self.assertEqual((code, json.loads(out)["created"]), (0, 1))
        code, out, _, _ = self.run_script("--date", "2026-11-05", env=self.env_off())
        self.assertEqual((code, json.loads(out)["created"], json.loads(out)["existing"]), (0, 0, 1))
        self.assertEqual(self.count(), 1)
        with db.reader(self.path) as c:
            self.assertTrue(bytes(c.execute("SELECT pdf FROM reports").fetchone()[0]).startswith(b"%PDF-1.7"))

    def test_mail_not_configured_is_exit_zero(self):
        reports.set_mail_enabled(self.path, self.boss, True, now=T0 - dt.timedelta(days=1))
        code, out, err, smtp = self.run_script("--date", "2026-11-05", env=self.env_off())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["mail"]["configured"], False)
        smtp.assert_not_called()

    def test_switch_on_and_configured_sends_through_the_mock_and_honours_the_database_value_each_run(self):
        env = {"BAROR_SMTP_HOST": "smtp.example.test", "BAROR_SMTP_USER": "u", "BAROR_SMTP_PASSWORD": "p",
               "BAROR_SMTP_FROM": "r@example.test"}
        with mock.patch("baror.mailer.smtplib.SMTP") as smtp:
            server = smtp.return_value.__enter__.return_value
            off = script.run(self.path, dt.date(2026, 11, 5), env=env, now=T0)                  # OFF: nothing sent
            self.assertEqual((off["mail"]["enabled"], off["mail"]["sent"]), (False, 0))
            smtp.assert_not_called()
            reports.set_mail_enabled(self.path, self.boss, True, now=T0 + dt.timedelta(hours=1))
            again = script.run(self.path, dt.date(2026, 11, 5), env=env, now=T0 + dt.timedelta(hours=2))
            self.assertEqual((again["mail"]["enabled"], again["mail"]["sent"], again["created"]), (True, 0, 0),
                             "the report of the OFF period is not sent when the switch is turned on")
            later = dt.datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
            on = script.run(self.path, dt.date(2026, 11, 12), env=env, now=later)
            self.assertEqual((on["mail"]["sent"], on["created"]), (2, 1))
            self.assertEqual(server.send_message.call_count, 2)
            reports.set_mail_enabled(self.path, self.deputy, False, now=later + dt.timedelta(hours=1))
            script.run(self.path, dt.date(2026, 11, 19), env=env, now=later + dt.timedelta(days=7))
            self.assertEqual(server.send_message.call_count, 2, "switched off again: the DB value is read on every run")

    def test_dry_run_writes_and_sends_nothing_and_reports_the_state(self):
        code, out, _, smtp = self.run_script("--date", "2026-04-30", "--dry-run", env=self.env_off())
        data = json.loads(out)
        self.assertEqual((code, self.count()), (0, 0))
        self.assertEqual([d["kind"] for d in data["due"]], ["weekly", "monthly"])
        self.assertEqual((data["mail"]["enabled"], data["mail"]["sent"]), (False, 0))
        smtp.assert_not_called()

    def test_since_catches_up_missed_days_and_is_idempotent(self):
        code, out, _, _ = self.run_script("--date", "2026-11-12", "--since", "2026-10-29", env=self.env_off())
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(sorted((d["kind"], d["end"]) for d in data["due"]),
                         [("monthly", "2026-10-29"), ("weekly", "2026-10-29"), ("weekly", "2026-11-05"), ("weekly", "2026-11-12")])
        self.assertEqual(self.count(), 4)
        self.run_script("--date", "2026-11-12", "--since", "2026-10-29", env=self.env_off())
        self.assertEqual(self.count(), 4)

    def test_bad_arguments_and_an_old_schema_exit_one(self):
        self.assertEqual(self.run_script("--date", "2026-11-05", "--since", "2026-11-06")[0], 1)
        old = str(Path(self.dir) / "old.db")
        db.init_db(old, target_version=7)
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(script.main(["--db", old, "--date", "2026-11-05"]), 1)

    def test_invalid_mail_settings_exit_two_only_when_the_switch_is_on(self):
        env = {**self.env_off(), "BAROR_SMTP_HOST": "smtp.example.test"}                    # no login: invalid
        self.assertEqual(self.run_script("--date", "2026-11-05", env=env)[0], 0)
        reports.set_mail_enabled(self.path, self.boss, True)
        code, _, err, _ = self.run_script("--date", "2026-11-12", env=env)
        self.assertEqual(code, 2)
        self.assertNotIn("example", err)


if __name__ == "__main__":
    unittest.main()
