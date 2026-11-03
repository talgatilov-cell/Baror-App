"""Every colour pair that carries text or a control, computed (WCAG 2.x relative luminance): text needs 4.5:1,
interface parts and chart lines 3:1. The tokens are read from static/app.css (:root) and .streamlit/config.toml, so a
changed token that breaks a pair fails here. Sunlight performance is NOT covered (untested outdoors)."""
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
THEME = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["theme"]


def token(name: str) -> str:
    return re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", CSS).group(1)


def luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


WHITE = "#ffffff"
TEXT_PAIRS = [   # (foreground, background, label): >= 4.5
    ("ink", "bg", "body text on sand"), ("ink", "bg2", "text on input background"),
    ("muted", "bg", "caption on sand"), ("muted", "bg2", "caption on input background"),
    ("olive", "bg", "olive text (read-back) on sand"), ("olive", "bg2", "olive on input background"),
    ("danger", "danger-bg", "field error"), ("danger", "bg", "danger on sand"),
    ("good", "good-bg", "success text on tint"), ("good", "bg", "success on sand"),
    ("warn", "warn-bg", "warning text on tint"),
]
UI_PAIRS = [     # >= 3
    ("line", "bg", "control border on sand"), ("line", "bg2", "control border on input background"),
    ("olive", WHITE, "chart line on white"), ("olive-dark", "bg", "focus ring on sand"),
]


def blend(fg_hex: str, alpha: float, under_hex: str) -> str:
    f = [int(fg_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    u = [int(under_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(a * alpha + b * (1 - alpha)):02x}" for a, b in zip(f, u))


class CardOverThePicture(unittest.TestCase):
    """Text sits on the content card, never on the camouflage picture. The card is sand at ALPHA over the picture; the
    worst case is any pixel at all, so the check uses pure white and pure black underneath (image independent)."""

    ALPHA = float(re.search(r"stMainBlockContainer\"\]\{\s*background:rgba\(246,\s*244,\s*236,\s*([0-9.]+)\)", CSS).group(1))

    def test_the_card_is_sand_and_nearly_opaque(self):
        self.assertEqual(token("bg"), "#f6f4ec")
        self.assertGreaterEqual(self.ALPHA, 0.9)

    def test_every_text_pair_holds_over_the_worst_pixel(self):
        for under in ("#000000", "#ffffff"):
            card = blend("#f6f4ec", self.ALPHA, under)
            for fg in ("ink", "muted", "olive"):
                self.assertGreaterEqual(ratio(token(fg), card), 4.5, f"{fg} on the card over {under}")
            self.assertGreaterEqual(ratio(token("line"), card), 3.0, f"control border over {under}")

    def test_bars_are_opaque_sand(self):
        code = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        for selector in (".st-key-bottom_nav", ".st-key-top_bar"):
            block = re.search(re.escape(selector) + r"\{([^}]*)\}", code).group(1)
            self.assertIn("background:var(--bg)", block.replace(" ", ""), selector)


class Contrast(unittest.TestCase):
    def colour(self, name):
        return name if name.startswith("#") else token(name)

    def test_text_pairs(self):
        for fg, bg, label in TEXT_PAIRS:
            self.assertGreaterEqual(ratio(self.colour(fg), self.colour(bg)), 4.5, label)

    def test_white_on_olive_buttons(self):
        for name in ("olive", "olive-dark"):
            self.assertGreaterEqual(ratio(WHITE, token(name)), 4.5, name)

    def test_interface_pairs(self):
        for fg, bg, label in UI_PAIRS:
            self.assertGreaterEqual(ratio(self.colour(fg), self.colour(bg)), 3.0, label)

    def test_css_tokens_and_streamlit_theme_agree(self):
        self.assertEqual(token("olive"), THEME["primaryColor"])
        self.assertEqual(token("bg"), THEME["backgroundColor"])
        self.assertEqual(token("bg2"), THEME["secondaryBackgroundColor"])
        self.assertEqual(token("ink"), THEME["textColor"])
        self.assertEqual(token("line"), THEME["borderColor"])
        self.assertEqual(token("danger"), THEME["redTextColor"])
        self.assertEqual(token("good"), THEME["greenTextColor"])
        self.assertEqual(token("warn"), THEME["orangeTextColor"])

    def test_theme_badge_text_colours_pass_on_their_tints(self):
        for text, bg in (("redTextColor", "redBackgroundColor"), ("greenTextColor", "greenBackgroundColor"),
                         ("orangeTextColor", "orangeBackgroundColor"), ("grayTextColor", "grayBackgroundColor")):
            self.assertGreaterEqual(ratio(THEME[text], THEME[bg]), 4.5, text)

    def test_known_values_match_the_report(self):
        self.assertAlmostEqual(ratio(token("ink"), token("bg")), 13.58, places=2)
        self.assertAlmostEqual(ratio(WHITE, token("olive")), 7.25, places=2)


if __name__ == "__main__":
    unittest.main()
