"""Instructor and chief screens under AppTest: role gating, one card per cadet, no overall verdict, aggregates with small
groups hidden, the logged drill-down (the notice comes BEFORE the click; one named read per press), markdown safety."""
import datetime as dt
import re
import unittest
import zoneinfo

from baror import config
from baror.messages_he import t
from baror.storage import accommodations, db, ops, repo
from baror.ui import accom, charts
from tests.test_app import AppCase, texts

# Words of an overall verdict. Each test is graded alone until the owner explains "כ.מ" and the overall rule.
VERDICT = re.compile(r"עובר|עוברים|נכשל|עברו|מתוך|שני המדדים|כשיר|פסול")


def md(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def names_in_cards(at) -> list[str]:
    """Cadet names are st.text elements (never Markdown)."""
    return [str(x.value) for x in at.text]


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
            self.assertEqual(len(options), 4, role)
            self.assertFalse([o for o in options if o in (t("nav_platoon"), t("nav_chief"))], role)


class StaffBase(AppCase):
    cohort_views = True

    def setUp(self):
        super().setUp()
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


class Navigation(StaffBase):
    def test_each_role_is_offered_its_own_screen_and_more(self):
        nav = lambda email: self.sign_in(email).segmented_control(key="nav").options      # the visible labels
        self.assertEqual(nav("ca@example.com"), [t("nav_summary"), t("nav_add"), t("nav_history"), t("nav_more")])
        self.assertEqual(nav("ins@example.com"), [t("nav_platoon"), t("nav_more")])
        self.assertEqual(nav("boss@example.com"), [t("nav_chief"), t("nav_more")])

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
    def test_own_platoon_only_one_card_per_cadet_and_every_read_is_audited(self):
        at = self.sign_in("ins@example.com")
        self.assertFalse(at.exception)
        self.assertEqual(names_in_cards(at), ["Alef-A"])
        self.assertNotIn("Bet-B", texts(at))
        self.assertIn("Platoon A", [x.value for x in at.subheader])
        self.assertEqual(audit_rows(self.path, "cohort.%"), [(self.ins, "cohort.summary", self.ca, "ok")])

    def test_one_card_per_cadet_with_each_test_graded_separately(self):
        ids = self.fill(self.pa, 3, "More", run=12000, pushups=40)       # run: not reached, push-ups: excellent
        at = self.sign_in("ins@example.com")
        self.assertEqual(len(names_in_cards(at)), 4)
        body = md(at)
        self.assertEqual(body.count(":material/flag: " + t("status_fail")), 3)         # three runs below the pass level
        self.assertEqual(body.count(":material/military_tech: " + t("status_excellent")), 3)   # three push-up results
        self.assertIn("**" + t("col_best_run") + "**", body)             # the card shows the BEST value of each test
        self.assertIn("**" + t("col_best_pushups") + "**", body)
        self.assertEqual(len(ids), 3)

    def test_there_is_no_overall_verdict_anywhere(self):
        self.fill(self.pa, 5, "V")
        for email in ("ins@example.com", "boss@example.com"):
            body = texts(self.sign_in(email)) + md(self.sign_in(email))
            self.assertIsNone(VERDICT.search(body), (email, VERDICT.search(body)))

    def test_the_read_only_and_logged_banner_is_on_the_screen(self):
        self.assertIn(t("platoon_banner"), texts(self.sign_in("ins@example.com")))

    def test_names_are_shown_literally_never_as_markdown_or_html(self):
        evil = self.cadet("evil@example.com", "**bold** <b>x</b> [l](http://e.example)", gender="M")
        ops.set_user_platoon(self.path, evil, self.pa)
        at = self.sign_in("ins@example.com")
        self.assertIn("**bold** <b>x</b> [l](http://e.example)", names_in_cards(at))
        self.assertNotIn("**bold**", md(at))
        self.assertFalse([h for h in at.get("html") if "bold" in str(getattr(h, "value", ""))])

    def test_a_cadet_without_results_and_a_platoon_without_cadets_have_their_own_empty_states(self):
        quiet = self.cadet("quiet@example.com", "Quiet", gender="F")
        ops.set_user_platoon(self.path, quiet, self.pa)
        self.assertIn(t("no_results_cadet"), texts(self.sign_in("ins@example.com")))
        empty = ops.create_platoon(self.path, "Empty")
        solo = self.cadet("solo@example.com", "Solo", role="instructor")
        ops.grant_platoon(self.path, solo, empty)
        at = self.sign_in("solo@example.com")
        self.assertIn(t("empty_platoon"), texts(at))
        self.assertEqual(names_in_cards(at), [])

    def test_an_instructor_without_a_grant_sees_a_notice_not_data(self):
        self.cadet("nogrant@example.com", "NoGrant", role="instructor")
        at = self.sign_in("nogrant@example.com")
        self.assertIn(t("platoon_none"), texts(at))
        self.assertEqual(names_in_cards(at), [])

    def test_a_second_granted_platoon_is_a_filter_and_an_ungranted_one_is_never_offered(self):
        ops.grant_platoon(self.path, self.ins, self.pb)
        pc = ops.create_platoon(self.path, "Platoon C")
        hidden = self.cadet("cc@example.com", "Gimel-C", gender="M")
        ops.set_user_platoon(self.path, hidden, pc)
        at = self.sign_in("ins@example.com")
        flt = at.segmented_control(key="staff_platoon")
        self.assertEqual(flt.options, ["Platoon A", "Platoon B"])      # visible names; Platoon C is not granted
        self.assertEqual(names_in_cards(at), ["Alef-A"])
        flt.set_value(self.pb).run()
        self.assertEqual(names_in_cards(at), ["Bet-B"])
        self.assertNotIn("Gimel-C", texts(at))


class InstructorProgress(StaffBase):
    """Best values on the card; the progress chart and the full history only after the cadet is opened."""

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

    def test_the_card_shows_the_best_values_with_a_level_and_no_chart(self):
        at = self.sign_in("ins@example.com")
        self.assertFalse(at.exception)
        body = md(at)
        self.assertIn("**" + t("col_best_run") + "** 13:50", body.replace("⁨", "").replace("⁩", ""))
        self.assertIn("**" + t("col_best_pushups") + "** 45", body.replace("⁨", "").replace("⁩", ""))
        self.assertIn(":material/military_tech: " + t("status_excellent"), body)       # 45 push-ups
        self.assertEqual(self.chart_keys(at), [], "no chart until a cadet is opened")
        self.assertEqual(len([b for b in at.button if b.key.startswith("ins_open_")]), 2)
        self.assertEqual(audit_rows(self.path, "cohort.cadet"), [])

    def test_opening_a_cadet_reads_once_and_shows_the_chart_and_the_full_history(self):
        at = self.open_cadet(self.sign_in("ins@example.com"), self.ca)
        self.assertFalse(at.exception)
        self.assertEqual(audit_rows(self.path, "cohort.cadet"), [(self.ins, "cohort.cadet", self.ca, "ok")])
        self.assertEqual(len(self.chart_keys(at)), 1)
        self.assertIn(t("trend_title"), [x.value for x in at.subheader])
        self.assertIn(t("chief_opened_logged"), texts(at))
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

    def test_a_second_cadet_closes_the_first(self):
        at = self.open_cadet(self.sign_in("ins@example.com"), self.ca)
        at = self.open_cadet(at, self.other)
        keys = [b.key for b in at.button]
        self.assertIn(f"ins_close_{self.other}", keys)
        self.assertIn(f"ins_open_{self.ca}", keys, "the first cadet closed again")
        self.assertNotIn(f"ins_close_{self.ca}", keys)
        self.assertEqual(len(self.chart_keys(at)), 1)

    def test_closing_hides_the_details_and_signing_out_forgets_them(self):
        at = self.open_cadet(self.sign_in("ins@example.com"), self.ca)
        self.assertIn("_staff_open", at.session_state)
        at.button(key=f"ins_close_{self.ca}").click()
        at.run()
        self.assertEqual(self.chart_keys(at), [])
        self.assertNotIn(t("chief_opened_logged"), texts(at))
        at = self.open_cadet(at, self.ca)
        at.button(key="logout_top").click()
        at.run()
        self.assertNotIn("_staff_open", at.session_state)

    def test_one_or_two_points_still_chart_and_an_empty_cadet_has_no_toggle(self):
        quiet = self.cadet("quiet@example.com", "Quiet", gender="F")
        ops.set_user_platoon(self.path, quiet, self.pa)
        at = self.sign_in("ins@example.com")
        self.assertIn(t("no_results_cadet"), texts(at))
        self.assertNotIn(f"ins_open_{quiet}", [b.key for b in at.button])
        at = self.open_cadet(at, self.other)                                           # a single result
        self.assertFalse(at.exception)
        self.assertEqual(len(self.chart_keys(at)), 1)

    def test_only_granted_platoons_can_be_opened_and_accommodation_buttons_still_work(self):
        at = self.sign_in("ins@example.com")
        self.assertNotIn(f"ins_open_{self.cb}", [b.key for b in at.button])
        self.assertEqual(sorted(b.key for b in at.button if b.key.startswith("ins_open_")),
                         sorted([f"ins_open_{self.ca}", f"ins_open_{self.other}"]))
        at = self.open_cadet(at, self.ca)
        at.button(key=f"accadd_{self.ca}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertIn(f"ins_close_{self.ca}", [b.key for b in at.button], "the dialog does not close the details")

    def test_an_admin_sees_the_same_screen_and_there_is_no_verdict(self):
        adm = self.cadet("adm@example.com", "Adm", role="admin")
        ops.grant_platoon(self.path, adm, self.pa)
        at = self.open_cadet(self.sign_in("adm@example.com"), self.ca)
        self.assertFalse(at.exception)
        body = texts(at) + md(at)
        self.assertIsNone(VERDICT.search(body), VERDICT.search(body))


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

    def test_choosing_a_platoon_lists_all_its_cadets_and_opening_one_is_the_only_named_read(self):
        evil = self.cadet("evil@example.com", "**bold** [x](http://e.example)", gender="M")
        ops.set_user_platoon(self.path, evil, self.pa)
        at = self.sign_in("boss@example.com")
        at = self.open_platoon(at, self.pa)
        self.assertFalse(at.exception)
        shown = names_in_cards(at)
        self.assertIn("Alef-A", shown)
        self.assertIn("**bold** [x](http://e.example)", shown, "a name is shown literally")
        self.assertNotIn("Bet-B", shown, "only the chosen platoon is listed")
        self.assertEqual(sorted(r[2] for r in audit_rows(self.path, "chief.summary")), sorted([self.ca, evil]))
        self.assertEqual(audit_rows(self.path, "chief.cadet"), [])
        at.run()
        self.assertEqual(len(audit_rows(self.path, "chief.summary")), 2, "the list is read once per chosen platoon, not per rerun")
        at.button(key=f"chief_open_{evil}").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(audit_rows(self.path, "chief.cadet"), [(self.boss, "chief.cadet", evil, "ok")])
        self.assertIn(t("chief_opened_logged"), texts(at))
        at.run()                                                      # a plain rerun is not a second named read
        self.assertEqual(len(audit_rows(self.path, "chief.cadet")), 1)

    def test_the_opened_cadet_shows_status_per_test_the_chart_and_accommodations_under_the_row(self):
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

    def test_the_instructor_card_shows_gender_records_dates_status_and_who_recorded_them(self):
        body = md(self.sign_in("ins@example.com"))
        self.assertIn(t("acc_approval_pending"), body, "the cadet's own entry waits for the chief")
        self.assertNotIn(t("acc_status_active"), body, "a record that is not approved is never 'in effect'")
        accommodations.approve_request(self.path, self.boss, self.rec_cadet)
        body = md(self.sign_in("ins@example.com"))
        self.assertIn(t("staff_gender", gender=t("gender_male")), texts(self.sign_in("ins@example.com")))
        self.assertIn(t("acc_source_cadet"), body)                       # the cadet's own entry is labelled as such
        self.assertIn(t("acc_source_instructor"), body)
        self.assertIn(t("acc_status_active"), body)
        self.assertIn(t("acc_status_upcoming"), body)
        self.assertIn(t("acc_kind_exemption"), body)
        self.assertIn(t("acc_kind_relief"), body)
        self.assertIn(self.today.strftime("%d/%m/%Y"), body.replace("\u2068", "").replace("\u2069", "")
                      .replace((self.today - dt.timedelta(days=3)).strftime("%d/%m/%Y"), self.today.strftime("%d/%m/%Y")))

    def test_the_cadets_note_is_plain_text_and_the_walk_track_marker_has_no_dates(self):
        km = self.cadet("km@example.com", "Km-A", gender="F", km=True)
        ops.set_user_platoon(self.path, km, self.pa)
        at = self.sign_in("ins@example.com")
        self.assertIn("Ankle sprain", names_in_cards(at))
        self.assertIn(t("acc_koam"), md(at))

    def test_an_instructor_can_request_a_deletion_once_and_then_sees_it_pending(self):
        at = self.sign_in("ins@example.com")
        self.assertIn(f"reqdel_{self.rec_cadet}", [b.key for b in at.button])
        self.assertIsNone(accom.apply_request_delete(self.cfg(), self.ins, self.rec_cadet))
        at = self.sign_in("ins@example.com")
        self.assertNotIn(f"reqdel_{self.rec_cadet}", [b.key for b in at.button], "no second request while one is pending")
        self.assertIn(t("acc_pending_chief"), md(at))
        self.assertIn(f"reqdel_{self.rec_ins}", [b.key for b in at.button])

    def test_the_dialog_buttons_open_without_changing_anything(self):
        at = self.sign_in("ins@example.com")
        for key in (f"reqdel_{self.rec_cadet}", f"accadd_{self.ca}"):
            at.button(key=key).click()
            at.run()
            self.assertFalse(at.exception, key)
        self.assertEqual(len(accommodations.list_own(self.path, self.ca)), 2)

    def test_the_chief_sees_pending_requests_and_can_approve_or_reject(self):
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_cadet)
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_ins)
        at = self.sign_in("boss@example.com")
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
        self.assertIn(t("chief_pending_none"), texts(self.sign_in("boss@example.com")))

    def test_a_cadet_and_an_instructor_cannot_approve_and_nobody_but_the_chief_sees_the_requests(self):
        accom.apply_request_delete(self.cfg(), self.ins, self.rec_cadet)
        self.assertIsNotNone(accom.apply_approve(self.cfg(), self.ins, self.rec_cadet))
        self.assertIsNotNone(accom.apply_approve(self.cfg(), self.ca, self.rec_cadet))
        self.assertEqual(len(accommodations.list_own(self.path, self.ca)), 2)
        self.assertNotIn(t("chief_pending_title"), texts(self.sign_in("ins@example.com")))

    def test_the_chief_opening_a_cadet_sees_the_records_and_may_delete_directly_after_a_confirmation(self):
        at = self.sign_in("boss@example.com")
        at.segmented_control(key="chief_platoon").set_value(self.pa)
        at.run()
        at.button(key=f"chief_open_{self.ca}").click()
        at.run()
        self.assertIn(t("acc_source_cadet"), md(at))
        self.assertIn(f"deldirect_{self.rec_cadet}", [b.key for b in at.button])
        self.assertNotIn(f"reqdel_{self.rec_cadet}", [b.key for b in at.button], "the chief deletes, an instructor requests")

    def cfg(self):
        return config.from_env({"DB_PATH": self.path})


if __name__ == "__main__":
    unittest.main()
