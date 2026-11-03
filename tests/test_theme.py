"""Regression for a bug only a real browser showed: the dark sign-in style block stayed active after signing in (white
headings on a light box), because removing a Streamlit <style> element does not remove its rules. The looks are now
scoped by the sign-in container's class, so they cannot outlive it. AppTest cannot see CSS, so this checks the source."""
import re
import unittest
from pathlib import Path

from baror.ui import theme

ROOT = Path(__file__).resolve().parent.parent


class ThemeScoping(unittest.TestCase):
    def setUp(self):
        self.css = theme._CSS.format(bg="", i=theme._IN, o=theme._OUT)

    def selectors(self):
        out = []
        for block in re.findall(r"([^{}]+)\{[^{}]*\}", re.sub(r"@media[^{]*\{", "", self.css)):
            out += [s.strip() for s in block.split(",") if s.strip()]
        return out

    def test_every_look_specific_selector_is_scoped_by_the_signin_container(self):
        unscoped = [s for s in self.selectors() if not s.startswith((theme._IN, theme._OUT))]
        self.assertEqual(sorted(unscoped), sorted([".stApp", ".stButton>button", ".stButton>button:hover"]))

    def test_the_two_looks_are_mutually_exclusive(self):
        self.assertEqual(theme._IN, ".stApp:has(.st-key-signin)")
        self.assertEqual(theme._OUT, ".stApp:not(:has(.st-key-signin))")

    def test_the_sign_in_page_lives_in_the_container_the_css_keys_on(self):
        self.assertIn('st.container(key="signin")', (ROOT / "app.py").read_text(encoding="utf-8"))

    def test_the_style_is_static_and_carries_no_user_input(self):
        self.assertNotIn("{name}", self.css)
        self.assertEqual(re.findall(r"\{[a-z_]+\}", self.css), [])


if __name__ == "__main__":
    unittest.main()
