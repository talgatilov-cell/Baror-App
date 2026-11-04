"""The chief's decision on a cadet's accommodation REQUEST on screen (approve / reject), kept apart from the DELETION
requests: wording, the cadet's own tab, the opened cadet, audit rows, and that nobody else sees the buttons.

AppTest cannot draw a dialog, so the reject dialog's body is drawn inline in the run that opens it and in the run that
presses its button (what a fragment rerun does), as tests/test_accommodation_add_flow.py does for the add dialog."""
import datetime as dt
import unittest
import zoneinfo
from unittest import mock

import streamlit as st

from baror import config
from baror.messages_he import t
from baror.storage import accommodations, db
from baror.ui import accom
from tests.test_app import texts
from tests.test_app_staff import StaffBase, audit_rows, md

_REJECT_BODY = accom.reject_request_dialog.__wrapped__
_FLAG = "_test_reject_open"


def _inline_reject(cfg, actor, rec_id, on_done=None):
    st.session_state[_FLAG] = (cfg, actor, rec_id, on_done)
    _REJECT_BODY(cfg, actor, rec_id, on_done)


def _redraw_take_notice(real):
    def run():
        held = st.session_state.pop(_FLAG, None)
        if held:
            _REJECT_BODY(*held)
        real()
    return run


class Base(StaffBase):
    def setUp(self):
        super().setUp()
        self.today = dt.datetime.now(zoneinfo.ZoneInfo("Asia/Jerusalem")).date()
        iso = lambda d: d.isoformat()
        # a cadet's own entry (pending) and an instructor's (approved), both for self.ca
        self.req = accommodations.add(self.path, self.ca, self.ca, kind="exemption", scope="run",
                                      start_date=iso(self.today - dt.timedelta(days=1)),
                                      end_date=iso(self.today + dt.timedelta(days=20)), note="Ankle sprain")
        self.rec_ins = accommodations.add(self.path, self.ins, self.ca, kind="relief", scope="pushups",
                                          start_date=iso(self.today - dt.timedelta(days=1)),
                                          end_date=iso(self.today + dt.timedelta(days=20)))

    def cfg(self):
        return config.from_env({"DB_PATH": self.path})

    def approval(self, rid=None):
        rid = rid or self.req
        return next(r["approval"] for r in accommodations.list_own(self.path, self.ca) if r["id"] == rid)

    def chief_card(self, name):
        """The chief on the requests page with one card open (a card opens by itself when it is the only kind waiting;
        otherwise the cards are collapsed until pressed)."""
        return self.chief_requests(card=name)

    def instructor_card(self):
        """The instructor signed in with Alef-A's card open (a card shows the name only until the name is pressed)."""
        at = self.sign_in("ins@example.com")
        at.button(key=f"ins_open_{self.ca}").click()
        return at.run()

    def cadet_tab(self, email="ca@example.com"):
        at = self.go(self.sign_in(email), "more")
        at.segmented_control(key="more_tab").set_value("accommodations")
        return at.run()


class Wording(unittest.TestCase):
    def test_the_two_pending_states_use_different_words(self):
        self.assertNotEqual(t("acc_pending_chief"), t("acc_approval_pending"))
        self.assertNotEqual(t("chief_requests_title"), t("chief_pending_title"))
        self.assertNotEqual(t("btn_reject_request"), t("btn_reject_delete"))
        self.assertNotEqual(t("acc_request_rejected"), t("acc_rejected"))
        self.assertIn("מחיק", t("acc_pending_chief"))
        self.assertIn("מחיק", t("chief_pending_title"))
        self.assertIn("מחיק", t("btn_reject_delete"))
        self.assertIn("מחיק", t("acc_requested_by_label"))
        self.assertNotIn("מחיק", t("btn_approve_request") + t("btn_reject_request") + t("chief_requests_title"))

    def test_no_status_badge_raises_for_any_status(self):
        today = dt.date(2026, 11, 3)
        for approval in ("pending", "approved", "rejected"):
            for start, end in (("2026-10-01", "2026-12-01"), ("2026-12-01", "2027-01-01"), ("2026-09-01", "2026-10-01")):
                rec = {"kind": "relief", "scope": "run", "start_date": start, "end_date": end, "approval": approval,
                       "source": "cadet"}
                self.assertIn("-badge[", accom.status_badge(rec, today), (approval, start))
        words = {a: accom.status_badge({"start_date": "2026-10-01", "end_date": "2026-12-01", "approval": a}, today)
                 for a in ("pending", "rejected")}
        self.assertIn(t("acc_approval_pending"), words["pending"])
        self.assertIn(t("acc_approval_rejected"), words["rejected"])

    def test_only_an_approved_record_counts_as_active(self):
        today = dt.date(2026, 11, 3)
        rec = {"start_date": "2026-10-01", "end_date": "2026-12-01"}
        self.assertEqual(accom.active_count([dict(rec, approval=a) for a in ("pending", "rejected", "approved")], today), 1)


class RequestList(Base):
    def test_a_lone_waiting_request_opens_by_itself_with_its_own_title(self):
        at = self.chief_requests()                                              # only the cadet's request waits
        self.assertEqual(at.session_state["_req_open"], "approve")
        self.assertIn(f"req_ok_{self.req}", [b.key for b in at.button])
        self.assertIn("**" + t("req_acc_card_title") + "**", [str(m.value) for m in at.markdown])

    def test_the_chief_sees_the_cadets_request_labelled_as_the_cadets_with_two_decision_buttons(self):
        at = self.chief_card("approve")
        self.assertFalse(at.exception)
        body = md(at)
        self.assertIn(t("chief_requests_one"), body)
        self.assertIn(t("acc_source_cadet"), body)
        self.assertIn(t("acc_approval_pending"), body)
        self.assertIn("Alef-A", [str(x.value) for x in at.text])
        self.assertIn("Ankle sprain", [str(x.value) for x in at.text])
        by_key = {b.key: b.label for b in at.button}
        self.assertEqual(by_key[f"req_ok_{self.req}"], t("btn_approve_request"))
        self.assertEqual(by_key[f"req_no_{self.req}"], t("btn_reject_request"))
        self.assertNotIn(f"req_ok_{self.rec_ins}", by_key, "an instructor's record is approved at once and never listed")

    def test_the_approval_card_comes_before_the_deletion_card_and_both_start_closed(self):
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_ins)         # two kinds wait: nothing opens by itself
        at = self.chief_requests()
        keys = [b.key for b in at.button if b.key.startswith("reqcard_btn_")]
        self.assertLess(keys.index("reqcard_btn_approve"), keys.index("reqcard_btn_delete"))
        self.assertFalse([b.key for b in at.button if b.key.startswith(("req_ok_", "pend_ok_"))])

    def test_no_request_is_a_designed_empty_state(self):
        accommodations.approve_request(self.path, self.boss, self.req)
        self.assertIn(t("chief_requests_none"), texts(self.chief_card("approve")))

    def test_an_instructor_and_a_cadet_do_not_see_the_request_section_or_any_decision_button(self):
        at = self.instructor_card()
        # the section HEADING: the word "בקשות" is also part of other copy ("עוד לא נשלחו בקשות")
        self.assertNotIn(t("req_section_title"), [x.value for x in at.subheader])
        keys = [b.key for b in at.button]
        self.assertFalse([k for k in keys if k.startswith(("req_", "dec_", "rej_"))])
        self.assertIn(t("acc_approval_pending"), md(at), "the instructor sees the state, not the buttons")
        cadet = self.cadet_tab()
        self.assertFalse([b.key for b in cadet.button if b.key.startswith(("req_", "dec_", "rej_"))])
        self.assertIsNotNone(accom.apply_approve_request(self.cfg(), self.ins, self.req), "storage refuses an instructor")
        self.assertIsNotNone(accom.apply_approve_request(self.cfg(), self.ca, self.req), "and a cadet approving their own")
        self.assertIsNotNone(accom.apply_reject_request(self.cfg(), self.ca, self.req))
        self.assertEqual(self.approval(), "pending")


class Decisions(Base):
    def test_approving_is_one_tap_and_the_record_is_active_in_the_cadets_own_tab(self):
        at = self.chief_card("approve")
        at.button(key=f"req_ok_{self.req}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.success[0].value, t("acc_request_approved"))
        self.assertIn(t("chief_requests_none"), texts(at), "the list was re-read after the action")
        self.assertEqual([r["approval"] for r in accommodations.list_own(self.path, self.ca) if r["id"] == self.req], ["approved"])
        body = md(self.cadet_tab())
        self.assertIn(t("acc_status_active"), body)
        self.assertNotIn(t("acc_approval_pending"), body)

    def test_a_pending_record_shows_its_approval_state_in_the_cadets_tab(self):
        body = md(self.cadet_tab())
        self.assertIn(t("acc_approval_pending"), body)
        self.assertEqual(body.count(t("acc_status_active")), 1, "only the instructor's record is in effect")

    def test_rejecting_asks_once_and_keeps_the_record_visible_as_rejected_to_the_cadet(self):
        at = self.chief_card("approve")
        at.button(key=f"req_no_{self.req}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(self.approval(), "pending", "opening the dialog decides nothing")
        self.assertEqual(audit_rows(self.path, "accommodation.reject"), [])
        problem = accom.apply_reject_request(self.cfg(), self.boss, self.req)
        self.assertIsNone(problem)
        body = md(self.cadet_tab())
        self.assertIn(t("acc_approval_rejected"), body)
        self.assertIn("Ankle sprain", [str(x.value) for x in self.cadet_tab().text], "the rejected row is kept")
        self.assertEqual(len(audit_rows(self.path, "accommodation.reject")), 1)

    def test_the_reject_dialog_confirms_then_shows_the_notice_and_empties_the_list(self):
        for target, new in (("reject_request_dialog", _inline_reject), ("take_notice", _redraw_take_notice(accom.take_notice))):
            p = mock.patch.object(accom, target, new)
            p.start()
            self.addCleanup(p.stop)
        real_rerun = st.rerun
        p = mock.patch.object(st, "rerun", lambda scope="app": real_rerun())
        p.start()
        self.addCleanup(p.stop)
        at = self.chief_card("approve")
        at.button(key=f"req_no_{self.req}").click()
        at.run()
        self.assertIn(f"rej_ok_{self.req}", [b.key for b in at.button])
        at.button(key=f"rej_ok_{self.req}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.success[0].value, t("acc_request_rejected"))
        self.assertIn(t("chief_requests_none"), texts(at))
        self.assertEqual(self.approval(), "rejected")

    def test_one_audited_read_per_load_and_one_row_per_action(self):
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_ins)         # two kinds wait: every card starts closed
        at = self.chief_requests()
        self.assertEqual(audit_rows(self.path, "accommodation.pending_requests"), [], "a closed card reads nothing")
        at.button(key="reqcard_btn_approve").click()
        at.run()
        at.run()
        at.run()
        self.assertEqual(len(audit_rows(self.path, "accommodation.pending_requests")), 1, "reruns do not read again")
        at.button(key=f"req_ok_{self.req}").click()
        at.run()
        at.run()
        self.assertEqual(len(audit_rows(self.path, "accommodation.pending_requests")), 2, "one re-read after the action")
        self.assertEqual(len(audit_rows(self.path, "accommodation.approve")), 1)

    def test_a_decision_taken_elsewhere_is_a_visible_error_not_a_crash(self):
        at = self.chief_card("approve")
        accommodations.reject_request(self.path, self.boss, self.req)         # decided in another session
        at.button(key=f"req_ok_{self.req}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(at.error, "the refusal is shown as an error")
        self.assertFalse(at.success)
        self.assertEqual(self.approval(), "rejected")


class DeletionRequestsStayApart(Base):
    def test_a_deletion_request_has_its_own_words_and_the_flow_still_works(self):
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_ins)
        at = self.chief_card("delete")
        body = md(at)
        self.assertIn(t("chief_pending_one"), body)
        self.assertIn(t("acc_pending_chief"), body)
        self.assertIn(f"{t('acc_requested_by_label')} Ins", [str(x.value) for x in at.text])
        labels = {b.key: b.label for b in at.button}
        self.assertEqual(labels[f"pend_ok_{self.rec_ins}"], t("btn_approve_delete"))
        self.assertEqual(labels[f"pend_no_{self.rec_ins}"], t("btn_reject_delete"))
        self.assertEqual(body.count(t("acc_approval_pending")), 0, "the cadet's request sits in the other, closed card")
        at.button(key="reqcard_btn_approve").click()                     # one card open at a time: this closes the deletion card
        at.run()
        self.assertEqual(md(at).count(t("acc_approval_pending")), 1, "only the cadet's request, not the deletion card")
        at.button(key="reqcard_btn_delete").click()
        at.run()
        at.button(key=f"pend_no_{self.rec_ins}").click()
        at.run()
        self.assertEqual(at.success[0].value, t("acc_rejected"))
        self.assertIn(t("chief_pending_none"), texts(at))
        self.assertEqual(len(accommodations.list_own(self.path, self.ca)), 2, "a rejected deletion request deletes nothing")
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_ins)
        self.assertIsNone(accom.apply_approve(self.cfg(), self.boss, self.rec_ins))
        self.assertEqual([r["id"] for r in accommodations.list_own(self.path, self.ca)], [self.req])

    def test_the_deletion_card_of_a_pending_record_does_not_wear_the_approval_badge(self):
        accom.apply_request_delete(self.cfg(), self.ins, self.req)
        at = self.chief_card("delete")
        body = md(at)
        self.assertEqual(body.count(t("acc_approval_pending")), 0, "never in the deletion card")
        self.assertEqual(body.count(t("acc_pending_chief")), 1)
        at.button(key="reqcard_btn_approve").click()
        at.run()
        self.assertEqual(md(at).count(t("acc_approval_pending")), 1, "once in the request list")


class OpenedCadet(Base):
    def open_cadet(self):
        at = self.sign_in("boss@example.com")
        at.segmented_control(key="chief_platoon").set_value(self.pa)
        at.run()
        at.button(key=f"chief_open_{self.ca}").click()
        return at.run()

    def test_the_opened_cadet_shows_the_approval_state_and_offers_the_same_two_buttons(self):
        at = self.open_cadet()
        self.assertFalse(at.exception)
        keys = [b.key for b in at.button]
        self.assertIn(f"dec_ok_{self.req}", keys)
        self.assertIn(f"dec_no_{self.req}", keys)
        self.assertNotIn(f"dec_ok_{self.rec_ins}", keys, "an approved record has nothing to decide")
        self.assertIn(t("acc_approval_pending"), md(at))
        at.button(key=f"dec_ok_{self.req}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.success[0].value, t("acc_request_approved"), at.success[0].value)
        self.assertNotIn(f"dec_ok_{self.req}", [b.key for b in at.button])
        self.assertNotIn(f"req_ok_{self.req}", [b.key for b in at.button])
        self.assertEqual(md(at).count(t("acc_approval_pending")), 0, "the opened cadet follows the approval, no stale copy")
        self.assertIn(t("acc_status_active"), md(at))

    def test_rejecting_from_the_opened_cadet_opens_the_confirmation_and_changes_nothing_yet(self):
        at = self.open_cadet()
        at.button(key=f"dec_no_{self.req}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(self.approval(), "pending")

    def test_a_closed_row_shows_no_values_and_an_open_one_shows_the_history_card_once(self):
        closed = self.sign_in("boss@example.com")
        closed.segmented_control(key="chief_platoon").set_value(self.pa)
        closed.run()
        row_line = lambda a: [m.value for m in a.markdown if str(m.value).startswith("**" + t("test_run") + "**")]
        self.assertEqual(len(row_line(closed)), 0)
        at = self.open_cadet()
        self.assertEqual(len(row_line(at)), 1, "the details carry the history card, once")

    def test_a_rejected_record_renders_everywhere_without_a_crash(self):
        accommodations.reject_request(self.path, self.boss, self.req)
        for email in ("ins@example.com", "boss@example.com"):
            at = self.sign_in(email)
            self.assertFalse(at.exception, email)
        self.assertIn(t("acc_approval_rejected"), md(self.instructor_card()))
        self.assertIn(t("acc_approval_rejected"), md(self.open_cadet()))
        self.assertFalse(self.cadet_tab().exception)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT approval FROM accommodations WHERE id=?", (self.req,)).fetchone()[0], "rejected")


if __name__ == "__main__":
    unittest.main()
