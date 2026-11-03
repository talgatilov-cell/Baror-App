"""Charts, tips and videos under AppTest (T6/T7; owned by the charts/content workstream). Results are written through
the repository, so these tests do not depend on how the add-result form looks."""
import datetime as dt
import unittest

from baror.messages_he import TIPS_PUSHUPS, TIPS_RUN, VIDEOS, t
from baror.storage import repo
from baror.ui import content
from tests.test_app import AppCase, texts

EMAIL = "dana@example.com"


def all_text(at) -> str:
    """Visible text: titles, captions, markdown (card headings and bullets) without the injected <style>/<script>,
    with the bidi isolates removed so that sentences can be compared as written in messages_he."""
    parts = [texts(at)] + [str(c.value) for c in at.caption]
    out = "\n".join(p for p in parts if not p.lstrip().startswith("<"))
    return out.replace("\u2068", "").replace("\u2069", "")


class ContentCase(AppCase):
    def with_results(self, n, gender="F", km=False, runs=True, pushups=True):
        run0 = 40000 if km else 12500          # a plausible walk (6 km) takes about an hour
        uid = self.cadet(EMAIL, gender=gender, km=km)
        start = dt.date.today() - dt.timedelta(days=7 * (n + 1))
        for i in range(n):
            repo.add_result(self.path, uid, test_date=(start + dt.timedelta(days=7 * (i + 1))).isoformat(),
                            run_tenths=(run0 - i * 100) if runs else None, pushups=(10 + i) if pushups else None)
        return self.sign_in(EMAIL)

    def charts(self, at):
        return at.get("plotly_chart")


class Chart(ContentCase):
    def test_no_results_no_chart_block(self):
        at = self.with_results(0)
        self.assertFalse(at.exception)
        self.assertEqual(self.charts(at), [])
        self.assertNotIn(t("trend_title"), texts(at))

    def test_chart_block_with_one_two_and_twelve_results(self):
        for n in (1, 2, 12):
            with self.subTest(n=n):
                self.setUp()
                at = self.with_results(n)
                self.assertFalse(at.exception)
                self.assertEqual(len(self.charts(at)), 1, "one chart at a time")
                self.assertIn(t("trend_title"), texts(at))
                self.assertEqual([e.label for e in at.expander], [t("chart_table")])
                self.assertEqual(at.segmented_control(key="chart_metric").options, [t("test_run"), t("test_pushups")])
                self.assertIn(t("chart_hint_up"), all_text(at))
                few = t("chart_few")
                self.assertEqual(few in all_text(at), n == 1)

    def test_the_metric_control_switches_to_pushups_and_the_walk_track_is_labelled_as_walk(self):
        at = self.with_results(3, km=True)
        self.assertEqual(at.segmented_control(key="chart_metric").options, [t("test_walk"), t("test_pushups")])
        at.segmented_control(key="chart_metric").set_value("pushups").run()
        self.assertFalse(at.exception)
        self.assertEqual(len(self.charts(at)), 1)
        self.assertIn("מ-10 ל-12 חזרות", all_text(at))

    def test_a_user_with_only_pushups_starts_on_pushups(self):
        at = self.with_results(2, runs=False)
        self.assertFalse(at.exception)
        self.assertEqual(at.segmented_control(key="chart_metric").value, "pushups")
        self.assertEqual(len(self.charts(at)), 1)

    def test_the_summary_sentence_is_text_under_the_chart(self):
        at = self.with_results(3)
        self.assertIn("ב-3 בחנים", all_text(at))
        self.assertNotIn("להכוונה בלבד", all_text(at), "thresholds are confirmed: no guidance-only notice")

    def test_pages_of_two_users_do_not_share_chart_data(self):
        self.with_results(2)
        other = self.cadet("noa@example.com", "Noa")
        repo.add_result(self.path, other, test_date=dt.date.today().isoformat(), run_tenths=7000, pushups=40)
        at = self.sign_in("noa@example.com")
        self.assertIn("בבוחן אחד", all_text(at))


class Tips(ContentCase):
    def more(self, at, tab):
        at = self.go(at, "more")
        at.segmented_control(key="more_tab").set_value(tab).run()
        self.assertFalse(at.exception)
        return at

    def test_tips_and_videos_pages_render(self):
        at = self.signed_in()
        for tab in ("tips", "videos"):
            at = self.more(at, tab)
            self.assertTrue(at.title, tab)

    def test_tips_are_cards_with_headings_bullets_and_the_week_table_and_use_no_alert_boxes(self):
        at = self.more(self.signed_in(), "tips")
        body = "\n".join(m.value for m in at.markdown)
        for section in (TIPS_RUN, TIPS_PUSHUPS):
            for card in section["cards"]:
                self.assertIn(f":material/{card['icon']}:", body)
                for b in card["bullets"]:
                    self.assertIn(content.isolate_latin(b), body)
        self.assertEqual(len(at.get("table")), 1, "the week table")
        self.assertEqual((len(at.info), len(at.success), len(at.warning), len(at.error)), (0, 0, 0, 0))

    def order(self, at):
        subs = [s.value for s in at.header]
        return subs.index(TIPS_RUN["title"]) < subs.index(TIPS_PUSHUPS["title"])

    def test_default_order_is_run_first_and_there_is_no_order_note_without_results(self):
        at = self.more(self.signed_in(), "tips")
        self.assertTrue(self.order(at))
        self.assertNotIn(t("tips_order_note"), all_text(at))

    def test_pushup_tips_come_first_when_pushups_are_lower_on_their_scale_than_the_run(self):
        uid = self.cadet(EMAIL, gender="M")
        # run 11:40 (excellent for men) vs 5 push-ups (below the pass level)
        repo.add_result(self.path, uid, test_date="2026-10-01", run_tenths=7000, pushups=5)
        at = self.more(self.sign_in(EMAIL), "tips")
        self.assertFalse(self.order(at))
        self.assertIn(t("tips_order_note"), all_text(at))

    def test_run_tips_stay_first_when_the_run_is_lower(self):
        uid = self.cadet(EMAIL, gender="M")
        repo.add_result(self.path, uid, test_date="2026-10-01", run_tenths=11000, pushups=40)
        at = self.more(self.sign_in(EMAIL), "tips")
        self.assertTrue(self.order(at))

    def test_ranking_rules_directly(self):
        user = {"gender": "M", "is_km": 0}
        rows = lambda r, p: [{"test_date": "2026-10-01", "run_tenths": r, "pushups": p}]
        self.assertTrue(content.pushups_first(rows(7000, 5), user))        # run excellent, push-ups below the pass level
        self.assertTrue(content.pushups_first(rows(9000, 5), user))        # run passed, push-ups below the pass level
        self.assertTrue(content.pushups_first(rows(7000, 22), user))       # run excellent, push-ups only passed
        self.assertFalse(content.pushups_first(rows(9000, 22), user))      # both passed
        self.assertFalse(content.pushups_first(rows(9000, 40), user))      # push-ups excellent
        self.assertFalse(content.pushups_first(rows(None, 5), user), "nothing to compare with")
        self.assertFalse(content.pushups_first([], user))
        walker = {"gender": "F", "is_km": 1}
        self.assertFalse(content.pushups_first(rows(39000, 30), walker))   # walk passed (top of its scale), push-ups excellent
        self.assertTrue(content.pushups_first(rows(39000, 5), walker))

    def test_latin_words_are_isolated(self):
        self.assertEqual(content.isolate_latin("ריצת נפח (Zone 2)"), "ריצת נפח (\u2068Zone 2\u2069)")
        self.assertEqual(content.isolate_latin("ללא לטינית 15:40"), "ללא לטינית 15:40")


class Videos(ContentCase):
    def videos(self):
        at = self.go(self.signed_in(), "more")
        at.segmented_control(key="more_tab").set_value("videos").run()
        self.assertFalse(at.exception)
        return at

    def test_nothing_is_loaded_before_a_tap(self):
        at = self.videos()
        self.assertEqual(at.get("video"), [])
        self.assertEqual([b.label for b in at.button if b.key and b.key.startswith("video_play_")],
                         [t("btn_play_video")] * len(VIDEOS["cards"]))

    def test_tapping_play_loads_exactly_that_player_and_keeps_a_plain_link(self):
        at = self.videos()
        at.button(key="video_play_0").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.get("video")), 1)
        self.assertIn("IODxDxX7oi4", at.get("video")[0].proto.url)         # Streamlit turns the watch URL into an embed URL
        self.assertEqual([b.key for b in at.button if b.key and b.key.startswith("video_play_")], ["video_play_1"])
        links = [e.proto.url for e in at.get("link_button")]
        self.assertEqual(links, [c["url"] for c in VIDEOS["cards"]], "a plain link for every video, also when embeds are blocked")

    def test_the_privacy_line_names_the_video_host_once_it_is_known(self):
        self.assertIn("YouTube", all_text(self.videos()))


if __name__ == "__main__":
    unittest.main()
