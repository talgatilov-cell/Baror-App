"""DEMO DATA for looking at the app in a browser. Synthetic people, one shared demo password, NEVER for deployment
(the Dockerfile copies an explicit list that does not include demo/). Refuses to touch an existing database file.

    python demo/demo_seed.py demo/demo.db
"""
import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from baror.auth import passwords          # noqa: E402
from baror.storage import accommodations as acc   # noqa: E402
from baror.storage import db, ops, repo   # noqa: E402

PASSWORD = "baror-demo-2026"

ACCOUNTS = [   # (e-mail, role / what you see)
    ("empty@example.com", "cadet, no results yet (empty state)"),
    ("one@example.com", "cadet, one result"),
    ("two@example.com", "cadet, two results"),
    ("twelve@example.com", "cadet, 12 results (full charts)"),
    ("walk@example.com", "cadet on the 6 km walk track"),
    ("ins@example.com", "instructor of platoon א only"),
    ("chief@example.com", "chief / deputy chief (same screen)"),
    ("admin@example.com", "admin: granted every platoon, sees the platoon screen for all of them"),
]


def main(path: str) -> None:
    if Path(path).exists():
        sys.exit(f"{path} already exists: delete it first (this script never modifies an existing database)")
    db.init_db(path)
    pw = passwords.hash_password(PASSWORD)
    rnd = random.Random(7)

    def user(email, name, gender="M", km=False, role="cadet"):
        return repo.create_user(path, email=email, name=name, gender=gender, is_km=km, role=role, pw_hash=pw)

    def results(uid, series):
        start = dt.date.today() - dt.timedelta(days=7 * len(series))
        for i, (run, push) in enumerate(series):
            repo.add_result(path, uid, test_date=(start + dt.timedelta(days=7 * (i + 1))).isoformat(), run_tenths=run, pushups=push)

    user("empty@example.com", "דנה כהן", "F")
    results(user("one@example.com", "איתי ברק"), [(9500, 24)])
    results(user("two@example.com", "נועה לוי", "F"), [(12200, 9), (11700, 12)])
    results(user("twelve@example.com", "יובל מזרחי"),
            [(10800 - i * 190 + rnd.randint(-8, 8) * 10, 14 + i * 2 + rnd.randint(-1, 1)) for i in range(12)])
    results(user("walk@example.com", "מיכל אבן", "F", km=True), [(42000, None), (41000, 10), (40200, None), (39000, 12)])

    platoons = [ops.create_platoon(path, n) for n in ("מחלקה א", "מחלקה ב", "מחלקה ג")]
    people = {
        platoons[0]: ["אביב כוכבי", "בר שחר", "גל אורן", "דור שמש", "הדר כהן", "ורד לוי", "זיו אדם"],
        platoons[1]: ["אורי בן-דוד", "מאיה רז", "עומר אלון", "שיר גולן", "תום ברק", "לירון פז"],
        platoons[2]: ["יואב נחום", "ענבל טל"],        # fewer than 5: hidden from the chief's aggregates
    }
    for platoon, names in people.items():
        for i, name in enumerate(names):
            uid = user(f"p{platoon}_{i}@example.com", name, "F" if i % 2 else "M")
            ops.set_user_platoon(path, uid, platoon)
            if i != 3:
                results(uid, [(rnd.randint(700, 1250) * 10, rnd.randint(8, 40)) for _ in range(rnd.randint(1, 4))])
    ops.grant_platoon(path, user("ins@example.com", "רינה מדריכה", "F", role="instructor"), platoons[0])
    user("chief@example.com", "ראש הקורס", "M", role="chief")
    admin = user("admin@example.com", "מנהל המערכת", "M", role="admin")
    for platoon in platoons:
        ops.grant_platoon(path, admin, platoon)       # explicit grants (audited): the admin is never locked out of the demo
    # --- הקלות ופטורים: one of each source, one expired, one upcoming, one with a pending deletion request
    today = dt.date.today()
    iso = lambda days: (today + dt.timedelta(days=days)).isoformat()
    ids = {r["email"]: r["id"] for r in ops.list_accounts(path)}
    twelve, one, two = ids["twelve@example.com"], ids["one@example.com"], ids["two@example.com"]
    ins, chief = ids["ins@example.com"], ids["chief@example.com"]
    p1 = [i for e, i in ids.items() if e.startswith(f"p{platoons[0]}_")]
    acc.add(path, twelve, twelve, kind="relief", scope="run", start_date=iso(-20), end_date=iso(25), note="הקלה זמנית בריצה")
    acc.add(path, two, two, kind="exemption", scope="pushups", start_date=iso(-60), end_date=iso(-10), note="פטור שפג תוקפו")
    acc.add(path, one, one, kind="relief", scope="both", start_date=iso(5), end_date=iso(40))
    cadet_made = acc.add(path, p1[0], p1[0], kind="exemption", scope="run", start_date=iso(-3), end_date=iso(60), note="הוזן על ידי הצוער")
    acc.add(path, ins, p1[1], kind="relief", scope="pushups", start_date=iso(-10), end_date=iso(20), note="הוזן על ידי המדריכה")
    acc.add(path, chief, p1[2], kind="exemption", scope="both", start_date=iso(-30), end_date=iso(90))
    acc.request_delete(path, ins, cadet_made)         # waits for the chief's approval (visible on the chief screen)
    print(f"demo database written to {path}\n")
    for email, what in ACCOUNTS:
        print(f"  {email:22} {what}")
    print(f"\n  password for every account: {PASSWORD}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
