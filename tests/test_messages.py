"""Copy lint (ported from the research pack, extended to the error catalogue and the tips) and Hebrew count phrases."""
import re
import string
import unittest

from baror import config, messages_he
from baror.errors import ALL_ERRORS, ConfigError, RateLimitedError, ValidationError
from baror.hebrew import count_phrase, duration_phrase, retry_phrase

# Singular gendered (masculine) imperatives: use the infinitive or the plural form instead.
DENY_IMPERATIVES = ["בחר", "הזן", "לחץ", "נסה", "היכנס", "ודא", "בדוק", "המתן", "הקלד", "שמור", "מלא", "המשך", "אשר",
                    "חזור", "פנה", "הוסף"]
# Exact phrases where a deny-listed word is not an imperative ("שם מלא" = full name, "המשך השימוש" = continued use).
ALLOWED_PHRASES = ("שם מלא", "המשך השימוש")
SLASH_GENDER = re.compile(r"[\u05d0-\u05ea]+[\u05d9]?/[\u05d0-\u05ea]")
HE = re.compile(r"[\u0590-\u05ff]")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d]")
SAMPLE = {"time": "05:00", "date": "12.10", "gap": "12 שניות", "wait": "30 שניות", "ok": 5, "total": 9,
          "name": "דנה", "scale": "זכר", "gender": "נקבה", "n": 7, "lo": 1, "hi": 120, "test": "ריצה", "status": "הושג הרף",
          "v": "15:40", "email": "a@example.com", "count": 3, "platoon": "מחלקה 1", "reads": 2, "label": "ריצה", "min": 1, "max": 60, "first": "15:40", "last": "14:50", "best": "14:30", "summary": "ב-3 בחנים", "start": "01/10/2026", "end": "01/12/2026", "old": "מחלקה 1", "new": "מחלקה 2", "title": "מ״מ"}
MAX_BTN = 18


def all_strings():
    for k, v in messages_he.M.items():
        yield f"M.{k}", v
    for k, m in messages_he.ERRORS.items():
        for part in ("title", "why", "action"):
            yield f"ERRORS.{k}.{part}", getattr(m, part)
    for name in ("TIPS_RUN", "TIPS_PUSHUPS", "TIPS_WEEK", "VIDEOS"):
        yield from _walk(name, getattr(messages_he, name))


def _walk(prefix, obj):
    """Every Hebrew string in a content dict (cards, bullets, table cells); icon names and URLs are not copy."""
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in ("icon", "url"):
                yield from _walk(f"{prefix}.{k}", v)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk(f"{prefix}.{i}", v)


def lint(strings=None):
    errs = []
    for k, v in (all_strings() if strings is None else strings.items()):
        if SLASH_GENDER.search(v):
            errs.append(f"{k}: slash gender form")
        if EMOJI.search(v):
            errs.append(f"{k}: emoji")
        if HE.search(v) and '"' in v:
            errs.append(f"{k}: straight double quote")
        checked = v
        for phrase in ALLOWED_PHRASES:
            checked = checked.replace(phrase, "")
        for w in re.findall(r"[\u05d0-\u05ea]+", checked):
            if w in DENY_IMPERATIVES:
                errs.append(f"{k}: gendered imperative {w}")
        for _, f, _, _ in string.Formatter().parse(v):
            if f and f not in SAMPLE:
                errs.append(f"{k}: param {f} has no supplied value")
        try:
            v.format(**SAMPLE)
        except (KeyError, IndexError, ValueError) as e:
            errs.append(f"{k}: format error {e!r}")
        if k.startswith("M.btn_") and len(v) > MAX_BTN:
            errs.append(f"{k}: button label {len(v)} > {MAX_BTN}")
    return errs


class Lint(unittest.TestCase):
    def test_all_copy_is_clean(self):
        self.assertEqual(lint(), [])

    def test_the_linter_detects_what_it_claims_to(self):
        self.assertEqual(lint({"x": "שם מלא"}), [])
        self.assertTrue(lint({"x": "יש למלא ולהמשיך. מלא את השדה"}))
        bad = {"a": "בחר/י אפשרות", "M.btn_x": "א" * 19, "c": "שלום 😀", "d": 'הוא אמר "שלום"', "e": "הזן מספר", "f": "ערך {zzz}"}
        errs = " ".join(lint(bad))
        for frag in ("slash", "button", "emoji", "quote", "imperative", "zzz"):
            self.assertIn(frag, errs)

    def test_no_guidance_only_notice(self):
        self.assertFalse([k for k in messages_he.M if "guidance" in k])
        self.assertFalse([v for v in messages_he.M.values() if "להכוונה בלבד" in v])

    def test_registration_refusal_is_generic_and_the_two_keys_say_the_same(self):
        for key in ("REGISTRATION_DENIED", "DUPLICATE_EMAIL"):
            m = messages_he.ERRORS[key]
            text = " ".join((m.title, m.why, m.action))
            for word in ("רשימ", "מורשה", "קיים", "כבר", "היתר"):
                self.assertNotIn(word, text, key)
        self.assertEqual(messages_he.ERRORS["REGISTRATION_DENIED"], messages_he.ERRORS["DUPLICATE_EMAIL"])

    def test_every_domain_error_key_has_a_message_or_degrades_safely(self):
        for cls in ALL_ERRORS:
            self.assertIn(messages_he.get_error(cls.message_key), messages_he.ERRORS.values(), cls.__name__)
        self.assertIs(messages_he.get_error("NO_SUCH_KEY"), messages_he.ERRORS["UNEXPECTED"])

    def test_error_keys_raised_by_the_domain_are_covered(self):
        keys = {"INVALID_PASSWORD", "INVALID_NAME", "INVALID_GENDER", "INVALID_EMAIL", "INVALID_TIME_FORMAT",
                "TIME_SECONDS_RANGE", "TIME_OUT_OF_RANGE", "TIME_ZERO", "EMPTY_RESULT", "INVALID_RESULT", "INVALID_DATE",
                "NOT_AUTHORISED", "COHORT_NOT_FOUND", "REGISTRATION_DENIED", "AUTH_FAILED", "RATE_LIMITED"}
        self.assertEqual({k for k in keys if k not in messages_he.ERRORS}, set())

    def test_render_exception_formats_waits_and_never_raises(self):
        text = messages_he.render_exception(RateLimitedError(params={"seconds": 130}))
        self.assertIn("3 דקות", text)
        self.assertIn("שתי דקות", messages_he.render_exception(RateLimitedError(params={"seconds": 61})))
        self.assertTrue(messages_he.render_exception(ValidationError("TIME_OUT_OF_RANGE")))   # missing params: no KeyError
        self.assertIn("abc123", messages_he.render_exception(ValidationError("INVALID_INPUT"), "abc123"))


class V9Terms(unittest.TestCase):
    """The company structure has fixed words: role = תפקיד, title = תואר, written with the Hebrew abbreviation mark."""

    ABBREVIATION = re.compile(r"(?:סמ|מ)[\"'”’׳]+[פמכ]")

    def test_titles_and_roles_have_names_and_use_the_abbreviation_mark(self):
        from baror.domain import roles
        m = messages_he.M
        self.assertEqual([m["title_" + t_] for t_ in ("mp", "smp", "mm", "cmdr")], ["מ״פ", "סמ״פ", "מ״מ", "מ״כ"])
        for title in roles.ALL_TITLES:
            self.assertIn("title_" + title, m)
        for role in roles.ROLES:
            self.assertIn("role_" + role, m)
        self.assertEqual(sorted(m["role_" + r] for r in roles.ROLES), sorted(["צוער", "מדריך", "ראש הקורס", "מנהל מערכת"]))

    def test_no_string_spells_an_abbreviation_with_a_straight_quote_or_apostrophe(self):
        bad = [k for k, v in all_strings() if self.ABBREVIATION.search(v)]
        self.assertEqual(bad, [])

    def test_the_title_errors_say_title_not_role(self):
        for key in ("TITLE_REQUIRED", "TITLE_TAKEN"):
            m = messages_he.ERRORS[key]
            text = " ".join((m.title, m.why, m.action))
            self.assertIn("תואר", text, key)
            self.assertNotIn("תפקיד", text, key)

    def test_every_v9_error_key_the_storage_raises_has_its_own_message(self):
        keys = {"ROLE_NOT_ALLOWED", "CHIEF_LIMIT", "TITLE_REQUIRED", "TITLE_TAKEN", "MM_TAKEN", "CMDR_LIMIT", "PLATOON_REQUIRED",
                "REQUEST_LIMIT", "CADET_NOT_REGISTERED", "ACCOUNT_CHANGED", "PLATOON_ALREADY_SET", "ADMIN_LIMIT", "LAST_ADMIN",
                "SIGNUP_BUSY", "REGISTRATION_CLOSED", "REPORT_RANGE_ORDER", "REPORT_RANGE_FUTURE", "REPORT_RANGE_TOO_LONG"}
        self.assertEqual({k for k in keys if k not in messages_he.ERRORS}, set())

    def test_grant_dialogs_ask_for_the_same_identity_check_in_the_same_words(self):
        m = messages_he.M
        self.assertEqual(m["adm_verified"], "הזהות אומתה מחוץ למערכת")
        self.assertEqual(m["grant_verified"], m["adm_verified"])


class Plural(unittest.TestCase):
    def test_counts(self):
        cases = {("second", 1): "שנייה אחת", ("second", 2): "שתי שניות", ("second", 11): "11 שניות",
                 ("minute", 1): "דקה אחת", ("minute", 2): "שתי דקות", ("rep", 1): "חזרה אחת", ("rep", 2): "שתי חזרות",
                 ("rep", 21): "21 חזרות", ("day", 1): "יום אחד", ("day", 2): "יומיים", ("second", 0): "0 שניות"}
        for (u, n), want in cases.items():
            self.assertEqual(count_phrase(u, n), want)

    def test_duration_and_retry(self):
        self.assertEqual(duration_phrase(190), "3 דקות ו-10 שניות")
        self.assertEqual(duration_phrase(12), "12 שניות")
        self.assertEqual(duration_phrase(62), "דקה אחת ושתי שניות")
        self.assertEqual((retry_phrase(30), retry_phrase(61)), ("30 שניות", "שתי דקות"))

    def test_gap_is_never_clock_format(self):
        self.assertNotRegex(messages_he.t("fail_with_gap", gap=duration_phrase(190)), r"\d:\d")


class Config(unittest.TestCase):
    def test_defaults(self):
        c = config.from_env({})
        self.assertEqual((str(c.db_path), c.session_days, c.tz_name, c.cohort_views), ("/data/baror.db", 14, "Asia/Jerusalem", False))
        self.assertEqual(c.tz.key, "Asia/Jerusalem")

    def test_valid_values(self):
        c = config.from_env({"DB_PATH": "/tmp/x.db", "SESSION_DAYS": "30", "BAROR_COHORT_VIEWS": "1"})
        self.assertEqual((str(c.db_path), c.session_days, c.cohort_views), ("/tmp/x.db", 30, True))

    def test_invalid_values_fail_fast_with_a_config_error(self):
        for env in ({"SESSION_DAYS": "x"}, {"SESSION_DAYS": "0"}, {"SESSION_DAYS": "91"}, {"TZ_NAME": "Mars/Base"},
                    {"BAROR_COHORT_VIEWS": "yes"}, {"BAROR_COHORT_VIEWS": "2"}):
            with self.assertRaises(ConfigError, msg=str(env)):
                config.from_env(env)

    def test_cohort_views_default_off_and_blank_is_off(self):
        self.assertFalse(config.from_env({"BAROR_COHORT_VIEWS": ""}).cohort_views)


if __name__ == "__main__":
    unittest.main()
