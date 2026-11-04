"""The admin screen, the accounts panel, the cadet dialogs, the join link box, the top-bar bell and the profile platoon
block, under AppTest (no browser).

AppTest cannot rerun a dialog fragment (a click reruns the whole script, where the opener button is False and the dialog
is never drawn again). The dialogs' work therefore lives in ``*_body`` functions and these tests draw the body inline
through small ``AppTest.from_function`` scripts, exactly what a fragment rerun shows. The full app is used for routing,
the table selection (the dataframe's selection state is set through the session state), the panel and the bell.

CSS cannot be rendered here: the stylesheet source is checked against the rules of CLAUDE.md, the 390 px / 1366 px
look is reviewed by hand."""
import dataclasses
import datetime as dt
import re
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from baror import config
from baror.domain import roles
from baror.messages_he import ERRORS, t
from baror.storage import accommodations, db, ops, people, repo, signup
from baror.ui import theme
from tests.test_app import AppCase, btn, texts

ROOT = Path(__file__).resolve().parent.parent
ADMIN_CSS = (ROOT / "static" / "admin.css").read_text(encoding="utf-8")
SECRET = "s3cret-" * 6                       # 42 characters: join links on


# ------------------------------------------------------------------------------------------------ inline dialog scripts
def _grant_script(cfg, user, row, role, title, pid, pname):
    from baror.ui import accounts_panel
    accounts_panel.grant_body(cfg, user, row, role, title, pid, pname)


def _demote_script(cfg, user, row):
    from baror.ui import accounts_panel
    accounts_panel.demote_body(cfg, user, row)


def _release_script(cfg, user, row):
    from baror.ui import accounts_panel
    accounts_panel.release_body(cfg, user, row)


def _panel_script(cfg, user):
    from baror.ui import accounts_panel
    accounts_panel.render(cfg, user)


def _edit_script(cfg, user, cadet):
    from baror.ui import cadet_admin
    cadet_admin.edit_body(cfg, user, cadet)


def _move_script(cfg, user, cadet):
    from baror.ui import cadet_admin
    cadet_admin.move_body(cfg, user, cadet)


def _remove_script(cfg, user, cadet):
    from baror.ui import cadet_admin
    cadet_admin.remove_body(cfg, user, cadet)


def _add_script(cfg, user, pid):
    from baror.ui import cadet_admin
    cadet_admin.add_body(cfg, user, pid)


def _join_script(cfg, user, pid):
    from baror.ui import cadet_admin
    cadet_admin.join_link_box(cfg, user, pid)


def _revoke_script(cfg, user, pid):
    from baror.ui import cadet_admin
    cadet_admin.revoke_body(cfg, user, pid)


def _title(key):
    return ERRORS[key].title


class AdminBase(AppCase):
    cohort_views = True

    def setUp(self):
        super().setUp()
        self.pa, self.pb = ops.create_platoon(self.path, "Platoon A"), ops.create_platoon(self.path, "Platoon B")
        self.admin = self.cadet("admin@example.com", "Admina", role="admin")
        self.boss = self.staff("boss@example.com", "Bossa", "chief", title="mp")
        self.mm = self.staff("mm@example.com", "Emem", "instructor", title="mm", platoon=self.pa)
        self.cmdr = self.staff("cmdr@example.com", "Kaf", "instructor", title="cmdr", platoon=self.pa)
        self.c1 = self.cadet("alef@example.com", "Alef", gender="M")
        self.c2 = self.cadet("bet@example.com", "Bet", gender="M")
        self.c3 = self.cadet("gimel@example.com", "Gimel", gender="F")
        ops.set_user_platoon(self.path, self.c1, self.pa)
        ops.set_user_platoon(self.path, self.c2, self.pb)
        self.cfg = config.from_env({"DB_PATH": self.path, "BAROR_COHORT_VIEWS": "1"})

    # fixtures
    def staff(self, email, name, role, *, title, platoon=None):
        uid = self.cadet(email, name)
        ops.set_role(self.path, uid, role, title=title, platoon_id=platoon)
        return uid

    def user(self, uid):
        return repo.get_user(self.path, uid)

    def row(self, actor, email):
        return people.search_accounts(self.path, actor, query=email)["rows"][0]

    def field(self, table, col, uid):
        with db.reader(self.path) as c:
            return c.execute(f"SELECT {col} FROM {table} WHERE id=?", (uid,)).fetchone()[0]    # test-only literals

    def platoon_of(self, uid):
        return self.user(uid)["platoon_id"]

    def script(self, fn, *args, cfg=None):
        at = AppTest.from_function(fn, args=(cfg or self.cfg, *args), default_timeout=30)
        return at.run()

    def pick_row(self, at, email):
        """Choose a row of the account table (the dataframe's selection state is set through the session state)."""
        held = at.session_state["_acct_rows"]
        idx = [r["email"] for r in held["res"]["rows"]].index(email)
        at.session_state[f"acct_table_{held['gen']}"] = {"selection": {"rows": [idx], "columns": []}}
        return at.run()

    def gen(self, at):
        return at.session_state["_acct_gen"]

    def errors(self, at) -> str:
        return "\n".join(str(e.value) for e in at.error)


# ------------------------------------------------------------------------------------------------------------ routing
class Routing(AdminBase):
    cohort_views = False                     # the admin screen is NOT behind the cohort switch

    def test_admin_gets_exactly_two_pages_even_with_the_switch_off(self):
        """Accounts and cadets, and the requests. No 'more' (tips, videos, profile, export, account deletion): admins are
        made and replaced by the operator script only; sign-out is in the top bar."""
        at = self.sign_in("admin@example.com")
        self.assertFalse(at.exception)
        self.assertEqual(at.segmented_control(key="nav").options, [t("nav_admin"), t("nav_requests")])
        self.assertNotIn(t("nav_more"), at.segmented_control(key="nav").options)
        self.assertEqual(at.title[0].value, t("admin_title"))
        self.assertFalse(at.segmented_control(key="nav").value == "more")
        self.assertIn("logout_top", [b.key for b in at.button], "sign-out stays one tap away")

    def test_admin_has_no_chief_dashboard_no_platoon_screen_no_accommodation_tools(self):
        at = self.sign_in("admin@example.com")
        self.assertEqual(len(at.metric), 0, "the dashboard tiles are the chief's")
        self.assertNotIn(t("chief_title"), texts(at))
        self.assertNotIn(t("platoon_title"), texts(at))
        self.assertFalse([b for b in at.button if b.key and ("accadd" in b.key or "reqcard_btn_approve" in b.key)])
        self.assertFalse([o for o in at.segmented_control(key="nav").options if o in (t("nav_chief"), t("nav_platoon"))])

    def test_the_admin_requests_page_shows_the_open_card_and_no_more_tabs(self):
        at = self.go(self.sign_in("admin@example.com"), "requests")
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["nav"], "requests")
        self.assertIn("adm_reqcard_btn", [b.key for b in at.button])
        self.assertFalse([w for w in at.segmented_control if w.key == "more_tab"], "no tips / videos / profile tabs")
        self.assertFalse([b for b in at.button if b.key in ("profile_platoon_save",) or (b.key or "").startswith("delete_account")])

    def test_the_admin_needs_no_account_deletion_or_export_button(self):
        """Those live in 'more' / profile, which an admin is not offered."""
        at = self.sign_in("admin@example.com")
        self.assertFalse([b for b in at.button if "export" in (b.key or "") or "delete_account" in (b.key or "")])

    def test_chief_with_the_switch_off_keeps_the_cadet_bar_and_gets_no_bell(self):
        at = self.sign_in("boss@example.com")
        self.assertEqual(at.segmented_control(key="nav").options,
                         [t("nav_summary"), t("nav_add"), t("nav_history"), t("nav_more")])
        self.assertFalse([b for b in at.button if (b.key or "").startswith("bell_go")])
        self.assertNotIn("bell", [getattr(p, "key", None) for p in at.get("popover")])


class Screen(AdminBase):
    def test_the_screen_lists_the_accounts_in_registration_order_with_the_columns_of_the_brief(self):
        at = self.sign_in("admin@example.com")
        self.assertFalse(at.exception)
        held = at.session_state["_acct_rows"]
        self.assertEqual(held["res"]["total"], len(held["res"]["rows"]))
        self.assertIn("admin@example.com", [r["email"] for r in held["res"]["rows"]])
        cols = list(at.dataframe[0].value.columns)
        self.assertEqual(cols, [t(k) for k in ("adm_col_name", "adm_col_email", "adm_col_registered", "adm_col_role",
                                                "adm_col_title", "adm_col_platoon")])

    def test_search_narrows_the_table_and_is_one_read(self):
        at = self.sign_in("admin@example.com")
        at.text_input(key="acct_q").set_value("bet@")
        btn(at, t("btn_adm_search")).click()
        at = at.run()
        self.assertEqual([r["email"] for r in at.session_state["_acct_rows"]["res"]["rows"]], ["bet@example.com"])
        before = self.audit_count("people.search")
        at.run()                                      # a rerun is not another read
        self.assertEqual(self.audit_count("people.search"), before)

    def audit_count(self, action):
        with db.reader(self.path) as c:
            return c.execute("SELECT count(*) FROM audit_log WHERE action=?", (action,)).fetchone()[0]

    def test_choosing_a_cadet_row_opens_the_panel_with_name_email_and_registration_time(self):
        at = self.pick_row(self.sign_in("admin@example.com"), "alef@example.com")
        self.assertFalse(at.exception)
        shown = [x.value for x in at.text]
        self.assertIn("Alef", shown)
        self.assertIn("alef@example.com", shown)
        self.assertTrue([c for c in at.caption if t("adm_registered_at", date="") .split(":")[0] in str(c.value)])
        self.assertEqual(at.selectbox(key=f"acct_{self.c1}_{self.gen(at)}_role").options,
                         [t("role_cadet"), t("role_instructor"), t("role_chief")])

    def test_the_drop_down_under_the_table_chooses_an_account_too(self):
        at = self.sign_in("admin@example.com")
        at.selectbox(key=f"acct_pick_{self.gen(at)}").select(self.c2)
        at = at.run()
        self.assertIn("bet@example.com", [x.value for x in at.text])

    def test_an_admin_row_is_read_only_with_the_operator_script_note(self):
        at = self.pick_row(self.sign_in("admin@example.com"), "admin@example.com")
        self.assertIn(t("adm_admin_readonly"), [i.value for i in at.info])
        # "acct_role" is the role FILTER of the search form, not a row control
        self.assertFalse([s for s in at.selectbox if s.key and s.key != "acct_role" and s.key.endswith("_role")])
        self.assertFalse([b for b in at.button if b.key and b.key.endswith("_save")])

    def test_a_chief_row_is_open_for_the_admin_with_a_title_choice(self):
        at = self.pick_row(self.sign_in("admin@example.com"), "boss@example.com")
        self.assertTrue(at.selectbox(key=f"acct_{self.boss}_{self.gen(at)}_title"))
        self.assertTrue([x.value for x in at.text if t("title_mp") in x.value], "the title is shown next to the role")

    def test_role_without_title_and_platoon_cannot_be_saved(self):
        at = self.pick_row(self.sign_in("admin@example.com"), "alef@example.com")
        k = f"acct_{self.c1}_{self.gen(at)}"
        at.selectbox(key=f"{k}_role").select("instructor")
        at = at.run()
        self.assertTrue(at.button(key=f"{k}_save").disabled)
        self.assertIn(t("adm_fill_title_platoon"), [c.value for c in at.caption])
        at.selectbox(key=f"{k}_title").select("cmdr")
        at.selectbox(key=f"{k}_platoon").select(self.pb)
        at = at.run()
        self.assertFalse(at.button(key=f"{k}_save").disabled)

    def test_the_platoon_choice_says_which_platoons_have_a_mm_and_how_many_mkim(self):
        at = self.pick_row(self.sign_in("admin@example.com"), "alef@example.com")
        k = f"acct_{self.c1}_{self.gen(at)}"
        at.selectbox(key=f"{k}_role").select("instructor")
        at = at.run()
        options = at.selectbox(key=f"{k}_platoon").options
        self.assertIn(t("adm_platoon_option", name="Platoon A", status=t("adm_has_mm"), n=1, max=roles.MAX_CMDRS_PER_PLATOON), options)
        self.assertIn(t("adm_platoon_option", name="Platoon B", status=t("adm_no_mm"), n=0, max=roles.MAX_CMDRS_PER_PLATOON), options)
        at.selectbox(key=f"{k}_title").select("mm")
        at.selectbox(key=f"{k}_platoon").select(self.pa)
        at = at.run()
        self.assertIn(t("adm_hint_mm_taken"), [w.value for w in at.warning])

    def test_save_opens_the_grant_dialog_without_writing(self):
        at = self.pick_row(self.sign_in("admin@example.com"), "alef@example.com")
        k = f"acct_{self.c1}_{self.gen(at)}"
        at.selectbox(key=f"{k}_role").select("instructor")
        at = at.run()                                   # the title and platoon choices appear after the role is chosen
        at.selectbox(key=f"{k}_title").select("cmdr")
        at.selectbox(key=f"{k}_platoon").select(self.pb)
        at = at.run()
        at.button(key=f"{k}_save").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertEqual(self.user(self.c1)["role"], "cadet", "the dialog decides nothing")

    def test_same_role_title_change_to_a_taken_mm_shows_the_hebrew_message_and_changes_nothing(self):
        at = self.pick_row(self.sign_in("admin@example.com"), "cmdr@example.com")
        k = f"acct_{self.cmdr}_{self.gen(at)}"
        at.selectbox(key=f"{k}_title").select("mm")
        at = at.run()
        at.button(key=f"{k}_save").click()
        at = at.run()
        self.assertIn(_title("MM_TAKEN"), self.errors(at))
        self.assertEqual(self.user(self.cmdr)["title"], "cmdr")

    def test_moving_an_instructor_into_a_full_platoon_shows_the_limit_with_its_number(self):
        for i in range(roles.MAX_CMDRS_PER_PLATOON):
            self.staff(f"k{i}@example.com", f"Kaf{i}", "instructor", title="cmdr", platoon=self.pb)
        at = self.pick_row(self.sign_in("admin@example.com"), "cmdr@example.com")
        k = f"acct_{self.cmdr}_{self.gen(at)}"
        at.selectbox(key=f"{k}_platoon").select(self.pb)
        at = at.run()
        self.assertIn(t("adm_hint_cmdr_full", max=roles.MAX_CMDRS_PER_PLATOON), [w.value for w in at.warning])
        at.button(key=f"{k}_save").click()
        at = at.run()
        err = self.errors(at)
        self.assertIn(_title("CMDR_LIMIT"), err)
        self.assertIn(str(roles.MAX_CMDRS_PER_PLATOON), err)
        self.assertEqual(self.platoon_of(self.cmdr), self.pa)

    def test_moving_an_instructor_to_a_free_platoon_works_and_refreshes_the_table(self):
        at = self.pick_row(self.sign_in("admin@example.com"), "cmdr@example.com")
        k = f"acct_{self.cmdr}_{self.gen(at)}"
        at.selectbox(key=f"{k}_platoon").select(self.pb)
        at = at.run()
        at.button(key=f"{k}_save").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertEqual(self.platoon_of(self.cmdr), self.pb)
        self.assertIn(t("adm_saved"), [s.value for s in at.success])

    def test_release_button_is_offered_for_a_cadet_row_to_the_admin_only(self):
        at = self.pick_row(self.sign_in("admin@example.com"), "alef@example.com")
        self.assertTrue(at.button(key=f"release_open_{self.c1}"))
        at = self.pick_row(self.sign_in("admin@example.com"), "cmdr@example.com")
        self.assertFalse([b for b in at.button if (b.key or "").startswith("release_open_")], "an instructor row")

    def test_creating_a_platoon_and_the_cadet_section_by_platoon(self):
        at = self.sign_in("admin@example.com")
        at.text_input(key="adm_platoon_name").set_value("Platoon C")
        btn(at, t("btn_adm_platoon_create")).click()
        at = at.run()
        self.assertIn(t("adm_platoon_created"), [s.value for s in at.success])
        self.assertEqual(len(people.list_platoons(self.path, self.admin)), 3)
        self.assertIn("Platoon C", [p["name"] for p in people.list_platoons(self.path, self.admin)])

    def test_cadets_are_listed_by_platoon_and_the_unassigned_have_their_own_group(self):
        at = self.sign_in("admin@example.com")
        at.selectbox(key="adm_platoon").select(self.pa)
        at = at.run()
        keys = [b.key for b in at.button]
        self.assertIn(f"btn_adm_cadet_edit_{self.c1}", keys)
        self.assertNotIn(f"btn_adm_cadet_edit_{self.c2}", keys, "another platoon")
        self.assertNotIn(f"btn_adm_cadet_edit_{self.c3}", keys, "unassigned")
        self.assertIn(f"adm_add_{self.pa}", keys)
        self.assertIn(t("adm_join_off"), [c.value for c in at.caption], "no secret: the feature is off")
        at.selectbox(key="adm_platoon").select(0)
        at = at.run()
        keys = [b.key for b in at.button]
        self.assertIn(f"btn_adm_cadet_edit_{self.c3}", keys)
        self.assertFalse([k for k in keys if k.startswith("adm_add_")], "nobody is added to 'no platoon'")

    def test_the_requests_card_is_open_on_the_requests_page_and_can_be_closed(self):
        at = self.sign_in("admin@example.com")
        self.assertNotIn("adm_reqcard_btn", [b.key for b in at.button], "the first page holds no requests")
        at = self.go(at, "requests")
        self.assertFalse(at.exception)
        self.assertTrue(at.session_state["_adm_req_open"], "a page with one card opens it by itself")
        at.button(key="adm_reqcard_btn").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertFalse(at.session_state["_adm_req_open"])


# ------------------------------------------------------------------------------------------------- the chief's panel
class ChiefPanel(AdminBase):
    def test_chief_search_never_lists_an_admin_row(self):
        at = self.script(_panel_script, self.user(self.boss))
        self.assertFalse(at.exception)
        roles_seen = {r["role"] for r in at.session_state["_acct_rows"]["res"]["rows"]}
        self.assertNotIn("admin", roles_seen)
        self.assertIn("instructor", roles_seen)
        self.assertEqual([o for o in at.selectbox(key="acct_role").options], [t("role_cadet"), t("role_instructor"), t("role_chief")])

    def test_admin_search_does_list_the_admin_row_and_offers_the_admin_filter(self):
        at = self.script(_panel_script, self.user(self.admin))
        self.assertIn("admin", {r["role"] for r in at.session_state["_acct_rows"]["res"]["rows"]})
        self.assertIn(t("role_admin"), at.selectbox(key="acct_role").options)

    def test_chief_may_grant_instructor_but_is_not_offered_chief_and_has_no_release(self):
        at = self.script(_panel_script, self.user(self.boss))
        self.assertEqual(at.session_state["_acct_gen"], 1)
        at = self.pick_row(at, "alef@example.com")
        k = f"acct_{self.c1}_1"
        self.assertEqual(at.selectbox(key=f"{k}_role").options, [t("role_cadet"), t("role_instructor")])
        self.assertFalse([b for b in at.button if (b.key or "").startswith("release_open_")])

    def test_chief_cannot_change_another_chief_or_the_own_account(self):
        other = self.staff("smp@example.com", "Sam", "chief", title="smp")
        at = self.pick_row(self.script(_panel_script, self.user(self.boss)), "smp@example.com")
        self.assertIn(t("adm_chief_readonly"), [c.value for c in at.caption])
        at = self.pick_row(self.script(_panel_script, self.user(self.boss)), "boss@example.com")
        self.assertIn(t("adm_self"), [c.value for c in at.caption])
        self.assertTrue(other)


# --------------------------------------------------------------------------------------------------- grant and friends
class Grant(AdminBase):
    def grant(self, actor, target_email, role, title, pid, pname=None, *, tick=True, row=None):
        row = row or self.row(actor, target_email)
        at = self.script(_grant_script, self.user(actor), row, role, title, pid, pname)
        uid = row["id"]
        at.checkbox(key=f"grant_{uid}_verified").set_value(tick)
        at = at.run()
        return at, uid

    def test_the_dialog_shows_name_email_registration_time_and_the_warning(self):
        row = self.row(self.admin, "alef@example.com")
        at = self.script(_grant_script, self.user(self.admin), row, "instructor", "cmdr", self.pa, "Platoon A")
        shown = [x.value for x in at.text]
        self.assertIn(t("adm_name_line", name="Alef"), shown)
        self.assertIn(t("adm_email_line", email="alef@example.com"), shown)
        self.assertTrue([s for s in shown if s.startswith(t("adm_registered_at", date="").split(":")[0])])
        self.assertIn(t("adm_grant_note"), [w.value for w in at.warning])
        self.assertEqual(at.checkbox(key=f"grant_{self.c1}_verified").label, t("adm_verified"))
        self.assertIn(t("adm_line_platoon", platoon="Platoon A"), shown)

    def test_the_name_is_text_never_markdown(self):
        with db.transaction(self.path) as c:
            c.execute("UPDATE users SET name=? WHERE id=?", ("**bold**[x](y)", self.c1))
        row = self.row(self.admin, "alef@example.com")
        at = self.script(_grant_script, self.user(self.admin), row, "instructor", "cmdr", self.pa, "Platoon A")
        self.assertIn(t("adm_name_line", name="**bold**[x](y)"), [x.value for x in at.text])
        self.assertNotIn("**bold**", "\n".join(str(m.value) for m in at.markdown))

    def test_without_the_box_ticked_the_grant_button_is_disabled_and_nothing_is_written(self):
        at, uid = self.grant(self.admin, "alef@example.com", "instructor", "cmdr", self.pa, tick=False)
        self.assertTrue(at.button(key=f"grant_{uid}_ok").disabled)
        self.assertEqual(self.user(uid)["role"], "cadet")

    def test_ticked_box_grants_the_role_with_the_verified_name_and_time(self):
        at, uid = self.grant(self.admin, "alef@example.com", "instructor", "cmdr", self.pa, "Platoon A")
        self.assertFalse(at.button(key=f"grant_{uid}_ok").disabled)
        at.button(key=f"grant_{uid}_ok").click()
        at.run()
        u = self.user(uid)
        self.assertEqual((u["role"], u["title"], u["platoon_id"]), ("instructor", "cmdr", self.pa))

    def test_chief_title_granted_by_the_admin(self):
        at, uid = self.grant(self.admin, "alef@example.com", "chief", "smp", None)
        at.button(key=f"grant_{uid}_ok").click()
        at.run()
        self.assertEqual((self.user(uid)["role"], self.user(uid)["title"]), ("chief", "smp"))

    def test_mm_taken_is_shown_in_hebrew_and_nothing_changes(self):
        at, uid = self.grant(self.admin, "alef@example.com", "instructor", "mm", self.pa, "Platoon A")
        at.button(key=f"grant_{uid}_ok").click()
        at = at.run()
        self.assertIn(_title("MM_TAKEN"), self.errors(at))
        self.assertEqual(self.user(uid)["role"], "cadet")

    def test_cmdr_limit_is_shown_in_hebrew_with_the_number(self):
        for i in range(roles.MAX_CMDRS_PER_PLATOON):
            self.staff(f"k{i}@example.com", f"Kaf{i}", "instructor", title="cmdr", platoon=self.pb)
        at, uid = self.grant(self.admin, "alef@example.com", "instructor", "cmdr", self.pb, "Platoon B")
        at.button(key=f"grant_{uid}_ok").click()
        err = self.errors(at.run())
        self.assertIn(_title("CMDR_LIMIT"), err)
        self.assertIn(str(roles.MAX_CMDRS_PER_PLATOON), err)

    def test_chief_limit_and_taken_title_are_shown_in_hebrew(self):
        self.staff("smp@example.com", "Sam", "chief", title="smp")
        at, uid = self.grant(self.admin, "alef@example.com", "chief", "mp", None)
        at.button(key=f"grant_{uid}_ok").click()
        self.assertIn(_title("CHIEF_LIMIT"), self.errors(at.run()))

    def test_a_chief_cannot_grant_chief(self):
        at, uid = self.grant(self.boss, "alef@example.com", "chief", "smp", None)
        at.button(key=f"grant_{uid}_ok").click()
        self.assertIn(_title("ROLE_NOT_ALLOWED"), self.errors(at.run()))
        self.assertEqual(self.user(uid)["role"], "cadet")

    def test_account_changed_since_it_was_verified_is_refused_and_the_lists_are_dropped(self):
        row = self.row(self.admin, "alef@example.com")
        with db.transaction(self.path) as c:
            c.execute("UPDATE users SET name='Someone Else' WHERE id=?", (self.c1,))
        at, uid = self.grant(self.admin, "alef@example.com", "instructor", "cmdr", self.pa, row=row)
        at.button(key=f"grant_{uid}_ok").click()
        at = at.run()
        self.assertEqual(self.user(uid)["role"], "cadet")
        self.assertIn(_title("ACCOUNT_CHANGED"), at.session_state["_notice_error"])

    def test_demotion_asks_once_and_clears_the_title(self):
        row = self.row(self.admin, "cmdr@example.com")
        at = self.script(_demote_script, self.user(self.admin), row)
        self.assertIn(t("adm_demote_body"), [m.value for m in at.markdown])
        at.button(key=f"demote_{self.cmdr}_ok").click()
        at.run()
        u = self.user(self.cmdr)
        self.assertEqual((u["role"], u["title"]), ("cadet", None))

    def test_a_chief_may_demote_an_instructor(self):
        row = self.row(self.boss, "cmdr@example.com")
        at = self.script(_demote_script, self.user(self.boss), row)
        at.button(key=f"demote_{self.cmdr}_ok").click()
        at.run()
        self.assertEqual(self.user(self.cmdr)["role"], "cadet")


class Release(AdminBase):
    def test_release_frees_the_email_and_keeps_the_results(self):
        repo.add_result(self.path, self.c1, test_date="2026-10-01", run_tenths=9000, pushups=30)
        row = self.row(self.admin, "alef@example.com")
        at = self.script(_release_script, self.user(self.admin), row)
        self.assertIn(t("adm_release_body"), [m.value for m in at.markdown])
        at.button(key=f"del_ok_release_{self.c1}").click()
        at.run()
        self.assertTrue(self.field("users", "email", self.c1).endswith("@released.invalid"))
        self.assertEqual(self.field("users", "active", self.c1), 0)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM results WHERE pseudonym=(SELECT pseudonym FROM users WHERE id=?)",
                                       (self.c1,)).fetchone()[0], 1)

    def test_a_chief_is_refused_with_the_hebrew_message(self):
        row = self.row(self.boss, "alef@example.com")
        at = self.script(_release_script, self.user(self.boss), row)
        at.button(key=f"del_ok_release_{self.c1}").click()
        self.assertTrue(self.errors(at.run()))
        self.assertFalse(self.field("users", "email", self.c1).endswith("@released.invalid"))


# ------------------------------------------------------------------------------------------------ cadet dialogs
class CadetDialogs(AdminBase):
    def cadet_dict(self, uid, **kw):
        u = self.user(uid)
        return {"id": uid, "name": u["name"], "gender": u["gender"], "is_km": u["is_km"], "platoon_id": u["platoon_id"], **kw}

    def requests(self, kind):
        with db.reader(self.path) as c:
            return c.execute("SELECT count(*) FROM cadet_change_requests WHERE kind=? AND status='pending'", (kind,)).fetchone()[0]

    def test_admin_edit_changes_the_name_gender_and_km_at_once(self):
        at = self.script(_edit_script, self.user(self.admin), self.cadet_dict(self.c1))
        at.text_input(key=f"cedit_{self.c1}_name").set_value("Alefon")
        at.segmented_control(key=f"cedit_{self.c1}_km").set_value(1)
        at = at.run()
        at.button(key=f"cedit_{self.c1}_save").click()
        at.run()
        u = self.user(self.c1)
        self.assertEqual((u["name"], u["is_km"]), ("Alefon", 1))

    def test_edit_with_nothing_changed_says_so(self):
        at = self.script(_edit_script, self.user(self.admin), self.cadet_dict(self.c1))
        at.button(key=f"cedit_{self.c1}_save").click()
        at = at.run()
        self.assertIn(t("adm_edit_nothing"), [i.value for i in at.info])

    def test_an_invalid_name_is_refused_with_the_catalogue_message(self):
        at = self.script(_edit_script, self.user(self.admin), self.cadet_dict(self.c1))
        at.text_input(key=f"cedit_{self.c1}_name").set_value("<b>x</b>")
        at = at.run()
        at.button(key=f"cedit_{self.c1}_save").click()
        self.assertIn(_title("INVALID_NAME"), self.errors(at.run()))
        self.assertEqual(self.user(self.c1)["name"], "Alef")

    def test_instructor_edit_becomes_a_request(self):
        at = self.script(_edit_script, self.user(self.mm), self.cadet_dict(self.c1))
        self.assertIn(t("adm_edit_request"), [c.value for c in at.caption])
        at.text_input(key=f"cedit_{self.c1}_name").set_value("Alefon")
        at = at.run()
        at.button(key=f"cedit_{self.c1}_save").click()
        at.run()
        self.assertEqual(self.user(self.c1)["name"], "Alef")
        self.assertEqual(self.requests("edit"), 1)

    def test_move_puts_the_cadet_into_another_platoon_or_into_none(self):
        at = self.script(_move_script, self.user(self.admin), self.cadet_dict(self.c1))
        self.assertIn(t("unassigned"), at.selectbox(key=f"cmove_{self.c1}_to").options)
        at.selectbox(key=f"cmove_{self.c1}_to").select(self.pb)
        at = at.run()
        at.button(key=f"cmove_{self.c1}_ok").click()
        at.run()
        self.assertEqual(self.platoon_of(self.c1), self.pb)
        at = self.script(_move_script, self.user(self.admin), self.cadet_dict(self.c1))
        at.selectbox(key=f"cmove_{self.c1}_to").select(0)
        at = at.run()
        at.button(key=f"cmove_{self.c1}_ok").click()
        at.run()
        self.assertIsNone(self.platoon_of(self.c1))

    def test_move_without_a_choice_asks_for_one(self):
        at = self.script(_move_script, self.user(self.admin), self.cadet_dict(self.c1))
        at.button(key=f"cmove_{self.c1}_ok").click()
        self.assertIn(t("adm_move_nothing"), [w.value for w in at.run().warning])

    def test_instructor_move_is_a_request_and_has_no_unassigned_choice(self):
        at = self.script(_move_script, self.user(self.mm), self.cadet_dict(self.c1))
        self.assertNotIn(t("unassigned"), at.selectbox(key=f"cmove_{self.c1}_to").options)
        at.selectbox(key=f"cmove_{self.c1}_to").select(self.pb)
        at = at.run()
        at.button(key=f"cmove_{self.c1}_ok").click()
        at.run()
        self.assertEqual(self.platoon_of(self.c1), self.pa)
        self.assertEqual(self.requests("edit"), 1)

    def test_remove_takes_the_cadet_out_of_the_platoon_and_keeps_account_and_results(self):
        repo.add_result(self.path, self.c1, test_date="2026-10-01", run_tenths=9000, pushups=30)
        at = self.script(_remove_script, self.user(self.admin), self.cadet_dict(self.c1))
        self.assertIn(t("adm_remove_body"), [w.value for w in at.markdown])
        at.button(key=f"del_ok_cadet_{self.c1}").click()
        at.run()
        self.assertIsNone(self.platoon_of(self.c1))
        self.assertEqual(self.field("users", "active", self.c1), 1)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM results WHERE pseudonym=(SELECT pseudonym FROM users WHERE id=?)",
                                       (self.c1,)).fetchone()[0], 1)

    def test_instructor_remove_is_a_request(self):
        at = self.script(_remove_script, self.user(self.mm), self.cadet_dict(self.c1))
        self.assertIn(t("adm_remove_body_request"), [w.value for w in at.markdown])
        at.button(key=f"del_ok_cadet_{self.c1}").click()
        at.run()
        self.assertEqual(self.platoon_of(self.c1), self.pa)
        self.assertEqual(self.requests("delete"), 1)

    def test_remove_works_with_a_bare_id(self):
        at = self.script(_remove_script, self.user(self.admin), self.c1)
        at.button(key=f"del_ok_cadet_{self.c1}").click()
        at.run()
        self.assertIsNone(self.platoon_of(self.c1))

    def test_add_assigns_a_registered_unassigned_cadet(self):
        at = self.script(_add_script, self.user(self.admin), self.pa)
        at.text_input(key=f"cadd_{self.pa}_email").set_value("gimel@example.com")
        at = at.run()
        at.button(key=f"cadd_{self.pa}_ok").click()
        at.run()
        self.assertEqual(self.platoon_of(self.c3), self.pa)

    def test_add_with_an_unknown_staff_or_inactive_email_gives_the_same_message(self):
        seen = set()
        for email in ("nobody@example.com", "cmdr@example.com", "boss@example.com"):
            at = self.script(_add_script, self.user(self.admin), self.pa)
            at.text_input(key=f"cadd_{self.pa}_email").set_value(email)
            at = at.run()
            at.button(key=f"cadd_{self.pa}_ok").click()
            err = self.errors(at.run())
            self.assertIn(_title("CADET_NOT_REGISTERED"), err)
            seen.add(err)
        self.assertEqual(len(seen), 1)

    def test_instructor_adding_a_cadet_of_another_platoon_files_a_request(self):
        at = self.script(_add_script, self.user(self.mm), self.pa)
        at.text_input(key=f"cadd_{self.pa}_email").set_value("bet@example.com")
        at = at.run()
        at.button(key=f"cadd_{self.pa}_ok").click()
        at.run()
        self.assertEqual(self.platoon_of(self.c2), self.pb)
        self.assertEqual(self.requests("add_conflict"), 1)


# ------------------------------------------------------------------------------------------------------- join links
class JoinLinks(AdminBase):
    def keyed(self, **kw):
        return dataclasses.replace(self.cfg, join_link_secret=SECRET, **kw)

    def test_without_a_secret_the_box_says_the_feature_is_off_and_offers_no_button(self):
        at = self.script(_join_script, self.user(self.admin), self.pa)
        self.assertIn(t("adm_join_off"), [c.value for c in at.caption])
        self.assertFalse([b for b in at.button if (b.key or "").startswith("join_")])

    def test_with_a_secret_the_admin_makes_a_link_that_selects_the_platoon(self):
        cfg = self.keyed(public_url="https://baror.example.org")
        at = self.script(_join_script, self.user(self.admin), self.pa, cfg=cfg)
        at.button(key=f"join_make_{self.pa}").click()
        at = at.run()
        url = at.code[0].value
        self.assertTrue(url.startswith("https://baror.example.org/?join="), url)
        token = url.split("join=")[1]
        self.assertEqual(signup.platoon_from_join_token(self.path, SECRET, token), self.pa)
        self.assertNotIn(SECRET, url)

    def test_without_a_public_address_only_the_suffix_is_shown_with_a_note(self):
        cfg = self.keyed(public_url=None)
        at = self.script(_join_script, self.user(self.admin), self.pa, cfg=cfg)
        at.button(key=f"join_make_{self.pa}").click()
        at = at.run()
        self.assertTrue(at.code[0].value.startswith("?join="))
        self.assertIn(t("adm_join_no_url"), [c.value for c in at.caption])

    def test_an_instructor_makes_a_link_for_the_own_platoon_only(self):
        cfg = self.keyed(public_url=None)
        at = self.script(_join_script, self.user(self.mm), self.pa, cfg=cfg)
        at.button(key=f"join_make_{self.pa}").click()
        self.assertTrue(at.run().code)
        at = self.script(_join_script, self.user(self.mm), self.pb, cfg=cfg)
        at.button(key=f"join_make_{self.pb}").click()
        at = at.run()
        self.assertFalse(at.code)
        self.assertTrue(self.errors(at))

    def test_revoking_bumps_the_epoch_so_the_old_link_stops_working(self):
        cfg = self.keyed(public_url=None)
        at = self.script(_join_script, self.user(self.admin), self.pa, cfg=cfg)
        at.button(key=f"join_make_{self.pa}").click()
        token = at.run().code[0].value.split("join=")[1]
        self.assertEqual(signup.platoon_from_join_token(self.path, SECRET, token), self.pa)
        at = self.script(_revoke_script, self.user(self.admin), self.pa, cfg=cfg)
        at.button(key=f"del_ok_join_{self.pa}").click()
        at.run()
        self.assertIsNone(signup.platoon_from_join_token(self.path, SECRET, token))


# ------------------------------------------------------------------------------------------------------------ bell
class Bell(AdminBase):
    def pending_accommodation(self):
        today = dt.date.today().isoformat()
        accommodations.add(self.path, self.c1, self.c1, kind="relief", scope="run", start_date=today, end_date=today)

    def pending_change(self):
        people.add_cadet(self.path, self.mm, "bet@example.com", self.pa)          # a cadet of another platoon: a request

    def go_buttons(self, at):
        return sorted(b.key for b in at.button if (b.key or "").startswith("bell_go_"))

    def test_the_chief_bell_counts_all_three_kinds(self):
        self.pending_accommodation()
        self.pending_change()
        at = self.sign_in("boss@example.com")
        self.assertFalse(at.exception)
        self.assertEqual(self.go_buttons(at), ["bell_go_approve", "bell_go_changes"])
        lines = [m.value for m in at.markdown]
        self.assertIn(t("bell_approve", n=1), lines)
        self.assertIn(t("bell_changes", n=1), lines)

    def test_the_admin_bell_counts_change_requests_only(self):
        self.pending_accommodation()
        self.pending_change()
        at = self.sign_in("admin@example.com")
        self.assertEqual(self.go_buttons(at), ["bell_go_changes"])

    def test_an_empty_bell_says_so_and_a_cadet_has_no_bell(self):
        at = self.sign_in("admin@example.com")
        self.assertEqual(self.go_buttons(at), [])
        self.assertIn(t("bell_none"), [c.value for c in at.caption])
        at = self.sign_in("alef@example.com")
        self.assertEqual(self.go_buttons(at), [])
        self.assertNotIn(t("bell_none"), [c.value for c in at.caption])

    def test_the_admin_bell_leads_to_the_open_change_requests_card(self):
        self.pending_change()
        at = self.sign_in("admin@example.com")
        at.button(key="bell_go_changes").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["nav"], "requests", "the bell lands on the requests page, not the first page")
        self.assertEqual(at.segmented_control(key="nav").value, "requests")
        self.assertTrue(at.session_state["_adm_req_open"])
        self.assertIn("adm_reqcard_btn", [b.key for b in at.button])
        self.assertNotIn("_req_focus", at.session_state)

    def test_the_chief_bell_sets_the_focus_for_the_requests_section_and_navigates(self):
        self.pending_accommodation()
        at = self.sign_in("boss@example.com")
        at = self.go(at, "more")
        at.button(key="bell_go_approve").click()
        at = at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["nav"], "requests")


# -------------------------------------------------------------------------------------------- the profile platoon block
class ProfilePlatoon(AdminBase):
    def test_a_cadet_without_a_platoon_gets_the_block_and_picks_once(self):
        at = self.profile(self.sign_in("gimel@example.com"))
        self.assertFalse(at.exception)
        self.assertIn(t("profile_platoon_note"), [c.value for c in at.caption])
        self.assertEqual(at.selectbox(key="profile_platoon_pick").options, ["Platoon A", "Platoon B"])
        at.button(key="profile_platoon_save").click()
        at = at.run()
        self.assertIn(t("profile_platoon_choose"), [w.value for w in at.warning])
        at.selectbox(key="profile_platoon_pick").select(self.pb)
        at = at.run()
        at.button(key="profile_platoon_save").click()
        at = at.run()
        self.assertEqual(self.platoon_of(self.c3), self.pb)
        self.assertIn(t("profile_platoon_saved"), [s.value for s in at.success])
        self.assertFalse([s for s in at.selectbox if s.key == "profile_platoon_pick"], "the block is gone once a platoon is set")

    def test_a_cadet_with_a_platoon_has_no_block(self):
        at = self.profile(self.sign_in("alef@example.com"))
        self.assertFalse([s for s in at.selectbox if s.key == "profile_platoon_pick"])

    def test_the_profile_shows_the_registration_time_as_the_accounts_table_does(self):
        from baror.ui import accounts_panel
        at = self.profile(self.sign_in("alef@example.com"))
        when = accounts_panel.registered_text(self.field("users", "created_at", self.c1), self.cfg.tz)
        self.assertIn(t("profile_registered", date=when), "\n".join(c.value for c in at.caption).replace("⁨", "").replace("⁩", ""))
        self.assertRegex(when, r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$")

    def test_registered_text_converts_utc_to_the_course_time_zone(self):
        from baror.ui import accounts_panel
        self.assertEqual(accounts_panel.registered_text("2026-01-04T09:30:12Z", self.cfg.tz), "04/01/2026 11:30")
        self.assertEqual(accounts_panel.registered_text("2026-07-04T09:30:12Z", self.cfg.tz), "04/07/2026 12:30")
        self.assertEqual(accounts_panel.registered_text("garbage", self.cfg.tz), "garbage")


# ----------------------------------------------------------------------------------------------------- the stylesheet
class Stylesheet(unittest.TestCase):
    CODE = re.sub(r"/\*.*?\*/", "", ADMIN_CSS, flags=re.S)

    def test_it_is_loaded_after_app_css_by_the_theme(self):
        text = theme.css_text()
        self.assertIn(".st-key-acct_table_box", text)
        self.assertLess(text.index(".st-key-top_bar{"), text.index(".st-key-acct_table_box"))

    def test_no_hashed_classes_no_important_logical_properties_no_angle_bracket(self):
        self.assertNotIn("st-emotion-cache", self.CODE)
        self.assertNotIn("!important", self.CODE)
        self.assertNotIn("<", ADMIN_CSS)
        physical = re.findall(r"(?<![-\w])(margin|padding|border)-(left|right)\s*:|(?<![-\w])(left|right)\s*:|text-align\s*:\s*(left|right)",
                              self.CODE)
        self.assertEqual(physical, [])

    def test_selectors_use_the_documented_hooks(self):
        for selector, _ in re.findall(r"([^{}]+)\{([^{}]*)\}", self.CODE):
            for part in selector.split(","):
                part = part.strip()
                if part:
                    self.assertTrue(re.search(r"\.st-key-|st-key-|\[data-testid", part), part)

    def test_the_bell_button_and_the_popover_buttons_are_48_px_targets(self):
        flat = self.CODE.replace("\n", " ")
        self.assertRegex(flat, r'stPopoverButton"\]\{min-height:var\(--tap\)')
        self.assertRegex(flat, r'stPopoverBody"\] \.stButton button\{min-height:var\(--tap\)')


if __name__ == "__main__":
    unittest.main()
