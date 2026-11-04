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
    # app (one product name everywhere)
    "app_title": "בר-אור", "app_tagline": "מעקב כשירות לצוערים",
    # TEMPORARY demo switch (baror/ui/demo_switch.py): to be removed after the class demo
    "demo_switch_label": "מצב הדגמה",
    "demo_switch_help": "פותח מסד נתונים נפרד עם צוערים בדויים. המסד האמיתי אינו נפגע.",
    "demo_accounts_intro": "חשבונות להדגמה. כל הכתובות מסתיימות ב-@example.com והסיסמה זהה לכולם:",
    "demo_accounts_list": "צוערים: empty, one, two, twelve, walk. מדריך: ins. ראש הקורס: chief. מנהל מערכת: admin.",
    "demo_banner": "מצב הדגמה: כל הנתונים בדויים. כדי לחזור לאפליקציה האמיתית יוצאים ומכבים את מצב ההדגמה בדף הכניסה.",
    "page_title": "בר-אור | מעקב כשירות",
    # navigation: bottom bar, noun labels
    "nav_label": "ניווט", "nav_summary": "סיכום", "nav_add": "הזנה", "nav_history": "היסטוריה", "nav_more": "עוד",
    "nav_platoon": "מחלקה", "nav_chief": "סיכום פיקודי", "nav_requests": "בקשות",
    "more_tab_reports": "דוחות", "more_tab_accounts": "חשבונות", "adm_new_platoon_expander": "מחלקה חדשה",
    "req_acc_card_title": "בקשת הקלה או פטור",
    # login
    "login_title": "כניסה", "field_email": "אימייל", "field_password": "סיסמה",
    "btn_login": "כניסה", "btn_logout": "יציאה", "account_label": "חשבון", "logged_out": "ההתחברות הסתיימה. אפשר לסגור את החלון או לרענן כדי להיכנס שוב.",
    # registration (open; every new account is a cadet): one generic refusal, never reveals whether an account exists
    "register_title": "הרשמה", "btn_register": "יצירת חשבון",
    "register_ok": "החשבון נוצר.",
    "password_rule": "הסיסמה חייבת לכלול לפחות 8 תווים.",
    "password_confirm_mismatch": "הסיסמאות אינן זהות.",
    "field_password_confirm": "אימות סיסמה",
    "field_name": "שם מלא", "field_gender": "מגדר (קובע רף)", "choose_placeholder": "יש לבחור",
    "gender_male": "זכר", "gender_female": "נקבה",
    "field_km": "יש לי פטור ריצה (כ.מ) - יעד הליכה 6 ק״מ",
    "err_gender_missing": "יש לבחור מגדר.",
    # cadet notice about the instructor view (owner decision: instructors read their own platoon; every view is logged)
    "cadet_view_notice": "מדריכים ומדריכות במחלקה יכולים לצפות בתוצאות. כל צפייה נרשמת ביומן.",
    # forced / voluntary password change
    "pw_change_title": "החלפת סיסמה", "pw_change_body": "יש לבחור סיסמה חדשה לפני המשך השימוש.",
    "field_password_current": "סיסמה נוכחית", "field_password_new": "סיסמה חדשה",
    "btn_change_password": "החלפת סיסמה", "pw_changed": "הסיסמה הוחלפה.",
    # summary
    "greeting": "שלום, {name}", "scale_line": "המדדים מחושבים לפי רף: {scale}",
    "scale_male": "זכר", "scale_female": "נקבה", "scale_walk": "{gender} (כ.מ - הליכה 6 ק״מ)",
    "kpi_last_run": "ריצה אחרונה", "kpi_last_walk": "הליכה אחרונה", "kpi_best": "שיא אישי", "kpi_pushups": "שכיבות",
    "kpi_note": "החצים מראים שינוי לעומת הבוחן הקודם.",
    "latest_status_title": "הסטטוס בבוחן האחרון",
    "empty_summary_title": "עוד אין תוצאות",
    "empty_summary_body": "לאחר הבוחן אפשר להזין זמן ושכיבות סמיכה. כאן יוצגו הסטטוס, השיא האישי והגרף.",
    "btn_first_result": "הזנת תוצאה ראשונה",
    # add result
    "add_title": "הזנת תוצאה", "field_date": "תאריך הבוחן",
    "section_run": "ריצת 3 ק״מ", "section_walk": "הליכת 6 ק״מ", "section_pushups": "שכיבות סמיכה",
    "field_minutes": "דקות", "field_seconds": "שניות", "field_pushups": "מספר חזרות",
    "ph_minutes": "יש לבחור", "ph_seconds": "יש לבחור", "ph_pushups": "יש לבחור",
    "readback": "הזמן שהוזן: {time}", "readback_empty": "הזמן יוצג כאן (דקות:שניות)",
    "chip_pass": "רף מעבר", "chip_excellent": "רף הצטיינות", "chip_upto": "עד {v}", "chip_atleast": "מ-{v} חזרות",
    "btn_save_result": "שמירת תוצאה",
    "err_none": "יש להזין לפחות תוצאה אחת: זמן או שכיבות סמיכה.",
    "err_minutes_missing": "חסרות דקות. יש להזין מספר שלם, למשל 15.",
    "err_seconds_missing": "חסרות שניות. יש להזין מספר בין 0 ל-59.",
    "err_seconds_range": "שניות: מספר בין 0 ל-59.",
    "err_time_zero": "הזמן חייב להיות גדול מ-00:00.",
    "err_time_implausible": "הזמן {time} נראה חריג לבוחן הזה. יש לבדוק את הדקות והשניות.",
    "err_pushups_range": "שכיבות סמיכה: מספר שלם בין 0 ל-200.",
    "err_future_date": "לא ניתן להזין תאריך עתידי.",
    "err_date_missing": "יש לבחור את תאריך הבוחן.",
    "input_kept": "הערכים שהוזנו נשמרו בטופס.",
    "warn_zero_pushups": "הוזנו 0 שכיבות סמיכה. הערך יישמר כתוצאה בפועל.",
    "warn_duplicate": "כבר נשמרה תוצאה זהה לאותו תאריך. האם לשמור אותה שוב?",
    "btn_confirm_save": "אישור ושמירה", "btn_fix": "תיקון",
    # result card (each test is graded on its own; there is no overall verdict)
    "saved": "התוצאה נשמרה",
    "test_run": "ריצה", "test_walk": "הליכה", "test_pushups": "שכיבות סמיכה",
    "status_line": "{test}: {status}",
    "status_excellent": "הצטיינות", "status_pass": "הושג הרף", "status_fail": "טרם הושג הרף",
    "fail_with_gap": "טרם הושג הרף — חסרות {gap}", "fail_with_gap_one": "טרם הושג הרף — חסרה {gap}",
    "gap_to_excellent": "להצטיינות חסרות {gap}", "gap_to_excellent_one": "להצטיינות חסרה {gap}",
    "at_top": "הושגה רמת ההצטיינות",
    "new_personal_best": "שיא אישי חדש",
    "vs_prev_better": "לעומת הבוחן הקודם: שיפור של {gap}", "vs_prev_worse": "לעומת הבוחן הקודם: ירידה של {gap}",
    "vs_prev_same": "ללא שינוי לעומת הבוחן הקודם",
    "status_ungraded": "הערך חורג מהטווח הצפוי ולא דורג",
    "result_warning": "הערך נראה חריג. יש לוודא שהוזן נכון.",
    "btn_tip_link": "טיפים לשיפור", "btn_add_another": "הזנה נוספת", "btn_to_summary": "לסיכום",
    # history: edit / delete / confirm
    "history_title": "היסטוריית תוצאות", "empty_history": "עוד אין תוצאות להצגה.",
    "btn_edit": "עריכה", "btn_delete": "מחיקה", "btn_cancel": "ביטול",
    "btn_save_changes": "שמירת שינויים", "btn_delete_confirm": "מחיקת התוצאה", "btn_keep": "השארת התוצאה",
    "btn_show_older": "תוצאות ישנות יותר",
    "edit_title": "עריכת תוצאה", "delete_dialog_title": "מחיקת תוצאה",
    "delete_title": "מחיקת התוצאה מתאריך {date}?",
    "delete_body": "המחיקה סופית ולא ניתן לשחזר את התוצאה.",
    "deleted": "התוצאה נמחקה.", "updated": "התוצאה עודכנה.",
    # more: sections, profile, export, account deletion
    "more_title": "עוד", "more_tab_tips": "טיפים", "more_tab_videos": "סרטונים", "more_tab_profile": "פרופיל", "more_tab_accommodations": "הקלות",
    "profile_title": "פרופיל",
    "btn_export": "ייצוא נתונים (PDF)",
    "export_body": "יופק קובץ PDF עם כל התוצאות, ההקלות והפטורים והפרטים השמורים בחשבון.",
    "btn_delete_account": "מחיקת החשבון",
    "delete_account_title": "מחיקת החשבון?",
    "delete_account_body": "כל התוצאות והפרטים יימחקו לצמיתות. לא ניתן לשחזר אותם.",
    "delete_account_password": "יש להזין את הסיסמה לאישור המחיקה.",
    "btn_delete_account_confirm": "מחיקה לצמיתות", "btn_keep_account": "השארת החשבון",
    "account_deleted": "החשבון נמחק.",
    # rate limit, bot check, session, storage
    "rate_limited": "בוצעו יותר מדי ניסיונות. נסו שוב בעוד {wait}.",
    "honeypot_label": "שדה זה יש להשאיר ריק",
    "bot_check_failed": "בדיקת האבטחה לא הושלמה. יש לנסות שוב.",
    "session_expired": "פג תוקף ההתחברות. יש להיכנס מחדש. תוצאות שנשמרו קודם לא נפגעו.",
    "btn_retry": "ניסיון נוסף", "btn_refresh": "רענון",
    # ---- section: charts and content (owned by the charts/content workstream) ----
    "trend_title": "התקדמות", "chart_metric_label": "מדד להצגה בגרף",
    "chart_line_pass": "רף מעבר", "chart_line_excellent": "רף הצטיינות",
    "chart_hint_up": "ככל שהנקודה גבוהה יותר, התוצאה טובה יותר.",
    "chart_marks": "עיגול גדול: הבוחן האחרון. יהלום: השיא האישי.",
    "chart_few": "בגרף נקודה אחת בלבד. לאחר בוחן נוסף יופיע קו התקדמות.",
    "chart_empty_metric": "עוד אין תוצאות במדד הזה.",
    "chart_table": "טבלת נתונים",
    "chart_col_run": "זמן ריצה", "chart_col_walk": "זמן הליכה", "chart_col_reps": "חזרות", "chart_col_status": "סטטוס",
    "chart_sum_time": "ב-{n} בחנים: מ-{first} ל-{last}. השיא האישי: {best}.",
    "chart_sum_time_one": "בבוחן אחד: {last}.",
    "chart_sum_reps": "ב-{n} בחנים: מ-{first} ל-{last} חזרות. השיא האישי: {best}.",
    "chart_sum_reps_one": "בבוחן אחד: {last} חזרות.",
    "chart_alt": "גרף התקדמות. {summary}",
    "chart_best_label": "שיא",
    "tips_order_note": "הסדר מותאם לתוצאה האחרונה.",
    "btn_play_video": "הפעלת הסרטון", "btn_open_video": "פתיחה בחלון חדש",
    # ---- section: instructor and chief screens (owned by the staff workstream) ----
    "platoon_title": "מבט מחלקתי",
    "platoon_banner": "מוצגות תוצאות אישיות של צוערים. התצוגה לקריאה בלבד, והגישה אליה נרשמת ביומן.",
    "platoon_none": "אין מחלקות שהוענקו לחשבון הזה.",
    "chief_title": "סיכום פיקודי",
    "chief_banner": "מוצגים נתונים מצטברים בלבד. פתיחת תוצאות של צוער בשמו נרשמת ביומן.",
    "group_too_small": "קבוצות של פחות מ-5 צוערים אינן מוצגות.",
    "group_hidden": "הקבוצה אינה מוצגת (פחות מ-5 צוערים).",
    "pick_platoon": "בחירת מחלקה", "pick_cadet": "בחירת צוער", "btn_open_cadet": "פתיחת התוצאות",
    "unassigned": "ללא מחלקה", "cadets_count": "{n} צוערים", "cadets_count_one": "צוער אחד", "cadets_count_two": "שני צוערים",
    "col_name": "שם", "col_platoon": "מחלקה", "col_date": "תאריך",
    "col_run": "ריצה אחרונה", "col_pushups": "שכיבות אחרונות", "col_best_run": "שיא ריצה",
    "col_best_walk": "שיא הליכה", "col_best_pushups": "שיא שכיבות", "col_metric": "מדד", "col_results": "תוצאות", "col_status": "סטטוס",
    "col_excellent": "הצטיינות", "col_pass": "הושג הרף", "col_fail": "טרם הושג הרף", "col_n": "מספר",
    "metric_run": "ריצה 3 ק״מ", "metric_walk": "הליכה 6 ק״מ", "metric_pushups": "שכיבות סמיכה",
    "total_row": "סך הכול (קבוצות מוצגות בלבד)",
    "no_results_cadet": "אין תוצאות לצוער הזה.",
    "empty_platoon": "אין צוערים להצגה בסינון הזה.",
    "chief_aggregates_title": "מצטבר לפי מחלקה",
    "chief_drill_title": "תוצאות של צוער בשמו",
    "chief_open_notice": "פתיחת תוצאות של צוער בשמו נרשמת ביומן.",
    "chief_opened_logged": "הפתיחה נרשמה ביומן.",
    # ---- section: round 2 UI ----
    "acc_tab_label": "הקלות",
    "acc_title": "הקלות ופטורים", "acc_none": "אין הקלות או פטורים רשומים.",
    "acc_cadet_warning": "הקלות ופטורים שנרשמים כאן נשלחים לאישור ראש הקורס ונבדקים גם על ידי המדריכים. "
                         "רק רשומה שאושרה נכנסת לתוקף. רשומה שנשמרה אי אפשר לערוך או למחוק. "
                         "מחיקה אפשרית רק בבקשת מדריך ובאישור ראש הקורס.",
    "acc_add_title": "הוספת הקלה או פטור",
    "field_acc_kind": "סוג", "field_acc_scope": "תחום", "field_acc_start": "תאריך התחלה", "field_acc_end": "תאריך סיום",
    "field_acc_note": "הערה (לא חובה)",
    "btn_acc_save": "שמירת הרשומה", "btn_add_accommodation": "הוספת הקלה או פטור", "acc_added": "הרשומה נשמרה.",
    "acc_dates": "{start} עד {end}",
    "acc_pending_chief": "ממתין להחלטה על מחיקה",      # the DELETION-pending badge (not the approval badge acc_approval_pending)
    "btn_request_delete": "בקשת מחיקה", "btn_send_request": "שליחת הבקשה",
    "acc_request_title": "בקשת מחיקה?",
    "acc_request_body": "הבקשה תועבר לראש הקורס לאישור. עד האישור הרשומה תישאר גלויה.",
    "acc_requested": "בקשת המחיקה נשלחה.",
    "acc_koam": "כ.מ: הליכה 6 ק״מ",
    "staff_gender": "מגדר: {gender}",
    "acc_active_marker": "הקלה או פטור בתוקף",
    "chief_pending_title": "בקשות למחיקת רשומה", "chief_pending_none": "אין בקשות מחיקה ממתינות.",
    "chief_pending_one": "בקשת מחיקה אחת ממתינה להחלטה", "chief_pending_many": "{n} בקשות מחיקה ממתינות להחלטה",
    "acc_requested_by_label": "הבקשה למחיקה הוגשה על ידי",
    "btn_approve_delete": "אישור מחיקה", "btn_reject_delete": "דחיית בקשת המחיקה", "btn_delete_record": "מחיקת הרשומה",
    "acc_approve_title": "אישור מחיקה?", "acc_approve_body": "הרשומה תימחק לצמיתות ולא ניתן לשחזר אותה.",
    "acc_deleted": "הרשומה נמחקה.", "acc_rejected": "בקשת המחיקה נדחתה. הרשומה נשארה.",
    "chief_chart_title": "תוצאות לפי מחלקה",
    "chief_kpi_note": "אחוז הצוערים שהושג להם הרף בכל מבחן בנפרד, בקבוצות המוצגות בלבד.",
    "chief_chart_alt": "גרף עמודות מוערם: לכל מחלקה, מספר הצוערים בכל רמה.",
    "chief_chart_metric": "מדד לתצוגה",
    "chief_list_notice": "הצגת צוערי המחלקה נרשמת ביומן.",
    "chief_pick_hint": "יש לבחור מחלקה כדי לראות את הצוערים שלה.",
    "btn_open_details": "הצגת פרטים", "btn_close_details": "סגירת הפרטים",
    # ---- section: software (owned by the software workstream) ----
    # accommodations (הקלות ופטורים): names of kinds, scopes, sources and statuses, shared by every screen and the PDF
    "acc_kind_relief": "הקלה", "acc_kind_exemption": "פטור",
    "acc_scope_run": "ריצה", "acc_scope_pushups": "שכיבות סמיכה", "acc_scope_both": "ריצה ושכיבות סמיכה",
    "acc_source_cadet": "הוזן על ידי הצוער", "acc_source_instructor": "הוזן על ידי מדריך",
    "acc_source_chief": "הוזן על ידי הפיקוד", "acc_source_admin": "הוזן על ידי מנהל המערכת",
    "acc_status_upcoming": "עתידי", "acc_status_active": "בתוקף", "acc_status_expired": "פג תוקף",
    "acc_delete_pending": "בקשת מחיקה ממתינה לאישור",
    # approval step: a cadet's own record waits for the chief; only an approved record is in effect
    "acc_approval_pending": "ממתין לאישור ראש הקורס", "acc_approval_approved": "אושר", "acc_approval_rejected": "נדחה",
    # the chief's list of accommodation REQUESTS (a cadet's own entries waiting for approval); not the deletion requests
    "chief_requests_title": "בקשות הקלה ופטור ממתינות", "chief_requests_none": "אין בקשות הקלה או פטור ממתינות.",
    "chief_requests_one": "בקשה אחת ממתינה להחלטה", "chief_requests_many": "{n} בקשות ממתינות להחלטה",
    "acc_submitted_on": "הבקשה הוגשה בתאריך {date}",
    "btn_approve_request": "אישור הבקשה", "btn_reject_request": "דחיית הבקשה",
    "acc_request_approved": "הבקשה אושרה.", "acc_request_rejected": "הבקשה נדחתה.",
    "acc_reject_request_title": "דחיית הבקשה?",
    "acc_reject_request_body": "הרשומה תישאר גלויה לצוער ולצוות עם הסימון נדחה, ולא תיכנס לתוקף. אי אפשר לשנות את ההחלטה.",
    "acc_sent_for_approval": "הבקשה נשמרה וממתינה לאישור ראש הקורס.",
    # the PDF export of the cadet's own data
    "pdf_title": "הנתונים שלי", "pdf_generated": "הופק בתאריך {date}", "pdf_email": "אימייל: {email}",
    "pdf_section_results": "תוצאות", "pdf_section_accommodations": "הקלות ופטורים",
    "pdf_section_views": "מי צפה בתוצאות",
    "pdf_col_run": "ריצה", "pdf_col_pushups": "שכיבות סמיכה",
    "pdf_col_run_status": "סטטוס ריצה", "pdf_col_pushups_status": "סטטוס שכיבות",
    "pdf_col_kind": "סוג", "pdf_col_scope": "תחום", "pdf_col_from": "מתאריך", "pdf_col_to": "עד תאריך",
    "pdf_col_source": "מקור", "pdf_col_note": "הערה",
    "pdf_col_reader": "צופה", "pdf_col_reads": "מספר צפיות",
    "pdf_no_results": "עוד אין תוצאות.", "pdf_no_accommodations": "לא נרשמו הקלות או פטורים.",
    "pdf_no_views": "אין צפיות רשומות בתוצאות.", "pdf_reader_gone": "חשבון שאינו קיים עוד",
    "pdf_page": "עמוד {n} מתוך {total}",
    # ---- aggregated period reports (weekly on Thursday, monthly at month end): chief screen, PDF, e-mail
    "report_section_title": "דוחות",
    "report_intro": "דוחות מצטברים ללא שמות. דוח שבועי נוצר בכל יום חמישי, ודוח חודשי נוצר בסוף החודש. פתיחת דוח נרשמת ביומן.",
    "report_type_label": "סוג הדוח", "report_type_weekly": "שבועי", "report_type_monthly": "חודשי",
    "report_list_empty": "עוד לא נוצרו דוחות מסוג זה.",
    "report_row_weekly": "שבוע {start} עד {end}", "report_row_monthly": "{label}, נתונים עד {end}",
    "report_row_generated": "הופק בתאריך {date}",
    "report_btn_prepare": "הכנה להורדה", "report_btn_download": "הורדת הדוח",
    "report_prepared": "הדוח מוכן להורדה. הפתיחה נרשמה ביומן.",
    "report_older": "דוחות קודמים",
    "report_mail_toggle": "שליחה אוטומטית בדואר אלקטרוני",
    "report_mail_explain": "כשהשליחה פועלת, כל דוח חדש נשלח כקובץ PDF לכל חשבון פעיל של ראש הקורס. דוחות שנוצרו לפני "
                           "ההפעלה לא נשלחים. כשהשליחה כבויה, הדוחות נשמרים כאן להורדה בלבד.",
    "report_mail_on_since": "השליחה פועלת מאז {date}.", "report_mail_off_note": "השליחה כבויה.",
    "report_mail_not_configured": "השליחה פועלת, אך הגדרות שרת הדואר חסרות, ולכן לא יישלח דבר. יש לפנות למפעיל המערכת.",
    "report_mail_privacy": "הדוחות מכילים נתונים מצטברים בלבד. דואר אלקטרוני אינו מוצפן מקצה לקצה.",
    "report_mail_saved": "ההגדרה נשמרה ונרשמה ביומן.",
    "report_month_1": "ינואר", "report_month_2": "פברואר", "report_month_3": "מרץ", "report_month_4": "אפריל",
    "report_month_5": "מאי", "report_month_6": "יוני", "report_month_7": "יולי", "report_month_8": "אוגוסט",
    "report_month_9": "ספטמבר", "report_month_10": "אוקטובר", "report_month_11": "נובמבר", "report_month_12": "דצמבר",
    # the PDF
    "report_pdf_title_weekly": "דוח שבועי מצטבר", "report_pdf_title_monthly": "דוח חודשי מצטבר",
    "report_pdf_course": "קורס {name}", "report_pdf_period": "התקופה: {start} עד {end}",
    "report_pdf_month": "חודש {label}", "report_pdf_data_until": "נתונים עד {date}",
    "report_pdf_section_course": "סיכום הקורס כולו", "report_pdf_section_platoon": "מחלקה {platoon}",
    "report_hidden": "לא מוצג (קבוצה קטנה)",
    "report_platoon_hidden": "מחלקה {platoon}: לא מוצגת (קבוצה קטנה)",
    "report_line_cadets": "צוערים פעילים: {v}", "report_line_tested": "צוערים שנבחנו בתקופה: {v}",
    "report_line_results": "תוצאות שנרשמו בתקופה: {v}",
    "report_empty": "לא נרשמו תוצאות בתקופה זו.",
    "report_col_test": "מבחן", "report_col_n": "מספר צוערים", "report_col_avg": "ממוצע", "report_col_best": "שיא",
    "report_col_change": "שינוי בממוצע",
    "report_test_hidden": "{test}: לא מוצג (קבוצה קטנה)",
    "report_change_faster": "קצר ב-{v}", "report_change_slower": "ארוך ב-{v}",
    "report_change_more": "גבוה ב-{v}", "report_change_less": "נמוך ב-{v}", "report_change_same": "ללא שינוי",
    "report_omitted": "—",
    "report_section_acc": "הקלות ופטורים בתוקף בתקופה", "report_acc_col_kind": "סוג",
    "report_acc_hidden": "הקלות ופטורים: לא מוצג (קבוצה קטנה)",
    "report_section_notes": "הערות",
    "report_note_cohort": "הדוח כולל צוערים פעילים בלבד, ללא שמות.",
    "report_note_latest": "לכל צוער ובכל מבחן נספרת התוצאה האחרונה בתקופה, לפי תאריך הבוחן. מספר הצוערים בכל רמה, "
                          "הממוצע, השיא והשינוי מבוססים עליה. מספר התוצאות כולל את כל התוצאות שנרשמו בתקופה.",
    "report_note_weekly": "תקופה שבועית: מיום שישי עד יום חמישי, לפי תאריך הבוחן.",
    "report_note_monthly": "תקופה חודשית: מתחילת החודש ועד תאריך ההפקה, לפי תאריך הבוחן. כשהחודש מסתיים בסוף שבוע, "
                           "תוצאות מהימים שאחרי תאריך ההפקה אינן כלולות כאן, והן מופיעות בדוח השבועי הבא.",
    "report_note_small": "קבוצות של פחות מ-5 צוערים אינן מוצגות, וגם אין עבורן מספר. סיכום הקורס כולל את הקבוצות "
                         "המוצגות בלבד, ולכן אי אפשר לחשב קבוצה מוסתרת בחיסור.",
    "report_note_change": "שינוי בממוצע לעומת התקופה הקודמת ({start} עד {end}) מוצג רק כשבשתי התקופות יש לפחות 5 "
                          "צוערים עם ערך באותה קבוצה.",
    "report_note_acc": "הקלות ופטורים: רשומות מאושרות שהיו בתוקף ביום כלשהו בתקופה. המספר הוא מספר צוערים לכל סוג ותחום.",
    "report_note_grading": "כל מבחן מדורג בנפרד. אין ציון כולל ואין קביעה כוללת.",
    # the e-mail
    "report_mail_subject_weekly": "בר-אור: דוח שבועי מצטבר, {start} עד {end}",
    "report_mail_subject_monthly": "בר-אור: דוח חודשי מצטבר, {label}",
    "report_mail_body_weekly": "שלום,\nהדוח השבועי המצורף מכסה את התקופה {start} עד {end}.\n"
                               "הדוח המצורף מכיל נתונים מצטברים בלבד.",
    "report_mail_body_monthly": "שלום,\nהדוח החודשי המצורף מכסה את {label}, עם נתונים עד {end}.\n"
                                "הדוח המצורף מכיל נתונים מצטברים בלבד.",
    # --- v9 reports --- (on-demand reports: the last 30 days and a chosen date range; never stored, never mailed)
    "report_type_trailing": "30 הימים האחרונים", "report_type_range": "טווח תאריכים",
    "report_title_trailing": "דוח ל-30 הימים האחרונים", "report_title_range": "דוח לטווח תאריכים",
    "report_row_trailing": "30 הימים האחרונים, {start} עד {end}", "report_row_range": "{start} עד {end}",
    "report_range_start": "מתאריך", "report_range_end": "עד תאריך",
    "report_range_help": "טווח של עד 5 חודשים. אי אפשר לבחור תאריך עתידי.",
    "report_range_btn_make": "הפקת הדוח",
    "report_range_ready": "הדוח מוכן להורדה. ההפקה נרשמה ביומן.",
    "report_adhoc_note": "דוח זה נוצר לפי בקשה, אינו נשמר במערכת ואינו נשלח בדואר אלקטרוני. כל הפקה נרשמת ביומן.",
    "report_pdf_title_trailing": "דוח מצטבר ל-30 הימים האחרונים", "report_pdf_title_range": "דוח מצטבר לטווח תאריכים",
    "report_note_trailing": "תקופה של 30 הימים האחרונים: מלפני 30 יום ועד תאריך ההפקה, לפי תאריך הבוחן. "
                            "הדוח נוצר לפי בקשה ואינו נשמר.",
    "report_note_range": "טווח תאריכים שנבחר, עד 5 חודשים, לפי תאריך הבוחן. הדוח נוצר לפי בקשה ואינו נשמר.",
    "report_range_period": "התקופה: {start} עד {end}",
    "report_trailing_help": "הדוח מכסה את 30 הימים שעד היום, כולל היום.",
    "report_make_first": "הדוח נוצר לפי בקשה. אחרי ההפקה מופיע כפתור ההורדה.",
    # --- v9 staff --- (instructor page: name-only cards, requests, nomination; chief page: sections and the requests cards)
    "btn_add_cadet": "הוספת צוער",
    "btn_request_edit": "בקשה לעריכה", "btn_request_remove": "בקשה להסרה",
    "ins_requests_title": "הבקשות שלי", "ins_requests_none": "עוד לא נשלחו בקשות.",
    "req_kind_add_conflict": "העברת צוער למחלקה", "req_kind_edit": "עריכת פרטי צוער",
    "req_kind_delete": "הסרת צוער מהמחלקה", "req_kind_nominate_cmdr": "הצעת מ״כ",
    "req_status_pending": "ממתינה להחלטה", "req_status_approved": "אושרה", "req_status_rejected": "נדחתה",
    "nominate_title": "הצעת מ״כ",
    "nominate_help": "אפשר להציע צוער רשום כמ״כ במחלקה. ההצעה ממתינה לאישור של ראש הקורס או של מנהל המערכת.",
    "field_nominee_email": "אימייל הצוער המוצע", "btn_nominate": "שליחת ההצעה",
    "nominate_sent": "ההצעה נשלחה וממתינה להחלטה.",
    "chief_results_title": "תוצאות צוהר ובחירת המחלקה",
    "btn_staff_edit": "עריכת פרטים", "btn_staff_move": "העברה למחלקה", "btn_staff_remove": "הסרה מהמחלקה",
    "req_section_title": "בקשות",
    "req_card_approve": "בקשות לאישור", "req_card_delete": "בקשות למחיקה", "req_card_changes": "בקשות לשינוי צוערים",
    "req_card_label": "{label} ({n})",
    "change_none": "אין בקשות לשינוי צוערים ממתינות.",
    "change_by": "הוגשה על ידי",
    "change_to_platoon": "למחלקה: {platoon}", "change_now_platoon": "מחלקה נוכחית: {platoon}",
    "change_new_name": "שם חדש: {name}", "change_new_gender": "מגדר חדש: {gender}",
    "change_new_km": "מסלול הליכה (כ.מ): {v}", "change_new_platoon": "מחלקה חדשה: {platoon}",
    "change_yes": "כן", "change_no": "לא",
    # request cards, self-explanatory: what is asked, the change as old <- new, who asked, and what approval does
    "req_ask_add_conflict": "התבקשה העברת הצוער למחלקה אחרת. הצוער רשום כעת במחלקה אחרת, ולכן נדרש אישור.",
    "req_ask_edit": "התבקש שינוי בפרטי הצוער. השורות שלמטה מציגות מה ישתנה.",
    "req_ask_delete": "התבקשה הסרת הצוער מהמחלקה שלו.",
    "req_ask_nominate_cmdr": "הוצע צוער רשום למינוי כמ״כ במחלקה.",
    "chg_platoon": "מחלקה: {old} ← {new}", "chg_name": "שם: {old} ← {new}", "chg_gender": "מגדר: {old} ← {new}",
    "chg_km": "מסלול הליכה (כ.מ): {old} ← {new}", "chg_nominee_platoon": "המינוי למ״כ במחלקה: {platoon}",
    "change_by_titled": "הוגשה על ידי {name} ({title})",
    "req_if_approved_add_conflict": "באישור: הצוער יועבר למחלקה המבוקשת.",
    "req_if_approved_edit": "באישור: פרטי הצוער יתעדכנו לפי השורות שלמעלה.",
    "req_if_approved_delete": "באישור: הצוער יוסר מהמחלקה. החשבון, התוצאות וההקלות נשארים.",
    "req_if_approved_nominate_cmdr": "באישור: הצוער יהפוך למ״כ במחלקה. לפני כן נדרש לוודא את זהותו.",
    "req_if_rejected": "בדחייה: לא מתבצע שינוי.",
    "change_approved": "הבקשה אושרה, והשינוי בוצע.", "change_rejected": "הבקשה נדחתה. לא בוצע שינוי.",
    "grant_title": "אישור מינוי מ״כ",
    "grant_intro": "לפני האישור יש לוודא מחוץ למערכת שהאימייל שייך לצוער הנכון.",
    "grant_name": "שם: {name}", "grant_email": "אימייל: {email}", "grant_registered": "מועד הרשמה: {date}",
    "grant_platoon": "מחלקה: {platoon}",
    "grant_verified": "הזהות אומתה מחוץ למערכת", "btn_grant_confirm": "אישור המינוי",
    "grant_needs_check": "יש לסמן שהזהות אומתה לפני האישור.",
    "staff_join_title": "קישור הצטרפות למחלקה",
    # --- v9 registration --- (open registration: always a cadet; the platoon is picked in the form or via a join link)
    "register_intro": "ההרשמה פתוחה לצוערים. כל חשבון חדש נפתח כחשבון צוער. את המחלקה אפשר לבחור כאן או להשלים בהמשך.",
    "field_platoon": "מחלקה",
    "reg_platoon_none": "עדיין לא ידוע",
    "reg_platoon_hint": "אפשר להשאיר את הבחירה כ״עדיין לא ידוע״ ולקבוע מחלקה בהמשך.",
    "join_preselected": "המחלקה נבחרה לפי הקישור שהתקבל. אפשר לשנות את הבחירה.",
    "register_taken_note": "ייתכן שכבר נפתח חשבון עם הכתובת הזו. במקרה כזה אפשר להיכנס בלשונית הכניסה.",
    # --- v9 admin --- (admin screen, accounts panel, cadet dialogs, join links, top-bar bell, profile platoon block)
    "nav_admin": "ניהול",
    "admin_title": "ניהול הרשאות וצוערים",
    "admin_banner": "מסך זה מיועד להרשאות ולניהול צוערים. תוצאות והקלות אינן מוצגות כאן.",
    "role_cadet": "צוער", "role_instructor": "מדריך", "role_chief": "ראש הקורס", "role_admin": "מנהל מערכת",
    "title_mp": "מ״פ", "title_smp": "סמ״פ", "title_mm": "מ״מ", "title_cmdr": "מ״כ", "title_unset": "תואר לא הוגדר",
    # accounts panel (admin screen and chief page)
    "adm_accounts_title": "הרשאות וחשבונות",
    "adm_accounts_intro": "כל אדם יכול להירשם כצוער. תפקיד ניתן רק לחשבון שכבר נרשם, ורק אחרי אימות הזהות מחוץ למערכת.",
    "adm_search_label": "חיפוש לפי שם או אימייל",
    "adm_role_filter": "סינון לפי תפקיד", "adm_role_all": "כל התפקידים",
    "btn_adm_search": "חיפוש",
    "adm_count": "מוצגים {n} מתוך {total} חשבונות.",
    "adm_count_more": "מוצגים {n} מתוך {total} חשבונות. כדי לצמצם, אפשר לחפש לפי שם או אימייל.",
    "adm_none": "לא נמצאו חשבונות.",
    "adm_col_name": "שם", "adm_col_email": "אימייל", "adm_col_registered": "נרשם", "adm_col_role": "תפקיד",
    "adm_col_title": "תואר", "adm_col_platoon": "מחלקה",
    "adm_table_hint": "בחירת שורה בטבלה פותחת את פרטי החשבון. אפשר גם לבחור מהרשימה שמתחת לטבלה.",
    "adm_pick_account": "בחירה מהרשימה",
    "adm_registered_at": "מועד ההרשמה: {date}",
    "adm_line_platoon": "מחלקה: {platoon}",
    "adm_admin_readonly": "זה חשבון של מנהל מערכת. שינוי בו נעשה רק על ידי מפעיל המערכת, באמצעות סקריפט ייעודי בשרת.",
    "adm_self": "זה החשבון הנוכחי. אי אפשר לשנות בו את התפקיד.",
    "adm_chief_readonly": "שינוי תפקיד של ראש הקורס נעשה רק על ידי מנהל מערכת.",
    "adm_inactive": "החשבון אינו פעיל, ולכן אי אפשר לשנות בו תפקיד.",
    "adm_field_role": "תפקיד", "adm_field_title": "תואר", "adm_field_platoon": "מחלקה",
    "adm_platoon_option": "{name} ({status}, מ״כים: {n} מתוך {max})",
    "adm_has_mm": "יש מ״מ", "adm_no_mm": "אין מ״מ",
    "adm_hint_mm_taken": "במחלקה שנבחרה כבר יש מ״מ. להחלפה יש קודם לשנות את המ״מ הנוכחי למ״כ.",
    "adm_hint_cmdr_full": "במחלקה שנבחרה כבר יש {max} מ״כים.",
    "adm_fill_title": "יש לבחור תואר לפני השמירה.",
    "adm_fill_title_platoon": "יש לבחור תואר ומחלקה לפני השמירה.",
    "btn_adm_save": "שמירת השינוי",
    "adm_saved": "השינוי נשמר.",
    "adm_grant_title": "מתן תפקיד",
    "adm_grant_note": "כל אדם יכול להירשם עם כל כתובת אימייל. אין לתת תפקיד לפני שהשם והאימייל אומתו מול האדם עצמו, מחוץ למערכת.",
    "adm_name_line": "שם: {name}", "adm_email_line": "אימייל: {email}",
    "adm_role_line": "התפקיד המבוקש: {v}",
    "adm_verified": "הזהות אומתה מחוץ למערכת",
    "adm_verify_needed": "יש לסמן שהזהות אומתה לפני מתן התפקיד.",
    "btn_adm_grant": "מתן התפקיד",
    "adm_demote_title": "שינוי תפקיד לצוער",
    "adm_demote_body": "החשבון יהפוך לחשבון צוער, התואר יימחק, והבקשות הפתוחות בשמו ייסגרו. התוצאות נשמרות.",
    "btn_adm_demote": "שינוי התפקיד",
    "btn_adm_release": "שחרור אימייל",
    "adm_release_title": "שחרור כתובת אימייל",
    "adm_release_body": "הכתובת תשוחרר ותהיה פנויה להרשמה מחדש. החשבון יושבת, יוסר מהמחלקה, וההתחברויות שלו יסתיימו. התוצאות וההקלות נשמרות. אי אפשר לבטל את הפעולה.",
    "btn_adm_release_ok": "שחרור הכתובת", "adm_released": "הכתובת שוחררה.",
    # cadet management (admin screen; the dialogs are shared with the chief and instructor pages)
    "adm_cadets_title": "ניהול צוערים",
    "adm_cadets_intro": "בחירת מחלקה מציגה את הצוערים שלה. הסרה מהמחלקה אינה מוחקת את החשבון, את התוצאות או את ההקלות.",
    "adm_cadets_empty": "אין צוערים להצגה במחלקה הזו.",
    "adm_cadets_truncated": "מוצגים הצוערים הראשונים בלבד. כדי למצוא צוער מסוים, אפשר לחפש בטבלת החשבונות.",
    "btn_adm_cadet_edit": "עריכה", "btn_adm_cadet_move": "העברה", "btn_adm_cadet_remove": "הסרה",
    "btn_adm_cadet_add": "הוספת צוער",
    "adm_edit_title": "עריכת פרטי צוער",
    "adm_edit_direct": "אפשר לשנות שם, מגדר, מסלול הליכה (כ.מ) ומחלקה. תוצאות אינן נערכות כאן.",
    "adm_edit_request": "השינוי יישלח כבקשה, ויחול רק לאחר אישור של ראש הקורס או של מנהל המערכת.",
    "adm_field_km": "מסלול הליכה (כ.מ)",
    "adm_edit_nothing": "לא שונה אף פרט.",
    "adm_edit_done": "הפרטים עודכנו.", "adm_edit_requested": "בקשת העריכה נשלחה וממתינה להחלטה.",
    "adm_move_title": "העברת צוער למחלקה", "adm_move_field": "מחלקה חדשה",
    "adm_move_nothing": "יש לבחור מחלקה.",
    "btn_adm_move_ok": "העברה",
    "adm_move_done": "הצוער הועבר.", "adm_move_unchanged": "הצוער כבר נמצא ביעד שנבחר.",
    "adm_move_requested": "בקשת ההעברה נשלחה וממתינה להחלטה.",
    "adm_remove_title": "הסרה מהמחלקה",
    "adm_remove_body": "הצוער יוסר מהמחלקה ויישאר ללא מחלקה. החשבון, התוצאות וההקלות נשמרים, ובקשות פתוחות על הצוער ייסגרו.",
    "adm_remove_body_request": "תישלח בקשה להסרת הצוער מהמחלקה. החשבון והתוצאות נשמרים, וההסרה תתבצע לאחר אישור.",
    "btn_adm_remove_ok": "הסרה מהמחלקה",
    "adm_removed": "הצוער הוסר מהמחלקה.", "adm_remove_unchanged": "הצוער כבר ללא מחלקה.",
    "adm_remove_requested": "בקשת ההסרה נשלחה וממתינה להחלטה.",
    "adm_add_title": "הוספת צוער למחלקה",
    "adm_add_help": "אפשר להוסיף רק צוער שכבר נרשם, לפי האימייל שבו נרשם.",
    "adm_add_email": "אימייל הצוער", "btn_adm_add_ok": "הוספת הצוער",
    "adm_add_assigned": "הצוער נוסף למחלקה.", "adm_add_unchanged": "הצוער כבר נמצא במחלקה.",
    "adm_add_moved": "הצוער הועבר למחלקה.",
    "adm_add_requested": "הצוער משויך למחלקה אחרת. נשלחה בקשה להעברה, והיא ממתינה להחלטה.",
    # join links
    "adm_join_title": "קישור הצטרפות",
    "adm_join_intro": "הקישור רק בוחר מראש את המחלקה בטופס ההרשמה ואינו נותן הרשאה כלשהי.",
    "adm_join_off": "קישורי הצטרפות כבויים: בשרת לא הוגדר מפתח חתימה. יש לפנות למפעיל המערכת.",
    "adm_join_days": "תוקף הקישור", "adm_join_days_n": "{n} ימים",
    "btn_adm_join_make": "יצירת קישור",
    "adm_join_until": "הקישור תקף עד {date}.",
    "adm_join_no_url": "זו הסיומת של הקישור. את כתובת האפליקציה יש להוסיף לפניה.",
    "btn_adm_join_revoke": "ביטול הקישורים",
    "adm_join_revoke_title": "ביטול כל הקישורים",
    "adm_join_revoke_body": "קישורים ששותפו קודם יפסיקו לעבוד. אפשר ליצור קישור חדש בכל עת.",
    "btn_adm_join_revoke_ok": "ביטול כל הקישורים", "adm_join_revoked": "כל הקישורים של המחלקה בוטלו.",
    # requests and new platoons (admin screen)
    "adm_requests_title": "בקשות ממתינות",
    "adm_platoon_new_title": "מחלקה חדשה", "adm_platoon_name": "שם המחלקה",
    "btn_adm_platoon_create": "יצירת מחלקה", "adm_platoon_created": "המחלקה נוצרה.",
    # top-bar bell (chief and admin)
    "bell_label": "התראות", "bell_label_n": "התראות: {n}",
    "bell_title": "בקשות ממתינות", "bell_none": "אין בקשות ממתינות.",
    "bell_approve": "בקשות הקלה לאישור: {n}", "bell_delete": "בקשות למחיקת הקלה: {n}",
    "bell_changes": "בקשות לשינוי צוערים: {n}", "btn_bell_go": "מעבר לבקשה",
    # profile
    "profile_registered": "מועד ההרשמה: {date}",
    "profile_platoon_title": "בחירת מחלקה",
    "profile_platoon_note": "עדיין לא נבחרה מחלקה. אפשר לבחור פעם אחת בלבד, ואחר כך שינוי נעשה על ידי צוות ההדרכה.",
    "profile_platoon_pick": "מחלקה", "btn_profile_platoon": "שמירת המחלקה",
    "profile_platoon_saved": "המחלקה נשמרה.", "profile_platoon_none": "עדיין לא הוגדרו מחלקות.",
    "profile_platoon_choose": "יש לבחור מחלקה לפני השמירה.",
}

# Tips and videos as cards (T7). Substance of the tips is the original app's text, rewritten as short bullets in
# impersonal Hebrew (nothing added): see the report for the list of proposals needing a native-speaker review.
# Shape: {"title", "intro", "cards": [{"icon": Material symbol name, "head", "bullets": [...]}]}.
# Latin words (Zone 2, Grease the Groove) are wrapped in Unicode isolates by the UI (baror/ui/content.py).
TIPS_RUN = {
    "title": "ריצה",
    "intro": "ריצת 3 קילומטרים דורשת שילוב של מהירות, סיבולת לב-ריאה וחוסן מנטלי.",
    "cards": [
        {"icon": "directions_run", "head": "אימוני אינטרוולים (הפוגות)",
         "bullets": ["מומלץ לשלב פעם בשבוע, לשיפור המהירות.",
                     "400 מטר בקצב גבוה (כ-85% מהיכולת), ואחריהם 200 מטר הליכה קלה להתאוששות.",
                     "6 עד 8 חזרות על הצירוף הזה."]},
        {"icon": "timeline", "head": "ריצת נפח (Zone 2)",
         "bullets": ["אימון אחד בשבוע מוקדש לריצה ארוכה ואיטית.",
                     "5 עד 7 ק״מ בקצב שמאפשר לנהל שיחה קלה.",
                     "הריצה בונה את התשתית האירובית ומלמדת את הגוף לייעל את צריכת החמצן."]},
        {"icon": "local_fire_department", "head": "חימום לפני הבוחן",
         "bullets": ["לא לזנק מהמקום.",
                     "10 דקות לפני הזינוק: ריצה קלה מאוד, ואחריה מתיחות דינמיות כמו הנפות רגליים, מכרעים (לאנג׳ים) וסיבובי אגן.",
                     "החימום מונע את תחושת ״הרגליים הכבדות״ בקילומטר הראשון."]},
    ],
}
TIPS_PUSHUPS = {
    "title": "שכיבות סמיכה",
    "intro": "הסוד בשכיבות סמיכה הוא עקביות וטכניקה נכונה. עבודה נכונה מונעת פציעות ומגדילה את כמות החזרות.",
    "cards": [
        {"icon": "fitness_center", "head": "ירידה איטית (השלב האקצנטרי)",
         "bullets": ["השריר נבנה לא רק בדחיפה אלא גם בבלימה.",
                     "ירידה לקרקע בספירה איטית של 3 שניות, ודחיפה למעלה בשנייה אחת.",
                     "כשקשה לדחוף: ירידה איטית לקרקע ודחיפה עם הברכיים."]},
        {"icon": "repeat", "head": "סטים קטנים לאורך היום (Grease the Groove)",
         "bullets": ["במקום אימון אחד קורע פעם ביומיים: סטים קטנים וקלים לאורך כל היום.",
                     "למשל 5 עד 10 שכיבות סמיכה בכל כניסה למגורים.",
                     "זה בונה חיבור עצבי חזק לשריר בלי לעייף אותו לחלוטין."]},
        {"icon": "accessibility_new", "head": "מיקום ידיים ומרפקים",
         "bullets": ["מרפקים קרובים לגוף, בזווית של כ-45 מעלות.",
                     "פתיחת המרפקים החוצה ל-90 מעלות מעמיסה על הכתפיים ומסוכנת.",
                     "כיווץ הישבן והבטן שומר על גב ישר, כמו פלס, ומונע בזבוז אנרגיה."]},
    ],
}
# Summary of the tips above in one table; NOT an official training plan and no weekdays (none were given).
TIPS_WEEK = {
    "icon": "event_available", "head": "מבנה שבועי לפי הטיפים",
    "table": {"columns": ["אימון", "תדירות וקצב"],
              "rows": [["אינטרוולים", "פעם בשבוע: 6 עד 8 חזרות של 400 מטר"],
                       ["ריצה ארוכה", "פעם בשבוע: 5 עד 7 ק״מ בקצב שיחה"],
                       ["שכיבות סמיכה", "לאורך היום: 5 עד 10 שכיבות בכל פעם"]]},
    "note": "סיכום הטיפים שלמעלה, לא תוכנית אימונים רשמית.",
}
VIDEOS = {
    "title": "סרטוני אימון וטכניקה",
    "intro": "סרטונים שעוזרים לדייק את הטכניקה ולשפר תוצאות לקראת הבוחן.",
    "privacy": "הנגן נטען מ-YouTube רק לאחר הפעלת הסרטון.",
    "cards": [
        {"icon": "smart_display", "head": "טכניקה תקנית לשכיבות סמיכה", "url": "https://www.youtube.com/watch?v=IODxDxX7oi4"},
        {"icon": "smart_display", "head": "דגשים לריצת 3 ק״מ ושיפור טכניקת ריצה", "url": "https://www.youtube.com/watch?v=9L2b2khySLE"},
    ],
}


def t(key: str, **params) -> str:
    return M[key].format(**params)


def month_label(d) -> str:
    """'נובמבר 2026' for any date of that month (the Hebrew month name from the catalogue)."""
    return f"{M['report_month_' + str(d.month)]} {d.year}"


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
    "INVALID_NAME": Msg("השם אינו תקין", "השם יכול להכיל אותיות בעברית או בלטינית, רווחים, מקף, נקודה וגרש בלבד, עד 80 תווים.", "תקנו את השדה ונסו שוב."),
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
    # ---- section: software (owned by the software workstream) ----
    "INVALID_ACCOMMODATION": Msg("ההקלה או הפטור לא נשמרו", "הסוג או התחום שנבחרו אינם תקינים.",
                                 "יש לבחור מחדש ולנסות שוב."),
    "ACCOMMODATION_DATES": Msg("התאריכים אינם תקינים",
                               "תאריך הסיום צריך להיות אחרי תאריך ההתחלה, ולא יותר משלוש שנים ממנו.",
                               "יש לתקן את התאריכים ולנסות שוב."),
    "ACCOMMODATION_LIMIT": Msg("לא ניתן להוסיף עוד הקלות או פטורים", "נרשם כבר המספר המרבי של הקלות ופטורים בחשבון.",
                               "יש לפנות לצוות ההדרכה."),
    "ACCOMMODATION_NOTE": Msg("ההערה אינה תקינה",
                              "ההערה יכולה להכיל אותיות, ספרות, רווחים וסימני פיסוק בסיסיים, עד 100 תווים.",
                              "יש לתקן את ההערה ולנסות שוב."),
    # --- v9 reports ---
    "REPORT_RANGE_ORDER": Msg("טווח התאריכים אינו תקין", "תאריך ההתחלה מאוחר מתאריך הסיום.",
                              "יש לתקן את התאריכים ולנסות שוב."),
    "REPORT_RANGE_FUTURE": Msg("תאריך הסיום עתידי", "אי אפשר להפיק דוח על ימים שעוד לא הגיעו.",
                               "יש לבחור תאריך סיום עד היום."),
    "REPORT_RANGE_TOO_LONG": Msg("הטווח ארוך מדי", "הטווח המרבי הוא 5 חודשים קלנדריים.",
                                 "יש לקצר את הטווח ולנסות שוב."),
    # --- v9 people (roles, titles, platoons, change requests; storage errors) ---
    "ROLE_NOT_ALLOWED": Msg("אין הרשאה לשינוי הזה", "השינוי המבוקש אינו מותר לחשבון הנוכחי.",
                            "אפשר לפנות למנהל המערכת."),
    "CHIEF_LIMIT": Msg("לא ניתן למנות עוד בעל תפקיד ברמת הפלוגה", "ברמת הפלוגה יכולים לכהן {max} בעלי תפקיד לכל היותר.",
                       "אפשר לשנות קודם את התפקיד של אחד מהם."),
    "TITLE_REQUIRED": Msg("חסר תואר", "התואר (מ״פ, סמ״פ, מ״מ או מ״כ) נדרש כדי להשלים את הפעולה.", "יש לבחור תואר ולנסות שוב."),
    "TITLE_TAKEN": Msg("התואר כבר מאויש", "ברמת הפלוגה יש מ״פ אחד וסמ״פ אחד.", "יש לבחור תואר אחר."),
    "MM_TAKEN": Msg("במחלקה כבר יש מ״מ", "לכל מחלקה יש מ״מ אחד. כדי להחליף, קודם משנים את המ״מ הנוכחי למ״כ.",
                    "אפשר לנסות שוב אחרי השינוי."),
    "CMDR_LIMIT": Msg("אין מקום למ״כ נוסף במחלקה", "במחלקה יכולים לשרת עד {max} מ״כים.",
                      "אפשר לפנות מקום או לבחור מחלקה אחרת."),
    "PLATOON_REQUIRED": Msg("חסרה מחלקה", "המחלקה לא נבחרה או שאינה קיימת.", "יש לבחור מחלקה ולנסות שוב."),
    "REQUEST_LIMIT": Msg("יש יותר מדי בקשות פתוחות", "אפשר להחזיק עד {max} בקשות פתוחות בו-זמנית.",
                         "כדאי להמתין להכרעה בבקשות הקיימות."),
    "CADET_NOT_REGISTERED": Msg("לא נמצא צוער פעיל עם הכתובת שהוזנה", "הצוער צריך להירשם קודם למערכת.",
                                "אפשר לבדוק את הכתובת ולנסות שוב."),
    "ACCOUNT_CHANGED": Msg("פרטי החשבון השתנו", "השם או מועד ההרשמה אינם תואמים למה שאומת.",
                           "יש לאמת את הזהות מחדש ולנסות שוב."),
    "PLATOON_ALREADY_SET": Msg("המחלקה כבר נקבעה", "מכאן ואילך שינוי מחלקה נעשה על ידי צוות ההדרכה.",
                               "אפשר לפנות למ״מ או למ״פ."),
    "ADMIN_LIMIT": Msg("אי אפשר להוסיף עוד מנהל מערכת", "במערכת יכולים להיות {max} מנהלי מערכת לכל היותר.", ""),
    "LAST_ADMIN": Msg("לא ניתן להשאיר את המערכת בלי מנהל פעיל", "", "יש להגדיר מנהל נוסף לפני השינוי."),
    # --- v9 registration ---
    "SIGNUP_BUSY": Msg("ההרשמה עמוסה כרגע", "הגענו למספר ההרשמות המרבי לשעה האחרונה.",
                       "אפשר לנסות שוב מאוחר יותר."),
    "REGISTRATION_CLOSED": Msg("ההרשמה סגורה כרגע", "פתיחת חשבונות חדשים הושהתה.",
                               "אפשר לפנות לצוות ההדרכה."),
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
