"""Instructor and chief screens under AppTest: role gating, name-only cards that open on a press, no overall verdict,
aggregates with small groups hidden, the logged drill-down (one named read per press, a platoon-level row for the list),
requests (edit, removal, add, nomination), the chief's three collapsed request cards, markdown safety."""
import datetime as dt
import re
import unittest
import zoneinfo

from baror import config
from baror.messages_he import t
from baror.storage import accommodations, db, ops, people, repo
from baror.ui import accom, cadet_admin, charts, requests_section, staff
from tests import dialogs
from tests.test_app import AppCase, texts

# Words of an overall verdict. Each test is graded alone until the owner explains "כ.מ" and the overall rule.
VERDICT = re.compile(r"עובר|עוברים|נכשל|עברו|מתוך|שני המדדים|כשיר|פסול")
CARD_KEY = re.compile(r"(ins|chief)_(open|close)_\d+$")


def md(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def names_in_cards(at) -> list[str]:
    """Cadet names are the labels of the card buttons (escaped for Markdown, so the escapes are removed here)."""
    return [re.sub(r"\\(.)", r"\1", b.label) for b in at.button if b.key and CARD_KEY.match(b.key)]


def audit_rows(path, like):
    with db.reader(path) as c:
        return [tuple(r) for r in c.execute(
            "SELECT actor_id,action,target_user_id,outcome FROM audit_log WHERE action LIKE ? ORDER BY id", (like,))]


class StaffOff(AppCase):
    cohort_views = False

    def test_flag_off_means_no_staff_screens_for_anyone(self):
        pa = ops.create_platoon(self.path, "A")
        for role, email in (("instructor", "i@example.com"), ("chief", "c@example.com"), ("admin", "a@example.com")):
            uid = self.cadet(email, role, role=role)
            if role == "instructor":
                ops.grant_platoon(self.path, uid, pa)
            options = self.sign_in(email).segmented_control(key="nav").options
            if role == "admin":                  # the admin screen does not depend on the switch (permissions only)
                self.assertEqual(options, [t("nav_admin"), t("nav_requests")])
            else:                                # instructor and chief fall back to the cadet bar (four destinations)
                self.assertEqual(len(options), 4, role)
            self.assertFalse([o for o in options if o in (t("nav_platoon"), t("nav_chief"))], role)


class StaffBase(AppCase):
    cohort_views = True

    def setUp(self):
        super().setUp()
        # AppTest cannot rerun a dialog fragment: these dialogs are drawn inline (see tests/dialogs.py)
        dialogs.install(self, (cadet_admin, "add_dialog"), (cadet_admin, "edit_dialog"), (cadet_admin, "remove_dialog"),
                        (staff, "nominate_dialog"), (requests_section, "grant_dialog"))
        self.pa, self.pb = ops.create_platoon(self.path, "Platoon A"), ops.create_platoon(self.path, "Platoon B")
        self.ins = self.cadet("ins@example.com", "Ins", role="instructor")
        ops.grant_platoon(self.path, self.ins, self.pa)
        self.boss = self.cadet("boss@example.com", "Boss", role="chief")
        self.ca = self.cadet("ca@example.com", "Alef-A", gender="M")
        self.cb = self.cadet("cb@example.com", "Bet-B", gender="M")
        ops.set_user_platoon(self.path, self.ca, self.pa)
        ops.set_user_platoon(self.path, self.cb, self.pb)
        repo.add_result(self.path, self.ca, test_date="2026-10-01", run_tenths=9000, pushups=30)
        repo.add_result(self.path, self.cb, test_date="2026-10-01", run_tenths=9000, pushups=30)

    def fill(self, platoon, n, prefix, run=9000, pushups=30):
        """n cadets with one result each in a platoon (enough to pass the small-group rule)."""
        ids = []
        for i in range(n):
            uid = self.cadet(f"{prefix}{i}@example.com", f"{prefix}-{i}", gender="M")
            ops.set_user_platoon(self.path, uid, platoon)
            repo.add_result(self.path, uid, test_date="2026-10-01", run_tenths=run, pushups=pushups)
            ids.append(uid)
        return ids

    def commander(self, email="mm@example.com", name="Mm", title="mm", platoon=None):
        """An instructor with a title (mm = platoon commander, cmdr = squad commander) in the platoon."""
        uid = self.cadet(email, name, role="instructor")
        ops.grant_platoon(self.path, uid, platoon or self.pa)
        ops.set_title(self.path, uid, title)
        return uid

    def cfg(self):
        return config.from_env({"DB_PATH": self.path})

    def chief_requests(self, email="boss@example.com", card=None):
        """The chief's second page ("בקשות"). With `card`, that card is the open one afterwards: a card opens by itself when
        it is the only kind with waiting requests, so it is pressed only when it is not open yet."""
        at = self.go(self.sign_in(email), "requests")
        if card and at.session_state[staff._REQ_OPEN] != card:
            at.button(key=f"reqcard_btn_{card}").click()
            at.run()
        return at


class Navigation(StaffBase):
    def test_each_role_is_offered_its_own_screen_and_more(self):
        nav = lambda email: self.sign_in(email).segmented_control(key="nav").options      # the visible labels
        self.assertEqual(nav("ca@example.com"), [t("nav_summary"), t("nav_add"), t("nav_history"), t("nav_more")])
        self.assertEqual(nav("ins@example.com"), [t("nav_platoon"), t("nav_more")])
        # the chief has three pages: the summary, the requests (the bell leads there) and "more"
        self.assertEqual(nav("boss@example.com"), [t("nav_chief"), t("nav_requests"), t("nav_more")])

    def test_the_chief_pages_hold_their_own_sections_and_nothing_of_the_others(self):
        subs = lambda at: [x.value for x in at.subheader]
        first = self.sign_in("boss@example.com")                                   # page one: dashboard, then the results
        self.assertEqual([s for s in subs(first) if s in (t("chief_aggregates_title"), t("chief_results_title"))],
                         [t("chief_aggregates_title"), t("chief_results_title")])
        self.assertNotIn(t("report_section_title"), subs(first))
        self.assertNotIn(t("req_section_title"), subs(first))
        self.assertFalse([b for b in first.button if b.key.startswith("reqcard_")])
        second = self.go(first, "requests")                                       # page two: only the requests
        self.assertEqual(subs(second), [t("req_section_title")])
        self.assertEqual([b.key for b in second.button if b.key.startswith("reqcard_btn_")],
                         ["reqcard_btn_approve", "reqcard_btn_delete", "reqcard_btn_changes"])
        third = self.go(second, "more")                                           # page three: reports first, then the rest
        self.assertEqual(third.segmented_control(key="more_tab").options,
                         [t("more_tab_" + k) for k in ("reports", "accounts", "tips", "videos", "profile")])
        self.assertIn(t("report_section_title"), subs(third))
        accounts = third.segmented_control(key="more_tab").set_value("accounts").run()
        self.assertIn(t("adm_accounts_title"), [x.value for x in accounts.subheader] + [x.value for x in accounts.title])

    def test_an_instructor_has_no_requests_page_and_no_reports_or_accounts_tab(self):
        at = self.go(self.sign_in("ins@example.com"), "more")
        self.assertNotIn(t("nav_requests"), at.segmented_control(key="nav").options)
        self.assertEqual(at.segmented_control(key="more_tab").options,
                         [t("more_tab_" + k) for k in ("tips", "videos", "profile")])

    def test_an_instructor_lands_on_the_platoon_screen_and_a_chief_on_the_dashboard(self):
        self.assertIn(t("platoon_title"), [x.value for x in self.sign_in("ins@example.com").title])
        self.assertIn(t("chief_title"), [x.value for x in self.sign_in("boss@example.com").title])

    def test_demotion_removes_the_staff_screen_on_the_next_run(self):
        at = self.sign_in("ins@example.com")
        self.assertIn("Alef-A", names_in_cards(at))
        ops.set_role(self.path, self.ins, "cadet")
        at.run()
        self.assertNotIn(t("nav_platoon"), at.segmented_control(key="nav").options)
        self.assertNotIn("Alef-A", names_in_cards(at))

    def test_an_instructor_cannot_reach_the_chief_screen_by_forcing_the_state(self):
        at = self.sign_in("ins@example.com")
        try:
            at.segmented_control(key="nav").set_value("chief").run()
        except Exception:
            return                                                    # refused outright: fine
        self.assertNotIn(t("chief_title"), [x.value for x in at.title])
        self.assertNotIn(t("group_too_small"), texts(at))


class Instructor(StaffBase):
    def open_cadet(self, at, cid):
        at.button(key=f"ins_open_{cid}").click()
        return at.run()

    def test_own_platoon_only_name_only_cards_and_one_platoon_level_audit_row(self):
        at = self.sign_in("ins@example.com")
        self.assertFalse(at.exception)
        self.assertEqual(names_in_cards(at), ["Alef-A"])
        self.assertNotIn("Bet-B", texts(at) + md(at))
        self.assertIn("Platoon A", [x.value for x in at.subheader])
        self.assertEqual(audit_rows(self.path, "cohort.%"), [(self.ins, "cohort.members", None, "ok")])
        at.run()
        at.run()
        self.assertEqual(len(audit_rows(self.path, "cohort.%")), 1, "the list is read once, not on every rerun")

    def test_a_closed_card_shows_the_name_only(self):
        self.fill(self.pa, 3, "More")
        at = self.sign_in("ins@example.com")
        body = texts(at) + md(at)
        self.assertEqual(len(names_in_cards(at)), 4)
        for fragment in (t("col_best_run"), t("col_best_pushups"), t("status_pass"), t("status_fail"), t("status_excellent"),
                         t("staff_gender", gender=t("gender_male")), t("acc_title")):
            self.assertNotIn(fragment, body, fragment)
        self.assertEqual(audit_rows(self.path, "cohort.cadet"), [])
        self.assertEqual([e.key for e in at.get("plotly_chart")], [])

    def test_there_is_no_overall_verdict_anywhere(self):
        self.fill(self.pa, 5, "V")
        for email in ("ins@example.com", "boss@example.com"):
            at = self.sign_in(email)
            if email.startswith("ins"):
                at = self.open_cadet(at, self.ca)
            # the chief page also holds the accounts panel: "מוצגים 10 מתוך 10 חשבונות" counts accounts, it grades nobody
            body = re.sub(r"מוצגים \d+ מתוך \d+ חשבונות\.", "", texts(at) + md(at))
            self.assertIsNone(VERDICT.search(body), (email, VERDICT.search(body)))

    def test_the_read_only_and_logged_banner_is_on_the_screen(self):
        self.assertIn(t("platoon_banner"), texts(self.sign_in("ins@example.com")))

    def test_names_are_shown_literally_never_as_markdown_or_html(self):
        evil = self.cadet("evil@example.com", "**bold** <b>x</b> [l](http://e.example)", gender="M")
        ops.set_user_platoon(self.path, evil, self.pa)
        at = self.sign_in("ins@example.com")
        self.assertIn("**bold** <b>x</b> [l](http://e.example)", names_in_cards(at))
        self.assertNotIn("**bold**", md(at))
        # the name never reaches st.html (the stylesheet is html too and contains the plain word "bold", so look for the
        # name's own markup)
        self.assertFalse([h for h in at.get("html") if "<b>x</b>" in str(getattr(h, "value", ""))
                          or "e.example" in str(getattr(h, "value", ""))])
        at = self.open_cadet(at, evil)
        self.assertFalse(at.exception)
        self.assertNotIn("**bold**", md(at))

    def test_empty_states_for_a_platoon_without_cadets_and_an_instructor_without_a_platoon(self):
        empty = ops.create_platoon(self.path, "Empty")
        solo = self.cadet("solo@example.com", "Solo", role="instructor")
        ops.grant_platoon(self.path, solo, empty)
        at = self.sign_in("solo@example.com")
        self.assertIn(t("empty_platoon"), texts(at))
        self.assertEqual(names_in_cards(at), [])
        self.cadet("nogrant@example.com", "NoGrant", role="instructor")
        at = self.sign_in("nogrant@example.com")
        self.assertIn(t("platoon_none"), texts(at))
        self.assertEqual(names_in_cards(at), [])

    def test_an_ungranted_platoon_is_never_offered_or_listed(self):
        pc = ops.create_platoon(self.path, "Platoon C")
        hidden = self.cadet("cc@example.com", "Gimel-C", gender="M")
        ops.set_user_platoon(self.path, hidden, pc)
        at = self.sign_in("ins@example.com")
        self.assertEqual(at.get("segmented_control")[0].key, "nav", "no platoon filter: an instructor has one platoon")
        self.assertEqual(names_in_cards(at), ["Alef-A"])
        self.assertNotIn("Gimel-C", texts(at))


class InstructorProgress(StaffBase):
    """Best values, accommodations, the progress chart and the full history only after the name is pressed."""

    def setUp(self):
        super().setUp()
        # Alef-A: 3 results; best run 13:50 (older), best push-ups 45 (newest); latest run 15:10, latest push-ups 45.
        repo.add_result(self.path, self.ca, test_date="2026-09-01", run_tenths=8300, pushups=35)
        repo.add_result(self.path, self.ca, test_date="2026-10-15", run_tenths=9100, pushups=45)
        self.other = self.cadet("o@example.com", "Other-A", gender="M")
        ops.set_user_platoon(self.path, self.other, self.pa)
        repo.add_result(self.path, self.other, test_date="2026-10-01", run_tenths=9500, pushups=20)

    def open_cadet(self, at, cid):
        at.button(key=f"ins_open_{cid}").click()
        return at.run()

    def chart_keys(self, at):
        return [e.key for e in at.get("plotly_chart")] if at.get("plotly_chart") else []

    def test_opening_a_cadet_reads_once_and_shows_best_values_the_chart_and_the_full_history(self):
        at = self.sign_in("ins@example.com")
        self.assertEqual(self.chart_keys(at), [], "no chart until a cadet is opened")
        self.assertEqual(audit_rows(self.path, "cohort.cadet"), [])
        at = self.open_cadet(at, self.ca)
        self.assertFalse(at.exception)
        self.assertEqual(audit_rows(self.path, "cohort.cadet"), [(self.ins, "cohort.cadet", self.ca, "ok")])
        body = md(at).replace("⁨", "").replace("⁩", "")
        self.assertIn("**" + t("col_best_run") + "** 13:50", body)
        self.assertIn("**" + t("col_best_pushups") + "** 45", body)
        self.assertIn(":material/military_tech: " + t("status_excellent"), body)       # 45 push-ups
        self.assertEqual(len(self.chart_keys(at)), 1)
        self.assertIn(t("trend_title"), [x.value for x in at.subheader])
        self.assertIn(t("chief_opened_logged"), texts(at))
        self.assertIn(t("staff_gender", gender=t("gender_male")), texts(at))
        self.assertIn(f"ins_close_{self.ca}", [b.key for b in at.button])
        history = [e for e in at.expander if e.label == t("history_title")]
        self.assertEqual(len(history), 1)
        rows = [m.value for m in history[0].markdown if "·" in m.value]                 # one test row per value: 'test value · dd/mm'
        self.assertEqual(len(rows), 6, "three stored results, two tests each")
        for day in ("01/09", "15/10", "01/10"):                                         # all of Alef-A's dates
            self.assertTrue(any(day in d for d in rows), day)
        at.run()                                                                       # a rerun is not a second read
        at.run()
        self.assertEqual(len(audit_rows(self.path, "cohort.cadet")), 1)

    def test_each_test_is_graded_on_its_own_inside_the_opened_card(self):
        self.fill(self.pa, 2, "More", run=12000, pushups=40)           # run: not reached, push-ups: excellent
        at = self.sign_in("ins@example.com")
        more = [m["id"] for m in __import__("baror.storage.cohort", fromlist=["x"]).list_platoon_members(
            self.path, self.ins, self.pa) if m["name"].startswith("More-0")][0]
        at = self.open_cadet(at, more)
        body = md(at)
        self.assertIn(":material/flag: " + t("status_fail"), body)
        self.assertIn(":material/military_tech: " + t("status_excellent"), body)

    def test_a_second_cadet_closes_the_first(self):
        at = self.open_cadet(self.sign_in("ins@example.com"), self.ca)
        at = self.open_cadet(at, self.other)
        keys = [b.key for b in at.button]
        self.assertIn(f"ins_close_{self.other}", keys)
        self.assertIn(f"ins_open_{self.ca}", keys, "the first cadet closed again")
        self.assertNotIn(f"ins_close_{self.ca}", keys)
        self.assertEqual(len(self.chart_keys(at)), 1)

    def test_closing_hides_the_details_and_signing_out_forgets_them_and_the_list(self):
        at = self.open_cadet(self.sign_in("ins@example.com"), self.ca)
        self.assertIn("_staff_open", at.session_state)
        self.assertIn("_chief_rows", at.session_state)               # the kept member list (one key for both roles)
        at.button(key=f"ins_close_{self.ca}").click()
        at.run()
        self.assertEqual(self.chart_keys(at), [])
        self.assertNotIn(t("chief_opened_logged"), texts(at))
        at = self.open_cadet(at, self.ca)
        at.button(key="logout_top").click()
        at.run()
        self.assertNotIn("_staff_open", at.session_state)
        self.assertNotIn("_chief_rows", at.session_state)

    def test_a_cadet_without_results_opens_with_an_empty_state_and_one_result_still_charts(self):
        quiet = self.cadet("quiet@example.com", "Quiet", gender="F")
        ops.set_user_platoon(self.path, quiet, self.pa)
        at = self.open_cadet(self.sign_in("ins@example.com"), quiet)
        self.assertFalse(at.exception)
        self.assertIn(t("no_results_cadet"), texts(at))
        self.assertEqual(self.chart_keys(at), [])
        at = self.open_cadet(at, self.other)                                           # a single result
        self.assertFalse(at.exception)
        self.assertEqual(len(self.chart_keys(at)), 1)

    def test_only_cadets_of_the_own_platoon_have_a_card_and_accommodation_buttons_still_work(self):
        at = self.sign_in("ins@example.com")
        self.assertNotIn(f"ins_open_{self.cb}", [b.key for b in at.button])
        self.assertEqual(sorted(b.key for b in at.button if b.key.startswith("ins_open_")),
                         sorted([f"ins_open_{self.ca}", f"ins_open_{self.other}"]))
        at = self.open_cadet(at, self.ca)
        at.button(key=f"accadd_{self.ca}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertIn(f"ins_close_{self.ca}", [b.key for b in at.button], "the dialog does not close the card")

    def test_an_admin_has_no_platoon_screen(self):
        adm = self.cadet("adm@example.com", "Adm", role="admin")
        at = self.sign_in("adm@example.com")
        self.assertNotIn("Alef-A", names_in_cards(at))
        self.assertEqual(audit_rows(self.path, "cohort.%"), [])
        self.assertTrue(adm)


class InstructorRequests(StaffBase):
    """Page-level add, request for edit / removal inside the opened card, the nomination of a מ"כ, 'הבקשות שלי'."""

    def setUp(self):
        super().setUp()
        self.free = self.cadet("free@example.com", "Free-Cadet", gender="F")         # registered, no platoon yet

    def add(self, at, email):
        at.button(key="ins_add_cadet").click()
        at.run()
        at.text_input(key=f"cadd_{self.pa}_email").set_value(email)
        at.button(key=f"cadd_{self.pa}_ok").click()
        return at.run()

    def test_adding_a_registered_cadet_without_a_platoon_assigns_and_lists_them(self):
        at = self.add(self.sign_in("ins@example.com"), "free@example.com")
        self.assertFalse(at.exception)
        self.assertIn(t("adm_add_assigned"), [x.value for x in at.success])
        self.assertIn("Free-Cadet", names_in_cards(at))
        self.assertEqual(repo.get_user(self.path, self.free)["platoon_id"], self.pa)

    def test_adding_a_cadet_of_another_platoon_is_a_request_and_nothing_moves(self):
        at = self.add(self.sign_in("ins@example.com"), "cb@example.com")
        self.assertIn(t("adm_add_requested"), [x.value for x in at.success])
        self.assertNotIn("Bet-B", names_in_cards(at))
        self.assertEqual(repo.get_user(self.path, self.cb)["platoon_id"], self.pb)
        mine = people.my_changes(self.path, self.ins)
        self.assertEqual([(r["kind"], r["status"]) for r in mine], [("add_conflict", "pending")])
        self.assertIn(t("req_kind_add_conflict"), md(at))

    def test_an_unknown_staff_or_inactive_address_gets_the_same_generic_refusal(self):
        shown = set()
        for email in ("nobody@example.com", "boss@example.com", "ins@example.com"):
            at = self.add(self.sign_in("ins@example.com"), email)
            self.assertEqual(len(at.error), 1, email)
            shown.add(at.error[0].value)
        self.assertEqual(len(shown), 1)
        self.assertEqual(people.my_changes(self.path, self.ins), [])

    def test_a_request_for_an_edit_waits_for_the_chief_and_changes_nothing(self):
        at = self.sign_in("ins@example.com")
        at.button(key=f"ins_open_{self.ca}").click()
        at.run()
        at.button(key=f"insedit_{self.ca}").click()
        at.run()
        at.text_input(key=f"cedit_{self.ca}_name").set_value("Alef-New")
        at.button(key=f"cedit_{self.ca}_save").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(repo.get_user(self.path, self.ca)["name"], "Alef-A")
        mine = people.my_changes(self.path, self.ins)
        self.assertEqual([(r["kind"], r["status"], r["new_name"]) for r in mine], [("edit", "pending", "Alef-New")])
        self.assertIn(t("adm_edit_requested"), [x.value for x in at.success])

    def test_an_edit_request_with_no_change_is_refused_in_the_dialog(self):
        at = self.sign_in("ins@example.com")
        at.button(key=f"ins_open_{self.ca}").click()
        at.run()
        at.button(key=f"insedit_{self.ca}").click()
        at.run()
        at.button(key=f"cedit_{self.ca}_save").click()
        at.run()
        self.assertIn(t("adm_edit_nothing"), [x.value for x in at.info])
        self.assertEqual(people.my_changes(self.path, self.ins), [])

    def test_a_request_for_removal_keeps_the_cadet_in_the_platoon_until_decided(self):
        at = self.sign_in("ins@example.com")
        at.button(key=f"ins_open_{self.ca}").click()
        at.run()
        at.button(key=f"insrm_{self.ca}").click()
        at.run()
        at.button(key=f"del_ok_cadet_{self.ca}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(repo.get_user(self.path, self.ca)["platoon_id"], self.pa)
        self.assertEqual([(r["kind"], r["status"]) for r in people.my_changes(self.path, self.ins)], [("delete", "pending")])

    def test_my_requests_show_the_decision_of_the_chief(self):
        rid = people.delete_cadet(self.path, self.ins, self.ca)["request_id"]
        at = self.sign_in("ins@example.com")
        self.assertIn(t("req_status_pending"), md(at))
        people.approve_change(self.path, self.boss, rid)
        at.run()
        self.assertIn(t("req_status_approved"), md(at))
        self.assertNotIn(t("req_status_pending"), md(at))
        self.assertNotIn("Alef-A", names_in_cards(self.sign_in("ins@example.com")), "the removed cadet left the list")

    def test_only_a_platoon_commander_is_offered_the_nomination_and_a_squad_commander_is_not(self):
        mm = self.commander("mm@example.com", "Mm", "mm")
        cm = self.commander("cm@example.com", "Cm", "cmdr")
        self.assertIn("ins_nominate", [b.key for b in self.sign_in("mm@example.com").button])
        self.assertNotIn("ins_nominate", [b.key for b in self.sign_in("cm@example.com").button])
        self.assertNotIn("ins_nominate", [b.key for b in self.sign_in("ins@example.com").button], "legacy: no title")
        self.assertTrue(mm and cm)

    def test_the_nomination_becomes_a_request_and_an_unknown_address_gets_the_generic_refusal(self):
        self.commander("mm@example.com", "Mm", "mm")
        at = self.sign_in("mm@example.com")
        at.button(key="ins_nominate").click()
        at.run()
        at.text_input(key="nominate_email").set_value("nobody@example.com")
        at.button(key="nominate_ok").click()
        at.run()
        self.assertEqual(len(at.error), 1)
        at.text_input(key="nominate_email").set_value("free@example.com")
        at.button(key="nominate_ok").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertIn(t("nominate_sent"), [x.value for x in at.success])
        mine = people.my_changes(self.path, self.mm_id("mm@example.com"))
        self.assertEqual([(r["kind"], r["status"]) for r in mine], [("nominate_cmdr", "pending")])
        self.assertEqual(repo.get_user(self.path, self.free)["role"], "cadet", "nothing is granted by the proposal")

    def mm_id(self, email):
        with db.reader(self.path) as c:
            return c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]


class RequestsCards(StaffBase):
    """The chief's three collapsed cards: only the open one is read; the bell's focus opens one."""

    def setUp(self):
        super().setUp()
        self.today = dt.datetime.now(zoneinfo.ZoneInfo("Asia/Jerusalem")).date()
        iso = lambda d: d.isoformat()
        self.rec_cadet = accommodations.add(self.path, self.ca, self.ca, kind="exemption", scope="run",
                                            start_date=iso(self.today - dt.timedelta(days=3)),
                                            end_date=iso(self.today + dt.timedelta(days=30)), note="Ankle sprain")
        self.rec_ins = accommodations.add(self.path, self.ins, self.ca, kind="relief", scope="pushups",
                                          start_date=iso(self.today + dt.timedelta(days=2)),
                                          end_date=iso(self.today + dt.timedelta(days=40)))
        accommodations.request_delete(self.path, self.ins, self.rec_ins)
        self.free = self.cadet("free@example.com", "Free-Cadet", gender="F")

    def chief(self):
        """The chief's requests page (the second page). Here two kinds are waiting, so every card starts closed."""
        return self.chief_requests()

    def open_card(self, at, name):
        at.button(key=f"reqcard_btn_{name}").click()
        return at.run()

    def test_three_collapsed_cards_and_nothing_is_read_while_they_are_closed(self):
        at = self.chief()
        self.assertFalse(at.exception)
        self.assertEqual([b.key for b in at.button if b.key.startswith("reqcard_btn_")],
                         ["reqcard_btn_approve", "reqcard_btn_delete", "reqcard_btn_changes"])
        self.assertEqual([b.label for b in at.button if b.key.startswith("reqcard_btn_")],
                         [t("req_card_label", label=t("req_card_approve"), n=1), t("req_card_label", label=t("req_card_delete"), n=1),
                          t("req_card_label", label=t("req_card_changes"), n=0)])
        for like in ("accommodation.pending%", "accommodation.pending_requests"):
            self.assertEqual(audit_rows(self.path, like), [], like)
        self.assertFalse([b for b in at.button if b.key.startswith(("req_ok_", "pend_ok_", "change_ok_"))])
        self.assertEqual([x.value for x in at.subheader], [t("req_section_title")], "the page holds only the requests")

    def test_the_bell_leads_to_the_requests_page_and_opens_the_card_it_names(self):
        at = self.sign_in("boss@example.com")                              # on the summary page
        self.assertEqual(at.segmented_control(key="nav").value, "chief")
        keys = [b.key for b in at.button if b.key.startswith("bell_go_")]
        self.assertEqual(keys, ["bell_go_approve", "bell_go_delete"], "one line per kind with waiting requests")
        at.button(key="bell_go_delete").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.segmented_control(key="nav").value, "requests")
        self.assertIn(f"pend_ok_{self.rec_ins}", [b.key for b in at.button], "the deletion card is open")
        self.assertNotIn(f"req_ok_{self.rec_cadet}", [b.key for b in at.button], "and no other card")
        self.assertNotIn("_req_focus", at.session_state, "the focus is used once")
        at = self.go(at, "chief")
        at.button(key="bell_go_approve").click()
        at.run()
        self.assertEqual(at.segmented_control(key="nav").value, "requests")
        self.assertIn(f"req_ok_{self.rec_cadet}", [b.key for b in at.button])
        self.assertNotIn(f"pend_ok_{self.rec_ins}", [b.key for b in at.button])

    def test_a_lone_kind_of_waiting_request_opens_by_itself_on_the_first_visit(self):
        accommodations.reject_delete(self.path, self.boss, self.rec_ins)          # no deletion request waits any more
        at = self.chief()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state[staff._REQ_OPEN], "approve")
        self.assertIn(f"req_ok_{self.rec_cadet}", [b.key for b in at.button], "the only waiting kind is open")
        self.assertNotIn(f"pend_ok_{self.rec_ins}", [b.key for b in at.button])

    def test_opening_the_approval_card_reads_once_and_lists_the_waiting_request(self):
        at = self.open_card(self.chief(), "approve")                      # two kinds wait: nothing opens by itself
        self.assertEqual(len(audit_rows(self.path, "accommodation.pending_requests")), 1)
        self.assertEqual(audit_rows(self.path, "accommodation.pending"), [], "the other cards stay unread")
        self.assertIn(t("chief_requests_one"), md(at))
        self.assertIn("Alef-A", [x.value for x in at.text])
        at.run()
        self.assertEqual(len(audit_rows(self.path, "accommodation.pending_requests")), 1, "a rerun is not another read")
        at.button(key=f"req_ok_{self.rec_cadet}").click()
        at.run()
        self.assertIn(t("acc_request_approved"), [x.value for x in at.success])

    def test_only_one_card_is_open_at_a_time_and_a_second_press_closes_it(self):
        at = self.open_card(self.chief(), "approve")
        at = self.open_card(at, "delete")
        self.assertEqual(len(audit_rows(self.path, "accommodation.pending")), 1)
        self.assertNotIn(f"req_ok_{self.rec_cadet}", [b.key for b in at.button])
        self.assertIn(f"pend_ok_{self.rec_ins}", [b.key for b in at.button])
        at = self.open_card(at, "delete")
        self.assertNotIn(f"pend_ok_{self.rec_ins}", [b.key for b in at.button])

    def test_deletion_requests_can_be_rejected_and_approved(self):
        at = self.open_card(self.chief(), "delete")
        self.assertIn(t("chief_pending_one"), md(at))
        at.button(key=f"pend_no_{self.rec_ins}").click()
        at.run()
        self.assertEqual(at.success[0].value, t("acc_rejected"))
        self.assertEqual(len(accommodations.list_own(self.path, self.ca)), 2, "a rejection deletes nothing")
        self.assertIn(t("chief_pending_none"), texts(at))

    def test_the_bell_focus_opens_the_card_it_names_and_is_used_once(self):
        at = self.chief()
        at.session_state["_req_focus"] = "changes"
        at.run()
        self.assertIn(t("change_none"), texts(at))
        self.assertNotIn("_req_focus", at.session_state)
        self.assertEqual(audit_rows(self.path, "accommodation.pending_requests"), [])
        at.session_state["_req_focus"] = "nonsense"
        at.run()
        self.assertFalse(at.exception)

    def test_a_cadet_change_request_is_listed_and_approving_applies_it(self):
        people.add_cadet(self.path, self.ins, "cb@example.com", self.pa)                # another platoon: a request
        people.edit_cadet(self.path, self.ins, self.ca, {"name": "Alef-New"})
        at = self.open_card(self.chief(), "changes")
        kinds = [str(m.value) for m in at.markdown if str(m.value).startswith("**")]
        self.assertIn("**" + t("req_kind_add_conflict") + "**", kinds)
        self.assertIn("**" + t("req_kind_edit") + "**", kinds)
        edit_id = [r["id"] for r in people.pending_changes(self.path, self.boss) if r["kind"] == "edit"][0]
        conflict_id = [r["id"] for r in people.pending_changes(self.path, self.boss) if r["kind"] == "add_conflict"][0]
        at.button(key=f"change_ok_{edit_id}").click()
        at.run()
        self.assertEqual(repo.get_user(self.path, self.ca)["name"], "Alef-New")
        self.assertIn(t("change_approved"), [x.value for x in at.success])
        at.button(key=f"change_no_{conflict_id}").click()
        at.run()
        self.assertEqual(repo.get_user(self.path, self.cb)["platoon_id"], self.pb, "a rejection changes nothing")
        self.assertIn(t("change_rejected"), [x.value for x in at.success])
        self.assertIn(t("change_none"), texts(at))

    def test_a_nomination_needs_the_identity_box_and_passes_the_name_and_time_seen(self):
        mm = self.commander("mm@example.com", "Mm", "mm")
        rid = people.nominate_cmdr(self.path, mm, "free@example.com")["request_id"]
        at = self.open_card(self.chief(), "changes")
        at.button(key=f"change_ok_{rid}").click()
        at.run()
        shown = "\n".join(str(x.value) for x in at.text)
        self.assertIn("Free-Cadet", shown)
        self.assertIn("free@example.com", shown)
        self.assertTrue(at.button(key=f"grant_ok_{rid}").disabled, "not clickable until the box is ticked")
        at.checkbox(key=f"grant_verified_{rid}").check().run()
        self.assertFalse(at.button(key=f"grant_ok_{rid}").disabled)
        at.button(key=f"grant_ok_{rid}").click()
        at.run()
        self.assertFalse(at.exception)
        row = repo.get_user(self.path, self.free)
        self.assertEqual((row["role"], row["title"], row["platoon_id"]), ("instructor", "cmdr", self.pa))
        self.assertEqual(len(audit_rows(self.path, "role.to_instructor")), 1)

    def test_a_nomination_of_an_account_that_changed_since_is_refused(self):
        mm = self.commander("mm@example.com", "Mm", "mm")
        rid = people.nominate_cmdr(self.path, mm, "free@example.com")["request_id"]
        at = self.open_card(self.chief(), "changes")
        with db.transaction(self.path) as c:
            c.execute("UPDATE users SET name='Somebody Else' WHERE id=?", (self.free,))
        at.button(key=f"change_ok_{rid}").click()
        at.run()
        at.checkbox(key=f"grant_verified_{rid}").check().run()
        at.button(key=f"grant_ok_{rid}").click()
        at.run()
        self.assertEqual(repo.get_user(self.path, self.free)["role"], "cadet")
        self.assertTrue(at.error)

    def test_an_instructor_and_a_cadet_have_no_requests_section(self):
        for email in ("ins@example.com", "ca@example.com"):
            at = self.sign_in(email)
            self.assertFalse([b for b in at.button if b.key.startswith("reqcard_")], email)


def table_of(at) -> list[dict]:
    """The text alternative of the platoon chart (st.table in the expander) as a list of rows."""
    frame = at.table[0].value.reset_index()
    return frame.to_dict("records")


class Chief(StaffBase):
    def open_platoon(self, at, platoon):
        at.segmented_control(key="chief_platoon").set_value(platoon)
        return at.run()

    def test_small_groups_are_hidden_and_no_names_or_ids_appear_on_the_dashboard(self):
        at = self.sign_in("boss@example.com")
        self.assertFalse(at.exception)
        body = texts(at) + md(at)
        self.assertNotIn("Alef-A", body)
        self.assertNotIn("Bet-B", body)
        self.assertIn(t("group_too_small"), texts(at))
        rows = table_of(at)
        self.assertEqual({r[t("col_platoon")] for r in rows}, {"Platoon A", "Platoon B"})
        for r in rows:                                                 # a hidden group shows dashes, never a figure
            self.assertEqual([r[t("status_" + lv)] for lv in ("excellent", "pass", "fail")], ["—", "—", "—"])
        self.assertEqual([m.value for m in at.metric], ["—", "—"])      # totals: nothing is counted under the rule

    def test_the_chart_draws_a_hidden_group_as_an_empty_labelled_row(self):
        fig = charts.platoon_figure([("Platoon A", None), ("Platoon B", {"excellent": 1, "pass": 2, "fail": 3})])
        self.assertEqual([a.text for a in fig.layout.annotations], [t("group_hidden")])
        self.assertEqual([tr.name for tr in fig.data], [t("status_excellent"), t("status_pass"), t("status_fail")])
        self.assertEqual([list(tr.x) for tr in fig.data], [[0, 1], [0, 2], [0, 3]])
        self.assertTrue(fig.layout.xaxis.autorange == "reversed" and fig.layout.yaxis.side == "right", "right to left")

    def test_a_group_of_five_shows_counts_per_level_and_per_test_as_a_chart_table_and_tiles(self):
        self.fill(self.pa, 5, "Big", run=9000, pushups=40)          # run: pass, push-ups: excellent (male); Alef-A: pass, pass
        at = self.sign_in("boss@example.com")
        row = [r for r in table_of(at) if r[t("col_platoon")] == "Platoon A"][0]
        self.assertEqual([row[t("status_" + lv)] for lv in ("excellent", "pass", "fail")], ["0", "6", "0"])     # run
        at.segmented_control(key="chief_metric").set_value("pushups").run()
        row = [r for r in table_of(at) if r[t("col_platoon")] == "Platoon A"][0]
        self.assertEqual([row[t("status_" + lv)] for lv in ("excellent", "pass", "fail")], ["5", "1", "0"])     # push-ups
        self.assertEqual([m.value for m in at.metric], ["100%", "100%"])
        self.assertNotIn("Big-0", md(at) + texts(at))

    def test_both_notices_come_before_any_click_and_nothing_named_is_read_yet(self):
        at = self.sign_in("boss@example.com")
        self.assertIn(t("chief_open_notice"), texts(at))
        self.assertIn(t("chief_list_notice"), texts(at))
        for like in ("chief.cadet", "chief.summary", "chief.members"):
            self.assertEqual(audit_rows(self.path, like), [], like)
        self.assertFalse([b for b in at.button if b.key.startswith("chief_open_")])

    def test_choosing_a_platoon_lists_names_only_with_one_platoon_level_row_and_opening_one_is_the_named_read(self):
        evil = self.cadet("evil@example.com", "**bold** [x](http://e.example)", gender="M")
        ops.set_user_platoon(self.path, evil, self.pa)
        at = self.sign_in("boss@example.com")
        at = self.open_platoon(at, self.pa)
        self.assertFalse(at.exception)
        shown = names_in_cards(at)
        self.assertIn("Alef-A", shown)
        self.assertIn("**bold** [x](http://e.example)", shown, "a name is shown literally")
        self.assertNotIn("Bet-B", shown, "only the chosen platoon is listed")
        self.assertEqual(audit_rows(self.path, "chief.members"), [(self.boss, "chief.members", None, "ok")],
                         "one platoon-level row, not one per cadet")
        self.assertEqual(audit_rows(self.path, "chief.summary"), [])
        self.assertEqual(audit_rows(self.path, "chief.cadet"), [])
        self.assertNotIn(t("staff_gender", gender=t("gender_male")), texts(at), "a closed card shows the name only")
        at.run()
        self.assertEqual(len(audit_rows(self.path, "chief.members")), 1, "the list is read once per chosen platoon, not per rerun")
        at.button(key=f"chief_open_{evil}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(audit_rows(self.path, "chief.cadet"), [(self.boss, "chief.cadet", evil, "ok")])
        self.assertIn(t("chief_opened_logged"), texts(at))
        at.run()                                                      # a plain rerun is not a second named read
        self.assertEqual(len(audit_rows(self.path, "chief.cadet")), 1)

    def test_the_opened_cadet_shows_status_per_test_the_chart_and_accommodations_under_the_name(self):
        at = self.open_platoon(self.sign_in("boss@example.com"), self.pa)
        at.button(key=f"chief_open_{self.ca}").click()
        at.run()
        body = md(at)
        self.assertIn(":material/check_circle: " + t("status_pass"), body)
        self.assertIn("**" + t("test_run") + "**", body)
        self.assertEqual(len(at.metric), 2 + 3, "two totals tiles plus the cadet's three tiles")
        self.assertIn(t("acc_title"), [x.value for x in at.subheader])
        self.assertIn(t("btn_add_accommodation"), [b.label for b in at.button])

    def test_only_one_cadet_is_open_at_a_time(self):
        other = self.cadet("o@example.com", "Other-A", gender="M")
        ops.set_user_platoon(self.path, other, self.pa)
        at = self.open_platoon(self.sign_in("boss@example.com"), self.pa)
        at.button(key=f"chief_open_{self.ca}").click()
        at.run()
        at.button(key=f"chief_open_{other}").click()
        at.run()
        keys = [b.key for b in at.button]
        self.assertIn(f"chief_close_{other}", keys)
        self.assertIn(f"chief_open_{self.ca}", keys, "the first cadet closed again")
        self.assertNotIn(f"chief_close_{self.ca}", keys)

    def test_closing_hides_the_details(self):
        at = self.open_platoon(self.sign_in("boss@example.com"), self.pa)
        at.button(key=f"chief_open_{self.ca}").click()
        at.run()
        at.button(key=f"chief_close_{self.ca}").click()
        at.run()
        self.assertNotIn(t("chief_opened_logged"), texts(at))

    def test_a_platoon_that_still_has_no_active_cadet_gets_an_empty_state(self):
        empty = ops.create_platoon(self.path, "Empty")
        at = self.open_platoon(self.sign_in("boss@example.com"), empty)
        self.assertIn(t("empty_platoon"), texts(at))

    def test_cadets_without_a_platoon_are_a_pseudo_platoon_that_can_be_listed_and_opened(self):
        lone = self.cadet("lone@example.com", "Lone-Cadet", gender="F")
        at = self.sign_in("boss@example.com")
        flt = at.segmented_control(key="chief_platoon")
        self.assertIn(t("unassigned"), flt.options)
        at = self.open_platoon(at, 0)
        self.assertEqual(names_in_cards(at), ["Lone-Cadet"])
        self.assertNotIn("chief_add_cadet", [b.key for b in at.button], "nobody is added to 'no platoon'")
        at.button(key=f"chief_open_{lone}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(audit_rows(self.path, "chief.cadet"), [(self.boss, "chief.cadet", lone, "ok")])

    def test_the_chief_can_add_a_registered_cadet_to_the_chosen_platoon_at_once(self):
        free = self.cadet("free@example.com", "Free-Cadet", gender="F")
        at = self.open_platoon(self.sign_in("boss@example.com"), self.pa)
        at.button(key="chief_add_cadet").click()
        at.run()
        at.text_input(key=f"cadd_{self.pa}_email").set_value("cb@example.com")          # another platoon: moved
        at.button(key=f"cadd_{self.pa}_ok").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(repo.get_user(self.path, self.cb)["platoon_id"], self.pa)
        self.assertIn("Bet-B", names_in_cards(at))
        self.assertEqual(repo.get_user(self.path, free)["platoon_id"], None)

    def test_signing_out_forgets_the_list_and_the_opened_cadet(self):
        at = self.open_platoon(self.sign_in("boss@example.com"), self.pa)
        at.button(key=f"chief_open_{self.ca}").click()
        at.run()
        self.assertIn("_chief_rows", at.session_state)
        self.assertIn("_chief_open", at.session_state)
        at.button(key="logout_top").click()
        at.run()
        self.assertNotIn("_chief_rows", at.session_state)
        self.assertNotIn("_chief_open", at.session_state)


class Accommodations(StaffBase):
    def setUp(self):
        super().setUp()
        self.today = dt.datetime.now(zoneinfo.ZoneInfo("Asia/Jerusalem")).date()
        iso = lambda d: d.isoformat()
        self.rec_cadet = accommodations.add(self.path, self.ca, self.ca, kind="exemption", scope="run",
                                            start_date=iso(self.today - dt.timedelta(days=3)),
                                            end_date=iso(self.today + dt.timedelta(days=30)), note="Ankle sprain")
        self.rec_ins = accommodations.add(self.path, self.ins, self.ca, kind="relief", scope="pushups",
                                          start_date=iso(self.today + dt.timedelta(days=2)),
                                          end_date=iso(self.today + dt.timedelta(days=40)))

    def opened(self, email="ins@example.com"):
        at = self.sign_in(email)
        at.button(key=f"ins_open_{self.ca}").click()
        return at.run()

    def test_the_opened_instructor_card_shows_records_dates_status_and_who_recorded_them(self):
        body = md(self.opened())
        self.assertIn(t("acc_approval_pending"), body, "the cadet's own entry waits for the chief")
        self.assertNotIn(t("acc_status_active"), body, "a record that is not approved is never 'in effect'")
        accommodations.approve_request(self.path, self.boss, self.rec_cadet)
        at = self.opened()
        body = md(at)
        self.assertIn(t("acc_source_cadet"), body)                       # the cadet's own entry is labelled as such
        self.assertIn(t("acc_source_instructor"), body)
        self.assertIn(t("acc_status_active"), body)
        self.assertIn(t("acc_status_upcoming"), body)
        self.assertIn(t("acc_kind_exemption"), body)
        self.assertIn(t("acc_kind_relief"), body)
        self.assertIn(t("staff_gender", gender=t("gender_male")), texts(at))
        plain = body.replace("⁨", "").replace("⁩", "")
        self.assertIn((self.today + dt.timedelta(days=40)).strftime("%d/%m/%Y"), plain)

    def test_the_cadets_note_is_plain_text_and_the_walk_track_marker_has_no_dates(self):
        km = self.cadet("km@example.com", "Km-A", gender="F", km=True)
        ops.set_user_platoon(self.path, km, self.pa)
        at = self.opened()
        self.assertIn("Ankle sprain", [x.value for x in at.text])
        at.button(key=f"ins_open_{km}").click()
        at.run()
        self.assertIn(t("acc_koam"), md(at))

    def test_an_instructor_can_request_a_deletion_once_and_then_sees_it_pending(self):
        at = self.opened()
        self.assertIn(f"reqdel_{self.rec_cadet}", [b.key for b in at.button])
        self.assertIsNone(accom.apply_request_delete(self.cfg(), self.ins, self.rec_cadet))
        at = self.opened()
        self.assertNotIn(f"reqdel_{self.rec_cadet}", [b.key for b in at.button], "no second request while one is pending")
        self.assertIn(t("acc_pending_chief"), md(at))
        self.assertIn(f"reqdel_{self.rec_ins}", [b.key for b in at.button])

    def test_the_dialog_buttons_open_without_changing_anything(self):
        at = self.opened()
        for key in (f"reqdel_{self.rec_cadet}", f"accadd_{self.ca}"):
            at.button(key=key).click()
            at.run()
            self.assertFalse(at.exception, key)
        self.assertEqual(len(accommodations.list_own(self.path, self.ca)), 2)

    def test_the_chief_sees_pending_deletion_requests_and_can_approve_or_reject(self):
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_cadet)
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_ins)
        at = self.chief_requests(card="delete")
        self.assertIn(t("chief_pending_many", n=2), md(at))
        self.assertIn(f"pend_ok_{self.rec_cadet}", [b.key for b in at.button])
        at.button(key=f"pend_no_{self.rec_ins}").click()
        at.run()
        self.assertEqual(at.success[0].value, t("acc_rejected"))
        self.assertEqual(len(accommodations.list_own(self.path, self.ca)), 2, "a rejection deletes nothing")
        self.assertIn(t("chief_pending_one"), md(at))
        self.assertIsNone(accom.apply_approve(self.cfg(), self.boss, self.rec_cadet))
        self.assertEqual([r["id"] for r in accommodations.list_own(self.path, self.ca)], [self.rec_ins])

    def test_no_pending_requests_is_a_designed_empty_state(self):
        at = self.chief_requests(card="delete")
        self.assertIn(t("chief_pending_none"), texts(at))

    def test_a_cadet_and_an_instructor_cannot_approve_and_nobody_but_the_chief_sees_the_requests(self):
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_cadet)
        self.assertIsNotNone(accom.apply_approve(self.cfg(), self.ins, self.rec_cadet))
        self.assertIsNotNone(accom.apply_approve(self.cfg(), self.ca, self.rec_cadet))
        self.assertEqual(len(accommodations.list_own(self.path, self.ca)), 2)
        # compare the section HEADING: the word "בקשות" is also part of other fixed copy ("עוד לא נשלחו בקשות")
        self.assertNotIn(t("req_section_title"), [x.value for x in self.sign_in("ins@example.com").subheader])

    def test_the_chief_opening_a_cadet_sees_the_records_and_may_delete_directly_after_a_confirmation(self):
        at = self.sign_in("boss@example.com")
        at.segmented_control(key="chief_platoon").set_value(self.pa)
        at.run()
        at.button(key=f"chief_open_{self.ca}").click()
        at.run()
        self.assertIn(t("acc_source_cadet"), md(at))
        self.assertIn(f"deldirect_{self.rec_cadet}", [b.key for b in at.button])
        self.assertNotIn(f"reqdel_{self.rec_cadet}", [b.key for b in at.button], "the chief deletes, an instructor requests")



if __name__ == "__main__":
    unittest.main()
