"""End to end, v9 (open registration, roles granted afterwards): the whole life of the accounts through the real screens under
AppTest, with a database in a temp folder.

Story, in order:
  1. cadets REGISTER through the form and pick a platoon (or none); everybody is a cadet
  2. the admin finds a registered account and grants instructor (title mm) through the grant dialog (identity check box)
  3. the chief grants a cmdr the same way
  4. the mm NOMINATES a cmdr (a cmdr cannot); the chief APPROVES it (identity check again)
  5. the instructor adds a registered cadet (no platoon -> assigned; cadet of another platoon -> a request)
  6. the admin REMOVES a cadet from the platoon (the account, the results stay)
  7. the now unassigned cadet picks a platoon in the profile (and sees the registration time the grant dialog showed)

AppTest cannot rerun a dialog fragment (a click reruns the whole script, where the button that opened the dialog is False and
the dialog is never drawn again). So, as in tests/test_accommodation_add_flow.py, the dialog functions are replaced by a
version that draws the same body inline and remembers it; the body is drawn again at the top of every following run until
something calls ``st.rerun`` (a save, a cancel), exactly what a fragment rerun does. The dialog bodies, the buttons, the keys
and the storage calls are the real ones."""
import unittest
from unittest import mock

import streamlit as st

from baror.messages_he import t
from baror.storage import db, ops, repo
from baror.ui import accounts_panel, cadet_admin, requests_section, shell, staff
from tests.test_app import AppCase, texts

FLAG = "_e2e_open_dialog"
PW2 = "long enough 1"                       # the password tests/test_app.py's register() uses


def _inline(fn):
    """Draw the dialog's own function in the opening run and keep it for the runs that follow."""
    body = fn.__wrapped__

    def open_it(*args, **kw):
        st.session_state[FLAG] = (body, args, kw)
        return body(*args, **kw)
    return open_it


class E2E(AppCase):
    cohort_views = True

    def setUp(self):
        super().setUp()
        real_rerun = st.rerun

        def rerun(scope="app"):
            st.session_state.pop(FLAG, None)            # the dialog closes with the full rerun
            real_rerun()
        patches = [mock.patch.object(st, "rerun", rerun)]
        for module, names in ((accounts_panel, ("grant_dialog", "demote_dialog", "release_dialog")),
                              (cadet_admin, ("edit_dialog", "move_dialog", "remove_dialog", "add_dialog", "revoke_dialog")),
                              (requests_section, ("grant_dialog",)),
                              (staff, ("nominate_dialog",))):
            for name in names:
                patches.append(mock.patch.object(module, name, _inline(getattr(module, name))))
        real_top_bar = shell._top_bar

        def top_bar(cfg, user, pages):
            real_top_bar(cfg, user, pages)
            held = st.session_state.get(FLAG)
            if held:                                    # the open dialog is drawn again, with its widget state
                body, args, kw = held
                body(*args, **kw)
        patches.append(mock.patch.object(shell, "_top_bar", top_bar))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # the company: two platoons, the first admin (operator path only) and the mp (a chief)
        self.pa = ops.create_platoon(self.path, "Platoon A")
        self.pb = ops.create_platoon(self.path, "Platoon B")
        self.admin = self.cadet("admin@example.com", "Admin")
        ops.set_role(self.path, self.admin, "admin")
        self.chief = self.cadet("mp@example.com", "Boss")
        ops.set_role(self.path, self.chief, "chief", title="mp")

    # ---- helpers
    def row(self, email):
        with db.reader(self.path) as c:
            r = c.execute("SELECT u.id,u.role,u.title,u.active,u.created_at,COALESCE(ip.platoon_id,u.platoon_id) AS platoon_id "
                          "FROM users u LEFT JOIN instructor_platoons ip ON ip.instructor_id=u.id WHERE u.email=?", (email,)).fetchone()
        return dict(r) if r else None

    def requests(self, **where):
        sql = "SELECT kind,status,requested_by,target_user_id,platoon_id FROM cadet_change_requests"
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
        with db.reader(self.path) as c:
            return [dict(r) for r in c.execute(sql + " ORDER BY id", tuple(where.values()))]

    def register_cadet(self, email, name, platoon=None, gender="gender_male"):
        at = self.register(email, name=name, gender=gender, platoon=platoon, password=PW2)
        self.assertFalse(at.exception)
        r = self.row(email)
        self.assertIsNotNone(r, f"{email} was not registered")
        self.assertEqual((r["role"], r["title"], r["platoon_id"]), ("cadet", None, platoon))
        return r["id"]

    def pick_key(self, at):
        return next(w.key for w in at.selectbox if w.key and w.key.startswith("acct_pick_"))

    def chief_accounts(self, at):
        """The chief's accounts panel is on the third page: more -> the 'accounts' tab (the admin has it on page one)."""
        at = self.go(at, "more")
        at.segmented_control(key="more_tab").set_value("accounts")
        return at.run()

    def grant_through_the_panel(self, at, target_id, email, *, role, title, platoon):
        """Search result -> choose the account -> role, title, platoon -> save -> the dialog -> identity box -> confirm."""
        at.selectbox(key=self.pick_key(at)).select(target_id)
        at = at.run()
        self.assertFalse(at.exception)
        k = f"acct_{target_id}_{at.session_state['_acct_gen']}"
        at.selectbox(key=f"{k}_role").select(role)
        at = at.run()
        at.selectbox(key=f"{k}_title").select(title)
        at = at.run()
        if role == "instructor":
            at.selectbox(key=f"{k}_platoon").select(platoon)
            at = at.run()
        at.button(key=f"{k}_save").click()
        at = at.run()
        self.assertFalse(at.exception)
        # the dialog shows who is being promoted and refuses until the identity box is ticked
        shown = texts(at)
        self.assertIn(email, shown)
        self.assertIn(accounts_panel.registered_text(self.row(email)["created_at"], self.cfg_tz()), shown)
        self.assertIn(t("adm_grant_note"), shown)
        gk = f"grant_{target_id}"
        self.assertTrue(at.button(key=f"{gk}_ok").disabled, "the grant button must stay disabled until the box is ticked")
        at.checkbox(key=f"{gk}_verified").set_value(True)
        at = at.run()
        self.assertFalse(at.button(key=f"{gk}_ok").disabled)
        at.button(key=f"{gk}_ok").click()
        at = at.run()
        self.assertFalse(at.exception)
        return at

    def cfg_tz(self):
        from baror import config
        return config.from_env({"DB_PATH": self.path}).tz

    # ---- the story
    def test_registration_grants_nomination_add_remove_and_choosing_a_platoon(self):
        # 1. everybody registers as a cadet through the form
        noa = self.register_cadet("noa@example.com", "Noa Cohen", self.pa, "gender_female")      # will become the mm of A
        dan = self.register_cadet("dan@example.com", "Dan Levi", self.pa)                         # will become a cmdr (chief)
        eli = self.register_cadet("eli@example.com", "Eli Bar", self.pa)                          # will be nominated
        gal = self.register_cadet("gal@example.com", "Gal Tal")                                   # no platoon yet
        bo = self.register_cadet("bo@example.com", "Bo Raz", self.pb)                             # in the OTHER platoon
        ofer = self.register_cadet("ofer@example.com", "Ofer Nir", self.pa)                       # has results, removed later
        repo.add_result(self.path, ofer, test_date="2026-10-01", run_tenths=11000, pushups=20)
        repo.add_result(self.path, ofer, test_date="2026-10-15", run_tenths=10800, pushups=25)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM users WHERE role<>'cadet'").fetchone()[0], 2,
                             "only the two accounts made by the operator path are staff")

        # 2. the admin grants instructor / mm to noa
        at = self.sign_in("admin@example.com")
        self.assertTrue(self.in_app(at))
        at = self.grant_through_the_panel(at, noa, "noa@example.com", role="instructor", title="mm", platoon=self.pa)
        self.assertEqual({k: self.row("noa@example.com")[k] for k in ("role", "title", "platoon_id")},
                         {"role": "instructor", "title": "mm", "platoon_id": self.pa})

        # 3. the chief grants dan cmdr of platoon A through the same panel
        at = self.sign_in("mp@example.com")
        self.assertTrue(self.in_app(at))
        at = self.chief_accounts(at)
        at = self.grant_through_the_panel(at, dan, "dan@example.com", role="instructor", title="cmdr", platoon=self.pa)
        self.assertEqual({k: self.row("dan@example.com")[k] for k in ("role", "title", "platoon_id")},
                         {"role": "instructor", "title": "cmdr", "platoon_id": self.pa})

        # 4a. a cmdr has no nomination button; the mm has one and nominates eli
        at = self.sign_in("dan@example.com", PW2)
        self.assertFalse(at.exception)
        self.assertNotIn("ins_nominate", [b.key for b in at.button], "a cmdr cannot nominate")
        at = self.sign_in("noa@example.com", PW2)
        self.assertFalse(at.exception)
        at.button(key="ins_nominate").click()
        at = at.run()
        at.text_input(key="nominate_email").set_value("eli@example.com")
        at = at.run()
        at.button(key="nominate_ok").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertEqual(self.requests(kind="nominate_cmdr"), [
            {"kind": "nominate_cmdr", "status": "pending", "requested_by": noa, "target_user_id": eli, "platoon_id": self.pa}])
        self.assertEqual(self.row("eli@example.com")["role"], "cadet", "a nomination grants nothing")
        self.assertIn(t("req_kind_nominate_cmdr"), texts(at), "the mm sees the nomination among 'my requests'")

        # 4b. the chief opens the change-requests card and approves it through the identity dialog
        at = self.go(self.sign_in("mp@example.com"), "requests")
        self.assertFalse(at.exception)
        self.assertIn("reqcard_btn_changes", [b.key for b in at.button])
        self.assertEqual(at.session_state["_req_open"], "changes", "only one kind is waiting, so its card opens by itself")
        with db.reader(self.path) as c:
            rid = c.execute("SELECT id FROM cadet_change_requests WHERE kind='nominate_cmdr'").fetchone()[0]
        at.button(key=f"change_ok_{rid}").click()
        at = at.run()
        self.assertFalse(at.exception)
        shown = texts(at)
        self.assertIn("eli@example.com", shown)
        self.assertTrue(at.button(key=f"grant_ok_{rid}").disabled)
        at.checkbox(key=f"grant_verified_{rid}").set_value(True)
        at = at.run()
        at.button(key=f"grant_ok_{rid}").click()
        at = at.run()
        self.assertFalse(at.exception)
        eli_row = self.row("eli@example.com")
        self.assertEqual((eli_row["role"], eli_row["title"], eli_row["platoon_id"]), ("instructor", "cmdr", self.pa))
        self.assertEqual(self.requests(kind="nominate_cmdr")[0]["status"], "approved")

        # 5. the mm adds registered cadets: gal (no platoon) is assigned at once, bo (platoon B) becomes a request
        at = self.sign_in("noa@example.com", PW2)
        at.button(key="ins_add_cadet").click()
        at = at.run()
        at.text_input(key=f"cadd_{self.pa}_email").set_value("gal@example.com")
        at = at.run()
        at.button(key=f"cadd_{self.pa}_ok").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertEqual(self.row("gal@example.com")["platoon_id"], self.pa)
        at.button(key="ins_add_cadet").click()
        at = at.run()
        at.text_input(key=f"cadd_{self.pa}_email").set_value("bo@example.com")
        at = at.run()
        at.button(key=f"cadd_{self.pa}_ok").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertEqual(self.row("bo@example.com")["platoon_id"], self.pb, "the cadet of another platoon is not moved")
        self.assertEqual([(r["kind"], r["status"], r["target_user_id"]) for r in self.requests(kind="add_conflict")],
                         [("add_conflict", "pending", bo)])
        before = self.requests()
        at.button(key="ins_add_cadet").click()                       # an unknown address gets the generic answer, nothing else
        at = at.run()
        at.text_input(key=f"cadd_{self.pa}_email").set_value("nobody@example.com")
        at = at.run()
        at.button(key=f"cadd_{self.pa}_ok").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertEqual(self.requests(), before, "an unknown e-mail creates no request")
        self.assertIsNone(self.row("nobody@example.com"))

        # 6. the admin removes ofer from the platoon: the account and the results stay
        at = self.sign_in("admin@example.com")
        at.selectbox(key="adm_platoon").select(self.pa)
        at = at.run()
        self.assertFalse(at.exception)
        at.button(key=f"btn_adm_cadet_remove_{ofer}").click()
        at = at.run()
        self.assertFalse(at.exception)
        at.button(key=f"del_ok_cadet_{ofer}").click()
        at = at.run()
        self.assertFalse(at.exception)
        gone = self.row("ofer@example.com")
        self.assertEqual((gone["role"], gone["active"], gone["platoon_id"]), ("cadet", 1, None))
        self.assertEqual(len(repo.list_results(self.path, ofer)), 2, "removing from the platoon keeps the results")

        # 7. ofer signs in, sees the registration time, and picks platoon B in the profile (once)
        at = self.sign_in("ofer@example.com", PW2)
        self.assertTrue(self.in_app(at))
        at = self.profile(at)
        self.assertFalse(at.exception)
        self.assertIn(accounts_panel.registered_text(gone["created_at"], self.cfg_tz()), texts(at),
                      "the profile shows the registration time that the grant dialog compares")
        at.selectbox(key="profile_platoon_pick").select(self.pb)
        at.button(key="profile_platoon_save").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertEqual(self.row("ofer@example.com")["platoon_id"], self.pb)
        self.assertNotIn("profile_platoon_pick", [w.key for w in at.selectbox], "the block is gone once a platoon is set")

    def test_nobody_can_grant_admin_from_the_screens_and_the_chief_cannot_grant_chief(self):
        """Nothing in the role drop-down offers `admin`; the chief is not offered `chief`."""
        ins = self.register_cadet("ins@example.com", "Ins Man", self.pa)
        at = self.sign_in("admin@example.com")
        at.selectbox(key=self.pick_key(at)).select(ins)
        at = at.run()
        k = f"acct_{ins}_{at.session_state['_acct_gen']}"
        offered = at.selectbox(key=f"{k}_role").options
        self.assertNotIn(accounts_panel.role_text("admin"), offered)
        self.assertIn(accounts_panel.role_text("chief"), offered, "the admin may grant chief")
        # the chief cannot grant chief either
        at = self.chief_accounts(self.sign_in("mp@example.com"))
        at.selectbox(key=self.pick_key(at)).select(ins)
        at = at.run()
        k = f"acct_{ins}_{at.session_state['_acct_gen']}"
        self.assertEqual(sorted(at.selectbox(key=f"{k}_role").options),
                         sorted(accounts_panel.role_text(r) for r in ("cadet", "instructor")))


if __name__ == "__main__":
    unittest.main()
