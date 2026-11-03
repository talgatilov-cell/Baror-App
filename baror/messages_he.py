"""Every user-facing Hebrew string (CLAUDE.md rule). Draft copy deck from the research pack: NEEDS native-speaker review.

Register: formal, impersonal (noun / infinitive / plural imperative), no slash gender forms, no singular gendered
imperatives, no emoji in headings, no blame. Placeholders use {braces}; count phrases come from ``baror.hebrew``.
tests/test_messages.py lints this file (slash forms, emoji, straight quotes, singular imperatives, formatting).

Two catalogues:
* ``M``       UI strings, looked up with ``t(key, **params)``.
* ``ERRORS``  what a ``BarorError`` shows (title / why / action), looked up through its ``message_key``;
              unknown keys degrade to ``UNEXPECTED`` instead of raising inside an error handler.
Never interpolate user-typed text into a message that is rendered as Markdown (see ``baror.ui.text.esc``).
"""
from __future__ import annotations

from dataclasses import dataclass

from baror.hebrew import count_phrase, duration_phrase, retry_phrase  # noqa: F401

APP_NAME = "בר-אור"

M = {
    # app
    "app_title": "דשבורד כשירות", "app_tagline": "מערכת מעקב ושיפור ביצועי בר-אור לצוערים.",
    "page_title": "דשבורד בר-אור לקורס קצינים",
    # navigation
    "nav_title": "ניווט פלוגתי", "nav_label": "יעד ניווט",
    "nav_personal": "האזור האישי", "nav_tips_run": "טיפים לריצה",
    "nav_tips_pushups": "טיפים לשכיבות סמיכה", "nav_videos": "סרטוני הדרכה",
    "nav_platoon": "מבט מחלקתי", "nav_chief": "סיכום פיקודי",
    # login
    "login_title": "כניסה", "field_email": "אימייל", "field_password": "סיסמה",
    "btn_login": "כניסה", "btn_logout": "יציאה", "account_label": "חשבון", "logged_out": "ההתחברות הסתיימה. אפשר לסגור את החלון או לרענן כדי להיכנס שוב.",
    # registration (allowlist): one generic refusal, never reveals listing
    "register_title": "הרשמה", "btn_register": "יצירת חשבון",
    "register_intro": "הרשמה לצוערים שנוספו לרשימת הקורס.",
    "register_ok": "החשבון נוצר.",
    "password_rule": "הסיסמה חייבת לכלול לפחות 8 תווים.",
    "password_confirm_mismatch": "הסיסמאות אינן זהות.",
    "field_password_confirm": "אימות סיסמה",
    "field_name": "שם מלא", "field_gender": "מגדר (קובע רף)", "choose_placeholder": "יש לבחור",
    "gender_male": "זכר", "gender_female": "נקבה",
    "field_km": "יש לי פטור ריצה (כ.מ) - יעד הליכה 6 ק״מ",
    "err_gender_missing": "יש לבחור מגדר.",
    # forced / voluntary password change
    "pw_change_title": "החלפת סיסמה", "pw_change_body": "יש לבחור סיסמה חדשה לפני המשך השימוש.",
    "field_password_current": "סיסמה נוכחית", "field_password_new": "סיסמה חדשה",
    "btn_change_password": "החלפת סיסמה", "pw_changed": "הסיסמה הוחלפה.",
    # personal area
    "greeting": "שלום, {name}", "scale_line": "המדדים מחושבים לפי רף: {scale}",
    "scale_male": "זכר", "scale_female": "נקבה", "scale_walk": "{gender} (כ.מ - הליכה 6 ק״מ)",
    "trend_title": "מגמת ההתקדמות", "history_title": "היסטוריית תוצאות",
    "chart_run": "זמן ריצה בדקות (מהיר יותר למעלה)", "chart_walk": "זמן הליכה בדקות (מהיר יותר למעלה)",
    "chart_pushups": "שכיבות סמיכה (גבוה = טוב)",
    "axis_date": "תאריך", "axis_minutes": "דקות", "axis_reps": "חזרות",
    "add_title": "הזנת תוצאות בוחן חדש",
    "field_run_time_run": "זמן ריצה 3 ק״מ (למשל 15:40)",
    "field_run_time_walk": "זמן הליכה 6 ק״מ (יעד: עד 66:00)",
    "field_date": "תאריך הבוחן", "field_pushups": "שכיבות סמיכה (כמות)",
    "btn_save_result": "שמירת תוצאה",
    "input_kept": "הערכים שהוזנו נשמרו בטופס.",
    "warn_zero_pushups": "הוזנו 0 שכיבות סמיכה. הערך יישמר כתוצאה בפועל.",
    # result card (each test is graded on its own; there is no overall verdict)
    "saved": "התוצאה נשמרה",
    "test_run": "ריצה", "test_walk": "הליכה", "test_pushups": "שכיבות סמיכה",
    "status_line": "{test}: {status}",
    "status_excellent": "הצטיינות", "status_pass": "הושג הרף", "status_fail": "טרם הושג הרף",
    "fail_with_gap": "טרם הושג הרף — חסרות {gap}", "fail_with_gap_one": "טרם הושג הרף — חסרה {gap}",
    "gap_to_excellent": "להצטיינות חסרות {gap}", "gap_to_excellent_one": "להצטיינות חסרה {gap}",
    "at_top": "הושגה רמת ההצטיינות",
    "status_ungraded": "הערך חורג מהטווח הצפוי ולא דורג",
    "result_warning": "הערך נראה חריג. יש לוודא שהוזן נכון.",
    # empty states
    "empty_summary_title": "עוד אין תוצאות",
    "empty_summary_body": "לאחר הבוחן אפשר להזין זמן ושכיבות סמיכה. כאן יוצגו הגרפים וההתקדמות.",
    # instructor and chief dashboards
    "platoon_title": "מבט מחלקתי",
    "platoon_banner": "מוצגות תוצאות אישיות של צוערים. התצוגה לקריאה בלבד, והגישה אליה נרשמת ביומן.",
    "platoon_none": "אין מחלקות שהוענקו לחשבון הזה.",
    "chief_title": "סיכום פיקודי",
    "chief_banner": "מוצגים נתונים מצטברים בלבד. פתיחת תוצאות של צוער בשמו נרשמת ביומן.",
    "group_too_small": "קבוצות של פחות מ-5 צוערים אינן מוצגות.",
    "group_hidden": "הקבוצה אינה מוצגת (פחות מ-5 צוערים).",
    "pick_platoon": "בחירת מחלקה", "pick_cadet": "בחירת צוער", "btn_open_cadet": "פתיחת התוצאות",
    "unassigned": "ללא מחלקה", "cadets_count": "{n} צוערים",
    "col_name": "שם", "col_platoon": "מחלקה", "col_date": "תאריך",
    "col_run": "ריצה אחרונה", "col_pushups": "שכיבות אחרונות", "col_best_run": "שיא ריצה",
    "col_best_pushups": "שיא שכיבות", "col_metric": "מדד", "col_results": "תוצאות", "col_status": "סטטוס",
    "col_excellent": "הצטיינות", "col_pass": "הושג הרף", "col_fail": "טרם הושג הרף", "col_n": "מספר",
    "metric_run": "ריצה 3 ק״מ", "metric_walk": "הליכה 6 ק״מ", "metric_pushups": "שכיבות סמיכה",
    "total_row": "סך הכול (קבוצות מוצגות בלבד)",
    "no_results_cadet": "אין תוצאות לצוער הזה.",
    # rate limit, bot check, session, storage
    "rate_limited": "בוצעו יותר מדי ניסיונות. נסו שוב בעוד {wait}.",
    "bot_check_failed": "בדיקת האבטחה לא הושלמה. יש לנסות שוב.",
    "session_expired": "פג תוקף ההתחברות. יש להיכנס מחדש. תוצאות שנשמרו קודם לא נפגעו.",
    "btn_retry": "ניסיון נוסף", "btn_refresh": "רענון",
}

# Tips and videos: copied from the original app (wording unchanged except that the straight quote in the unit sign
# became the Hebrew abbreviation mark).
TIPS_RUN = {
    "title": "איך לשפר את זמני הריצה?",
    "intro": "ריצת 3 קילומטרים דורשת שילוב של מהירות, סיבולת לב-ריאה וחוסן מנטלי. הנה שיטות עבודה מוכחות:",
    "items": [
        ("1. אימוני אינטרוולים (הפוגות)",
         "מומלץ לשלב פעם בשבוע: רוצים לשפר מהירות? תרוצו מהר. רוצו 400 מטר בקצב גבוה (סביב 85% יכולת), ואז 200 מטר של הליכה קלה להתאוששות. חזרו על זה 6-8 פעמים."),
        ("2. ריצת נפח (Zone 2)",
         "אימון אחד בשבוע צריך להיות מוקדש לריצה ארוכה ואיטית. המטרה היא לרוץ 5-7 קילומטרים בקצב שמאפשר לכם לנהל שיחה קלה. זה בונה את התשתית האירובית ומלמד את הגוף לייעל את צריכת החמצן."),
        ("3. חימום לפני הבוחן",
         "אל תזנקו מהמקום. 10 דקות לפני הזינוק בצעו ריצה קלה מאוד, ולאחריה מתיחות דינמיות כמו הנפות רגליים, מכרעים (לאנג'ים) וסיבובי אגן. זה מונע את תחושת ה'רגליים הכבדות' בקילומטר הראשון."),
    ],
}
TIPS_PUSHUPS = {
    "title": "תוכנית פעולה לשכיבות סמיכה",
    "intro": "הסוד בשכיבות סמיכה הוא עקביות וטכניקה נכונה. עבודה נכונה תמנע פציעות ותגדיל את כמות החזרות.",
    "items": [
        ("1. עבודה על השלב האקצנטרי (הירידה)",
         "השריר נבנה לא רק בדחיפה אלא גם בבלימה. נסו לרדת לקרקע בספירה איטית של 3 שניות, ולדחוף למעלה בשנייה אחת. אם קשה לכם לדחוף, רדו לאט לקרקע, ודחפו עם הברכיים."),
        ("2. שיטת ה'Grease the Groove'",
         "במקום לעשות אימון אחד קורע פעם ביומיים, עשו סטים קטנים וקלים לאורך כל היום. למשל: כל פעם שנכנסים למגורים, עושים 5-10 שכיבות סמיכה. זה בונה חיבור עצבי חזק לשריר בלי לעייף אותו לחלוטין."),
        ("3. מיקום ידיים ומרפקים",
         "שמרו את המרפקים קרובים לגוף (בזווית של כ-45 מעלות). אל תפתחו אותם החוצה ל-90 מעלות – זה שם עומס מסוכן על הכתפיים. כיווץ ישבן ובטן ישמור על גב ישר כמו פלס וימנע בזבוז אנרגיה."),
    ],
}
VIDEOS = {
    "title": "סרטוני אימון וטכניקה",
    "intro": "ריכזנו עבורכם סרטונים מומלצים שיעזרו לדייק את הטכניקה ולשפר תוצאות לקראת הבוחן:",
    "items": [
        ("טכניקה תקנית לשכיבות סמיכה", "https://www.youtube.com/watch?v=IODxDxX7oi4"),
        ("דגשים לריצת 3 ק״מ ושיפור טכניקת ריצה", "https://www.youtube.com/watch?v=9L2b2khySLE"),
    ],
}


def t(key: str, **params) -> str:
    return M[key].format(**params)


# ---------------------------------------------------------------------------------------------------------- errors
@dataclass(frozen=True)
class Msg:
    title: str     # what happened
    why: str       # why (one short sentence; may be empty)
    action: str    # what to do next


ERRORS: dict[str, Msg] = {
    # authentication
    "AUTH_FAILED": Msg("ההתחברות לא הצליחה", "האימייל והסיסמה אינם תואמים לחשבון פעיל.", "בדקו את הפרטים ונסו שוב."),
    "RATE_LIMITED": Msg("בוצעו הרבה ניסיונות כניסה", "כדי להגן על החשבון עצרנו זמנית את אפשרות ההתחברות.",
                        "אפשר לנסות שוב בעוד {wait}."),
    "SESSION_EXPIRED": Msg("ההתחברות פגה", "לא הייתה פעילות זמן מה, ולכן ניתקנו אתכם כדי להגן על החשבון.",
                           "היכנסו שוב כדי להמשיך. כל מה ששמרתם קודם נשמר."),
    "ACCOUNT_NOT_FOUND": Msg("לא מצאנו את החשבון", "ייתכן שהוא נמחק או שהנתונים עודכנו.", "היכנסו מחדש."),
    "NOT_AUTHORISED": Msg("אין הרשאה לפעולה הזו", "", "אפשר לחזור לאזור האישי."),
    "COHORT_NOT_FOUND": Msg("הנתונים המבוקשים אינם זמינים", "", "אפשר לבחור פריט אחר מהרשימה."),
    # registration: ONE message for every refusal (listed or not, used or not, malformed or not)
    "REGISTRATION_DENIED": Msg("לא ניתן להשלים את ההרשמה עם הפרטים שהוזנו", "",
                               "אם הבעיה נמשכת, יש לפנות לצוות ההדרכה."),
    "DUPLICATE_EMAIL": Msg("לא ניתן להשלים את ההרשמה עם הפרטים שהוזנו", "",
                           "אם הבעיה נמשכת, יש לפנות לצוות ההדרכה."),
    # validation
    "INVALID_PASSWORD": Msg("הסיסמה אינה מתאימה", "נדרשים בין 8 ל-128 תווים.", "בחרו סיסמה אחרת ונסו שוב."),
    "PASSWORD_UNCHANGED": Msg("הסיסמה החדשה זהה לנוכחית", "", "בחרו סיסמה שונה מהנוכחית."),
    "INVALID_NAME": Msg("השם אינו תקין", "השם צריך להכיל בין תו אחד ל-80 תווים.", "תקנו את השדה ונסו שוב."),
    "INVALID_GENDER": Msg("חסר מגדר", "המגדר קובע את הרף שלפיו מחושבות התוצאות.", "יש לבחור מגדר."),
    "INVALID_EMAIL": Msg("לא הצלחנו להבין את כתובת האימייל", "היא צריכה להיראות כך: name@example.com.",
                         "תקנו את הכתובת ונסו שוב."),
    "INVALID_TIME_FORMAT": Msg("לא הצלחנו לקרוא את הזמן", "הזמן צריך להיכתב כדקות:שניות, למשל 14:30.",
                               "תקנו את השדה ושמרו שוב. שאר הפרטים בטופס נשמרו."),
    "TIME_SECONDS_RANGE": Msg("השניות מחוץ לטווח", "השניות צריכות להיות בין 00 ל-59.", "תקנו את השדה ושמרו שוב."),
    "TIME_OUT_OF_RANGE": Msg("הזמן נראה חריג", "הזמן צריך להיות בין {lo} ל-{hi} דקות.", "בדקו את הזמן ושמרו שוב."),
    "TIME_ZERO": Msg("הזמן חייב להיות גדול מ-00:00", "", "תקנו את השדה ושמרו שוב."),
    "FUTURE_DATE": Msg("לא ניתן להזין תאריך עתידי", "", "יש לבחור את תאריך הבוחן."),
    "EMPTY_RESULT": Msg("אין מה לשמור", "לא הוזנה אף תוצאה.", "יש להזין זמן או שכיבות סמיכה."),
    "INVALID_RESULT": Msg("אחד הערכים מחוץ לטווח", "", "בדקו את הערכים ושמרו שוב."),
    "INVALID_DATE": Msg("התאריך אינו תקין", "", "תקנו את התאריך ושמרו שוב."),
    "INVALID_INPUT": Msg("אחד הפרטים לא התקבל", "", "בדקו את הטופס ונסו שוב."),
    # storage and service state
    "STORAGE_UNAVAILABLE": Msg("לא הצלחנו לטעון או לשמור את הנתונים", "יש תקלה זמנית באחסון. הנתונים שלכם לא נמחקו.",
                               "המתינו רגע ונסו שוב. אם זה חוזר, פנו לצוות הקורס וציינו את קוד התקלה."),
    "SAVE_FAILED": Msg("התוצאה לא נשמרה", "הייתה תקלה בשמירה, והנתונים הקודמים שלכם שלמים.",
                       "מה שהזנתם עדיין בטופס. אפשר ללחוץ שוב על שמירה."),
    "DATA_CORRUPTED": Msg("קובץ הנתונים אינו קריא כרגע", "ייתכן שהוא נפגע. לא שינינו בו דבר, כדי לא לאבד מידע.",
                          "פנו לצוות הקורס עם קוד התקלה. אפשר לשחזר מגיבוי."),
    "MAINTENANCE": Msg("המערכת בעבודות תחזוקה", "אנחנו מעדכנים אותה, והנתונים שלכם שמורים.",
                       "נסו שוב בעוד כמה דקות."),
    "SERVICE_UNAVAILABLE": Msg("השירות לא זמין כרגע", "המערכת לא הצליחה להיפתח כראוי.",
                               "נסו שוב בעוד כמה דקות. אם זה נמשך, פנו לצוות הקורס עם קוד התקלה."),
    "UNEXPECTED": Msg("משהו השתבש אצלנו", "זו לא אשמתכם, ולא הצלחנו להשלים את הפעולה.",
                      "נסו לרענן את הדף. אם זה חוזר, שלחו לצוות הקורס את קוד התקלה."),
}
INCIDENT_LABEL = "קוד תקלה"


def get_error(key: str) -> Msg:
    """Unknown keys degrade to UNEXPECTED instead of raising inside an error handler."""
    return ERRORS.get(key) or ERRORS["UNEXPECTED"]


def render_error(key: str, **params) -> str:
    """Markdown for st.error: bold title, then why and action. Only numbers and fixed labels may be params."""
    m = get_error(key)
    try:
        why, action = m.why.format(**params), m.action.format(**params)
    except (KeyError, IndexError):   # a missing param must never crash the handler
        why, action = m.why.split("{")[0].rstrip(" :"), m.action.split("{")[0].rstrip(" :")
    body = " ".join(p for p in (why, action) if p)
    return f"**{m.title}**\n\n{body}" if body else f"**{m.title}**"


def render_exception(exc, incident_id: str | None = None) -> str:
    """Text for a BarorError: params come from the exception (numbers only); a wait in seconds becomes a phrase."""
    params = dict(getattr(exc, "params", {}) or {})
    if isinstance(params.get("seconds"), int):
        params["wait"] = retry_phrase(params["seconds"])
    text = render_error(getattr(exc, "message_key", "UNEXPECTED"), **params)
    if incident_id:
        text += f"\n\n{INCIDENT_LABEL}: `{incident_id}`"   # backticks: shown LTR/monospace inside RTL text
    return text


def gap_phrase(seconds: int) -> str:
    return duration_phrase(seconds)
