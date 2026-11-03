"""The look is one static stylesheet plus a language/direction script. AppTest cannot see CSS, so these check the SOURCE
against the rules of CLAUDE.md, and tests/test_design_tokens.py computes the contrast of every colour pair."""
import re
import unittest
from pathlib import Path

from baror.ui import theme

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
CODE = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)       # the rules without their comments


def rules(css: str):
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", body)


class StylesheetRules(unittest.TestCase):
    def test_no_hashed_emotion_classes(self):
        self.assertNotIn("st-emotion-cache", CODE)
        self.assertIsNone(re.search(r"\.css-[0-9a-z]{5,}", CODE))

    def test_no_important_flags(self):
        self.assertNotIn("!important", CODE, "every override must win by specificity or source order")

    def test_logical_properties_only(self):
        physical = re.findall(r"(?<![-\w])(margin|padding|border)-(left|right)\s*:|(?<![-\w])(left|right)\s*:|text-align\s*:\s*(left|right)",
                              re.sub(r"/\*.*?\*/", "", CSS, flags=re.S))
        self.assertEqual(physical, [], "use inline/block/start/end properties")

    def test_no_angle_brackets_that_could_break_the_style_element(self):
        self.assertNotIn("<", CSS, "a '<' inside the stylesheet (even in a comment) can end the style element early")

    def test_custom_selectors_use_the_documented_hooks(self):
        for selector, _ in rules(CSS):
            for part in selector.split(","):
                part = part.strip()
                if part.startswith(("@", ":root", "html", "from", "to")) or not part:
                    continue
                self.assertTrue(re.search(r"\.st-key-|st-key-|\[data-testid|\[role=|\.stButton|\.stFormSubmitButton|\.stDownloadButton|\.stApp|\.chip|\.readback|\.field-|\.brand|button\[|\.coh|\.card|\.kpi|\.tip|\.vid|\.cohort|\.staff|\.chart|\.legend|\.dash",
                                          part), part)

    def test_touch_target_token_is_48(self):
        self.assertIn("--tap:48px", CSS.replace(" ", ""))

    def test_honeypot_stays_off_screen_but_is_not_display_none(self):
        block = re.search(r"\.st-key-hp_login[^{]*\{([^}]*)\}", CSS).group(1)
        self.assertIn("-10000px", block)
        self.assertNotIn("display:none", block.replace(" ", ""))


class OriginalAssets(unittest.TestCase):
    """The original camouflage picture and logo are served from static/ (byte-identical to the repository-root
    originals, so nobody edits one copy and forgets the other)."""

    def test_picture_is_referenced_by_its_static_url_and_never_embedded(self):
        self.assertIn("url(app/static/image_bea22f.jpg)", CSS)
        self.assertNotIn("base64", CODE)
        self.assertNotIn("data:image", CODE)
        self.assertNotIn("base64", (ROOT / "baror" / "ui" / "theme.py").read_text(encoding="utf-8").replace("never base64", ""))

    def test_picture_sits_on_a_fixed_layer_not_on_background_attachment(self):
        self.assertIn(".stApp::before{content:\"\"; position:fixed; inset:0; z-index:-1;", CSS)
        self.assertNotIn("background-attachment", CODE)

    def test_copies_are_byte_identical_to_the_originals(self):
        for name in ("image_bea22f.jpg", "logo.png"):
            self.assertEqual((ROOT / "static" / name).read_bytes(), (ROOT / name).read_bytes(), name)

    def test_logo_is_the_original_png_everywhere(self):
        for path in ("app.py", "baror/ui/shell.py"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("app/static/logo.png", text, path)
            self.assertNotIn("logo.svg", text, path)
        self.assertFalse((ROOT / "static" / "logo.svg").exists())


class RightToLeftDetails(unittest.TestCase):
    def test_table_cells_start_on_the_right_and_the_table_fills_the_card(self):
        flat = CODE.replace(" ", "").replace("\n", "")
        self.assertIn('[data-testid="stTable"]th,[data-testid="stTable"]td{text-align:start}', flat)
        self.assertIn('[data-testid="stTable"]table{width:100%}', flat)

    def test_bullet_markers_hang_inside_the_start_padding(self):
        self.assertIn("padding-inline:1.25rem 0", CODE)

    def test_the_drop_downs_are_select_only_so_no_on_screen_keyboard_opens(self):
        source = (ROOT / "baror" / "ui" / "personal.py").read_text(encoding="utf-8")
        self.assertIn("filter_mode=None", source)
        for name in ("add_min", "add_sec", "add_push"):
            self.assertIn(f'"{name}"', source)
        self.assertNotIn("st.number_input", source)

    def test_the_options_of_a_drop_down_are_48_px_targets(self):
        self.assertIn('[role="listbox"] [role="option"]{min-height:var(--tap)', CODE.replace("\n", " ").replace("  ", " "))

    def test_no_json_word_is_shown_to_people(self):
        from baror import messages_he
        for key, value in messages_he.M.items():
            self.assertNotRegex(value, "(?i)json", key)


class Wiring(unittest.TestCase):
    def test_language_and_direction_are_set_by_a_fixed_script(self):
        self.assertEqual(theme._BOOT, "<script>const d=document.documentElement; d.lang='he'; d.dir='rtl';</script>")
        self.assertIn("html{direction:rtl}", CSS.replace(" ", "").replace("html{direction:rtl}", "html{direction:rtl}"))

    def test_the_stylesheet_is_static_and_read_from_the_repository_root(self):
        self.assertEqual(theme.CSS_PATH, ROOT / "static" / "app.css")
        self.assertNotRegex(theme.css_text(), r"\{[a-z_]+\}\s*;")      # no format placeholders: nothing is interpolated

    def test_sign_in_page_lives_in_the_container_the_css_keys_on(self):
        self.assertIn('st.container(key="signin")', (ROOT / "app.py").read_text(encoding="utf-8"))
        self.assertIn(".st-key-signin", CSS)

    def test_font_is_self_hosted_with_its_licence(self):
        cfg = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('url = "app/static/fonts/Heebo-VF-subset.woff"', cfg)
        self.assertNotIn("googleapis", cfg + CSS)
        self.assertTrue((ROOT / "static" / "fonts" / "OFL.txt").exists())
        self.assertTrue((ROOT / "static" / "fonts" / "Heebo-VF-subset.woff").exists())


if __name__ == "__main__":
    unittest.main()
