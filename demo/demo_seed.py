"""DEMO DATA for looking at the app in a browser. Synthetic people, one shared demo password, NEVER for deployment
(the Dockerfile copies an explicit list that does not include demo/). Refuses to touch an existing database file.

    python demo/demo_seed.py demo/demo.db

Schema v9: one company, three platoons. People are created like real ones: everybody is a cadet first (repo.create_user),
then the operator path (ops.*) grants the staff roles, exactly as an admin or a chief does it in the app. There is no
allowlist any more.
"""
import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from baror.auth import passwords          # noqa: E402
from baror.storage import accommodations as acc   # noqa: E402
from baror.storage import db, ops, people, repo   # noqa: E402

PASSWORD = "baror-demo-2026"

ACCOUNTS = [   # (e-mail, role / what you see)
    ("empty@example.com", "cadet in platoon א, no results yet (empty state)"),
    ("one@example.com", "cadet, one result"),
    ("two@example.com", "cadet, two results"),
    ("twelve@example.com", "cadet, 12 results (full charts)"),
    ("walk@example.com", "cadet on the 6 km walk track"),
    ("unassigned@example.com", "cadet with NO platoon (profile block to pick one)"),
    ("candidate1@example.com", "registered cadet in platoon א, nominated as מ\"כ (pending request)"),
    ("candidate2@example.com", "registered cadet, no platoon, no staff role (the admin can promote)"),
    ("candidate3@example.com", "registered cadet, no platoon, no staff role (the admin can promote)"),
    ("ins@example.com", "מ\"מ of platoon א (instructor, title mm)"),
    ("cmdr1@example.com", "מ\"כ of platoon א (instructor, title cmdr)"),
    ("cmdr2@example.com", "מ\"כ of platoon א (instructor, title cmdr)"),
    ("insb@example.com", "מ\"מ of platoon ב"),
    ("insc@example.com", "מ\"מ of platoon ג"),
    ("chief@example.com", "מ\"פ (chief, title mp)"),
    ("deputy@example.com", "סמ\"פ (chief, title smp; same screen as the מ\"פ)"),
    ("admin@example.com", "admin: permissions and cadet management only, no platoon grants"),
    ("admin2@example.com", "second admin (the maximum is two)"),
]


def main(path: str) -> None:
    if Path(path).exists():
        sys.exit(f"{path} already exists: delete it first (this script never modifies an existing database)")
    db.init_db(path)
    pw = passwords.hash_password(PASSWORD)
    rnd = random.Random(7)

    def user(email, name, gender="M", km=False):
        """Everybody starts as a cadet, as after a registration; roles are granted afterwards."""
        return repo.create_user(path, email=email, name=name, gender=gender, is_km=km, pw_hash=pw)

    def results(uid, series):
        start = dt.date.today() - dt.timedelta(days=7 * len(series))
        for i, (run, push) in enumerate(series):
            repo.add_result(path, uid, test_date=(start + dt.timedelta(days=7 * (i + 1))).isoformat(), run_tenths=run, pushups=push)

    platoons = [ops.create_platoon(path, n) for n in ("מחלקה א", "מחלקה ב", "מחלקה ג")]
    pa, pb, pc = platoons

    # --- cadets named in the README / checklist
    empty = user("empty@example.com", "דנה כהן", "F")
    one = user("one@example.com", "איתי ברק")
    two = user("two@example.com", "נועה לוי", "F")
    twelve = user("twelve@example.com", "יובל מזרחי")
    walk = user("walk@example.com", "מיכל אבן", "F", km=True)
    results(one, [(9500, 24)])
    results(two, [(12200, 9), (11700, 12)])
    results(twelve, [(10800 - i * 190 + rnd.randint(-8, 8) * 10, 14 + i * 2 + rnd.randint(-1, 1)) for i in range(12)])
    results(walk, [(42000, None), (41000, 10), (40200, None), (39000, 12)])
    for uid in (empty, one, two, twelve, walk):
        ops.set_user_platoon(path, uid, pa)

    # --- the rest of the platoons (platoon ג stays under 5: hidden from the chief's aggregates)
    people_by_platoon = {
        pa: ["אביב כוכבי", "בר שחר", "גל אורן", "דור שמש", "הדר כהן", "ורד לוי", "זיו אדם"],
        pb: ["אורי בן-דוד", "מאיה רז", "עומר אלון", "שיר גולן", "תום ברק", "לירון פז"],
        pc: ["יואב נחום", "ענבל טל"],
    }
    members: dict[int, list[int]] = {pa: [], pb: [], pc: []}
    for platoon, names in people_by_platoon.items():
        for i, name in enumerate(names):
            uid = user(f"p{platoon}_{i}@example.com", name, "F" if i % 2 else "M")
            ops.set_user_platoon(path, uid, platoon)
            members[platoon].append(uid)
            if i != 3:
                results(uid, [(rnd.randint(700, 1250) * 10, rnd.randint(8, 40)) for _ in range(rnd.randint(1, 4))])

    # --- registered cadets with no staff role: one without a platoon with results, two for the admin to promote
    unassigned = user("unassigned@example.com", "רותם ללא מחלקה", "F")
    results(unassigned, [(11500, 15), (11200, 18)])
    candidate1 = user("candidate1@example.com", "נדב מועמד")
    ops.set_user_platoon(path, candidate1, pa)
    user("candidate2@example.com", "טל נרשם", "F")
    user("candidate3@example.com", "עידו נרשם")

    # --- staff: registered as cadets above, now promoted (operator path, same limits as the app)
    def staff(email, name, role, *, title, platoon=None, gender="M"):
        uid = user(email, name, gender)
        ops.set_role(path, uid, role, title=title, platoon_id=platoon)
        return uid

    ins = staff("ins@example.com", "רינה לביא", "instructor", title="mm", platoon=pa, gender="F")
    staff("cmdr1@example.com", "אלון כרמי", "instructor", title="cmdr", platoon=pa)
    staff("cmdr2@example.com", "שרון אביב", "instructor", title="cmdr", platoon=pa, gender="F")
    staff("insb@example.com", "גדי מרום", "instructor", title="mm", platoon=pb)
    staff("insc@example.com", "ליאת ברוש", "instructor", title="mm", platoon=pc, gender="F")
    chief = staff("chief@example.com", "אהרון שדה", "chief", title="mp")
    staff("deputy@example.com", "יעל סגל", "chief", title="smp", gender="F")
    # the two admins: operator script only, never from the app; permissions and cadet management, NO platoon grants
    staff("admin@example.com", "מנהל המערכת", "admin", title=None)
    staff("admin2@example.com", "מנהלת המערכת", "admin", title=None, gender="F")

    # --- הקלות ופטורים: one of each source, one expired, one upcoming, one with a pending deletion request
    today = dt.date.today()
    iso = lambda days: (today + dt.timedelta(days=days)).isoformat()
    p1 = members[pa]
    acc.add(path, twelve, twelve, kind="relief", scope="run", start_date=iso(-20), end_date=iso(25), note="הקלה זמנית בריצה")
    acc.add(path, two, two, kind="exemption", scope="pushups", start_date=iso(-60), end_date=iso(-10), note="פטור שפג תוקפו")
    acc.add(path, one, one, kind="relief", scope="both", start_date=iso(5), end_date=iso(40))
    cadet_made = acc.add(path, p1[0], p1[0], kind="exemption", scope="run", start_date=iso(-3), end_date=iso(60), note="נקע בקרסול")
    acc.add(path, ins, p1[1], kind="relief", scope="pushups", start_date=iso(-10), end_date=iso(20), note="הוזן על ידי המדריכה")
    acc.add(path, chief, p1[2], kind="exemption", scope="both", start_date=iso(-30), end_date=iso(90))
    acc.request_delete(path, ins, cadet_made)         # waits for the chief's approval (visible on the chief screen)

    # --- open requests, so that the bell and the requests section have content
    # a cadet who is already in another platoon: the מ"מ's "add" becomes a request for the chief / admin
    people.add_cadet(path, ins, "p%d_0@example.com" % pb, pa)
    # an edit of a cadet of the own platoon: always a request
    people.edit_cadet(path, ins, p1[3], {"is_km": True})
    # the מ"מ proposes a מ"כ: an admin or a chief approves it after checking the identity outside the system
    people.nominate_cmdr(path, ins, "candidate1@example.com")

    print(f"demo database written to {path}\n")
    for email, what in ACCOUNTS:
        print(f"  {email:24} {what}")
    print(f"\n  password for every account: {PASSWORD}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
