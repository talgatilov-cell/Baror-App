"""The chief screen's 'דוחות' section under AppTest: the switch (database wins, audited), the list of stored reports, one
audited read per prepared download and none per rerun."""
import datetime as dt
import os
import unittest
from unittest import mock

from baror.domain.grading import grade_stored_value
from baror.messages_he import t
from baror.storage import reports
from tests.test_app import btn, texts
from tests.test_app_staff import StaffBase, audit_rows

UTC = dt.timezone.utc


class Section(StaffBase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"BAROR_SMTP_HOST": ""})
        env.start()
        self.addCleanup(env.stop)
        self.fill(self.pa, 6, "rp")

    def make(self, day, hour=8):
        reports.generate_due(self.path, day, grade=grade_stored_value, render=lambda d: b"%PDF-1.7 x",
                             now=dt.datetime(day.year, day.month, day.day, hour, tzinfo=UTC))

    def chief(self):
        return self.sign_in("boss@example.com")

    def test_empty_list_and_switch_off_by_default(self):
        at = self.chief()
        self.assertIn(t("report_section_title"), [x.value for x in at.subheader])
        self.assertIn(t("report_list_empty"), texts(at))
        self.assertFalse(at.toggle(key="report_mail_toggle").value)
        self.assertIn(t("report_mail_off_note"), texts(at))

    def test_reports_are_listed_newest_first_and_a_download_is_one_audited_read(self):
        self.make(dt.date(2026, 11, 5))
        self.make(dt.date(2026, 11, 12))
        at = self.chief()
        keys = [b.key for b in at.button if b.key.startswith("report_prep_")]
        self.assertEqual(len(keys), 2)
        self.assertEqual(audit_rows(self.path, "chief.reports")[-1][3], "ok")
        reads_before = len(audit_rows(self.path, "chief.reports"))
        at.run()
        at.run()
        self.assertEqual(len(audit_rows(self.path, "chief.reports")), reads_before, "reruns do not read the list again")
        self.assertEqual(audit_rows(self.path, "chief.report"), [])
        at.button(key=keys[0]).click().run()
        self.assertEqual(len(audit_rows(self.path, "chief.report")), 1)
        self.assertTrue(at.get("download_button"))
        at.run()
        self.assertEqual(len(audit_rows(self.path, "chief.report")), 1, "the download itself and reruns add no row")

    def test_the_first_row_is_the_newest_period(self):
        self.make(dt.date(2026, 11, 5))
        self.make(dt.date(2026, 11, 12))
        at = self.chief()
        titles = [str(m.value) for m in at.markdown if "עד" in str(m.value) and "**" in str(m.value)]
        self.assertIn("12/11/2026", titles[0])

    def test_switching_on_is_stored_audited_and_warns_when_the_mail_server_is_not_configured(self):
        at = self.chief()
        at.toggle(key="report_mail_toggle").set_value(True).run()
        self.assertTrue(reports.mail_state(self.path)["enabled"])
        self.assertEqual([r[:2] for r in audit_rows(self.path, "chief.mail_on")], [(self.boss, "chief.mail_on")])
        self.assertIn(t("report_mail_not_configured"), [w.value for w in at.warning])
        self.assertTrue(at.toggle(key="report_mail_toggle").value)

    def test_the_database_value_wins_over_the_browser_state(self):
        at = self.chief()
        reports.set_mail_enabled(self.path, self.boss, True)       # changed elsewhere (another chief, another tab)
        at.run()
        self.assertTrue(at.toggle(key="report_mail_toggle").value)
        reports.set_mail_enabled(self.path, self.boss, False)
        at.run()
        self.assertFalse(at.toggle(key="report_mail_toggle").value)

    def test_no_warning_when_mail_is_configured(self):
        env = {"BAROR_SMTP_HOST": "smtp.example.test", "BAROR_SMTP_USER": "u", "BAROR_SMTP_PASSWORD": "p",
               "BAROR_SMTP_FROM": "r@example.test"}
        with mock.patch.dict(os.environ, env):
            reports.set_mail_enabled(self.path, self.boss, True)
            at = self.chief()
        self.assertEqual([w.value for w in at.warning], [])

    def test_a_demoted_chief_cannot_flip_the_switch_through_a_stale_page(self):
        at = self.chief()
        with __import__("baror.storage.db", fromlist=["db"]).transaction(self.path) as c:
            c.execute("UPDATE users SET role='instructor' WHERE id=?", (self.boss,))
        at.toggle(key="report_mail_toggle").set_value(True).run()
        self.assertFalse(reports.mail_state(self.path)["enabled"])


if __name__ == "__main__":
    unittest.main()
