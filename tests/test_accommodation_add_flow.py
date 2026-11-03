"""The add-accommodation dialog, end to end, for the chief and the instructor (owner report: signed in as the chief,
opening a cadet and saving the dialog "does not save").

Finding (browser run, Playwright, throwaway venv outside the repo): the row WAS stored, but the chief's opened-cadet
view and the platoon list are snapshots kept in session state (``_chief_open``, ``_chief_rows``); after the save they
were still the old ones, so the new record was not on screen and the chief concluded nothing had been saved.

AppTest cannot rerun a dialog fragment on its own: a click reruns the whole script, where the opener button is False
and the dialog is never drawn again. So this test stands in for the browser: the dialog body is drawn inline (it is the
same function, ``add_dialog.__wrapped__``) in the run that opens it and again in the run that presses save, exactly
what a fragment rerun does, and is gone after the save's full ``st.rerun()``, as a real dialog is."""
import unittest
from unittest import mock

import streamlit as st

from baror.messages_he import t
from baror.storage import accommodations, ops
from baror.ui import accom
from tests.test_app_staff import StaffBase, md

_BODY = accom.add_dialog.__wrapped__          # the dialog's own function, without the dialog container
_FLAG = "_test_dialog_open"


def _inline_open(cfg, actor, cadet_id):
    st.session_state[_FLAG] = (cfg, actor, cadet_id)
    _BODY(cfg, actor, cadet_id)


def _redraw_take_notice(real):
    def run():
        held = st.session_state.pop(_FLAG, None)         # the save run: the dialog fragment reruns with its widget state
        if held:
            _BODY(*held)
        real()
    return run


class AddDialogFlow(StaffBase):
    def setUp(self):
        super().setUp()
        real_rerun = st.rerun
        p = mock.patch.object(st, "rerun", lambda scope="app": real_rerun())      # AppTest has no fragment reruns
        p.start()
        self.addCleanup(p.stop)
        for target, new in (("add_dialog", _inline_open), ("take_notice", _redraw_take_notice(accom.take_notice))):
            p = mock.patch.object(accom, target, new)
            p.start()
            self.addCleanup(p.stop)

    def _fill_and_save(self, at, cadet_id):
        at.segmented_control(key=f"accd_{cadet_id}_kind").set_value("relief")
        at.segmented_control(key=f"accd_{cadet_id}_scope").set_value("run")
        at.text_input(key=f"accd_{cadet_id}_note").set_value("typed in the dialog")
        at.button(key=f"accd_save_{cadet_id}").click()
        return at.run()

    def test_the_chief_adds_for_an_opened_cadet_and_sees_the_record(self):
        at = self.sign_in("boss@example.com")
        at.segmented_control(key="chief_platoon").set_value(self.pa)
        at.run()
        at.button(key=f"chief_open_{self.ca}").click()
        at.run()
        self.assertEqual(accommodations.list_own(self.path, self.ca), [])
        at.button(key=f"chief_accadd_{self.ca}").click()
        at.run()
        self.assertFalse(at.exception)
        at = self._fill_and_save(at, self.ca)
        self.assertFalse(at.exception)
        rows = accommodations.list_own(self.path, self.ca)
        self.assertEqual([(r["source"], r["kind"], r["scope"], r["note"]) for r in rows],
                         [("chief", "relief", "run", "typed in the dialog")], "stored by the chief, source stamped by storage")
        self.assertIn(t("acc_added"), [s.value for s in at.success], "the confirmation is shown")
        self.assertIn(t("acc_source_chief"), md(at), "the new record is on screen, not a stale snapshot")

    def test_the_instructor_adds_from_the_cadet_card_and_sees_the_record(self):
        at = self.sign_in("ins@example.com")
        at.button(key=f"accadd_{self.ca}").click()
        at.run()
        self.assertFalse(at.exception)
        at = self._fill_and_save(at, self.ca)
        self.assertFalse(at.exception)
        rows = accommodations.list_own(self.path, self.ca)
        self.assertEqual([(r["source"], r["kind"], r["scope"]) for r in rows], [("instructor", "relief", "run")])
        self.assertIn(t("acc_added"), [s.value for s in at.success])
        self.assertIn(t("acc_source_instructor"), md(at))

    def test_a_refused_save_stores_nothing_and_keeps_the_error_for_the_dialog(self):
        at = self.sign_in("boss@example.com")
        at.segmented_control(key="chief_platoon").set_value(self.pa)
        at.run()
        at.button(key=f"chief_open_{self.ca}").click()
        at.run()
        at.button(key=f"chief_accadd_{self.ca}").click()
        at.run()
        at.button(key=f"accd_save_{self.ca}").click()          # nothing chosen
        at.run()
        self.assertEqual(accommodations.list_own(self.path, self.ca), [])
        self.assertFalse(at.exception)
        self.assertTrue(at.session_state[f"_accd_error_{self.ca}"], "the refusal message waits for the dialog")


if __name__ == "__main__":
    unittest.main()
