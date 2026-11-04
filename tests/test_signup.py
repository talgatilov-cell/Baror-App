"""Open registration storage (baror/storage/signup.py): always a cadet, one transaction, hourly cap that survives account
deletion, generic refusal for duplicates, join-link platoon lookup that never raises."""
import datetime as dt
import threading
import unittest

from baror.domain import joinlink
from baror.errors import RegistrationDeniedError, SignupBusyError, ValidationError
from baror.storage import audit, db, people, repo, signup
from tests.base import DbCase

NOW = dt.datetime(2026, 11, 4, 12, 0, 0, tzinfo=dt.timezone.utc)
SECRET = "j" * 40


def reg(path, email="noa@example.com", **kw):
    args = dict(email=email, name="Noa Levi", gender="F", is_km=False, pw_hash="scrypt$x", cap=120, now=NOW)
    args.update(kw)
    return signup.register_cadet(path, **args)


def events(path):
    with db.reader(path) as c:
        return [r["ts"] for r in c.execute("SELECT ts FROM signup_events ORDER BY id")]


class PublicPlatoons(DbCase):
    def test_empty_then_ordered_by_name_with_id_and_name_only(self):
        self.assertEqual(signup.public_platoons(self.path), [])
        with db.transaction(self.path) as c:
            for n in ("Beta", "Alpha"):
                c.execute("INSERT INTO platoons(name) VALUES(?)", (n,))
        rows = signup.public_platoons(self.path)
        self.assertEqual([r["name"] for r in rows], ["Alpha", "Beta"])
        self.assertEqual(set(rows[0]), {"id", "name"})


class Cap(DbCase):
    def test_capacity_counts_the_last_hour_only(self):
        self.assertEqual(signup.capacity_left(self.path, 5, NOW), 5)
        with db.transaction(self.path) as c:
            for minutes in (10, 59, 61, 180):
                c.execute("INSERT INTO signup_events(ts) VALUES(?)", (audit.ts(NOW - dt.timedelta(minutes=minutes)),))
        self.assertEqual(signup.capacity_left(self.path, 5, NOW), 3)
        self.assertEqual(signup.capacity_left(self.path, 2, NOW), 0, "never negative")

    def test_bad_cap_values_fall_back_to_the_default_not_to_unlimited(self):
        for bad in (None, 0, -1, 1001, "5", 2.5, True):
            self.assertEqual(signup.capacity_left(self.path, bad, NOW), 120, repr(bad))
        self.assertEqual(signup.capacity_left(self.path, 1000, NOW), 1000)
        self.assertEqual(signup.capacity_left(self.path, 1, NOW), 1)

    def test_the_cap_is_enforced_and_a_refusal_creates_nothing(self):
        reg(self.path, "a@example.com", cap=2)
        reg(self.path, "b@example.com", cap=2)
        with self.assertRaises(SignupBusyError) as cm:
            reg(self.path, "c@example.com", cap=2)
        self.assertEqual(cm.exception.message_key, "SIGNUP_BUSY")
        self.assertIsNone(repo.get_login_record(self.path, "c@example.com"))
        self.assertEqual(len(events(self.path)), 2)
        later = NOW + dt.timedelta(minutes=61)
        reg(self.path, "c@example.com", cap=2, now=later)             # the window moved on

    def test_deleting_accounts_does_not_reopen_the_cap(self):
        a = reg(self.path, "a@example.com", cap=2)
        b = reg(self.path, "b@example.com", cap=2)
        for uid in (a, b):
            repo.delete_user(self.path, uid)
        self.assertEqual(signup.capacity_left(self.path, 2, NOW), 0)
        with self.assertRaises(SignupBusyError):
            reg(self.path, "c@example.com", cap=2)

    def test_events_older_than_two_hours_are_pruned_on_sign_up(self):
        with db.transaction(self.path) as c:
            c.execute("INSERT INTO signup_events(ts) VALUES(?)", (audit.ts(NOW - dt.timedelta(hours=3)),))
            c.execute("INSERT INTO signup_events(ts) VALUES(?)", (audit.ts(NOW - dt.timedelta(minutes=90)),))
        reg(self.path)
        self.assertEqual(len(events(self.path)), 2, "the 3 h old row is gone, the 90 min old row stays")

    def test_a_busy_refusal_still_prunes_nothing_that_counts(self):
        reg(self.path, "a@example.com", cap=1)
        with self.assertRaises(SignupBusyError):
            reg(self.path, "b@example.com", cap=1)
        self.assertEqual(len(events(self.path)), 1)

    def test_a_cap_filled_between_check_and_insert_is_caught_inside_the_transaction(self):
        """capacity_left said yes, then other sign-ups fill the cap: register_cadet re-checks under the write lock."""
        self.assertEqual(signup.capacity_left(self.path, 1, NOW), 1)
        reg(self.path, "first@example.com", cap=1)
        with self.assertRaises(SignupBusyError):
            reg(self.path, "second@example.com", cap=1)

    def test_concurrent_sign_ups_never_exceed_the_cap(self):
        results, lock = [], threading.Lock()

        def go(i):
            try:
                reg(self.path, f"c{i}@example.com", cap=3, now=None)
                out = "ok"
            except SignupBusyError:
                out = "busy"
            except Exception as exc:                  # noqa: BLE001
                out = repr(exc)
            with lock:
                results.append(out)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(results), ["busy"] * 7 + ["ok"] * 3, results)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], 3)
            self.assertEqual(c.execute("SELECT count(*) FROM signup_events").fetchone()[0], 3)


class Register(DbCase):
    def test_creates_a_cadet_with_a_pseudonym_event_and_audit_row_and_normalises_the_email(self):
        with db.transaction(self.path) as c:
            pid = int(c.execute("INSERT INTO platoons(name) VALUES('A')").lastrowid)
        uid = reg(self.path, "  Noa@Example.COM ", name="  Noa Levi ", is_km=True, platoon_id=pid)
        u = repo.get_user(self.path, uid)
        self.assertEqual((u["email"], u["name"], u["role"], u["platoon_id"], u["is_km"], u["title"]),
                         ("noa@example.com", "Noa Levi", "cadet", pid, 1, None))
        self.assertTrue(u["active"])
        self.assertEqual(len(self.pseudonym_of(uid)), 32)
        self.assertEqual(len(events(self.path)), 1)
        with db.reader(self.path) as c:
            rows = c.execute("SELECT actor_id,action,target_user_id,platoon_id,outcome FROM audit_log").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(uid, "auth.register", uid, pid, "ok")])
        with db.reader(self.path) as c:
            dump = repr([tuple(r) for r in c.execute("SELECT * FROM audit_log")])
        self.assertNotIn("noa", dump.lower(), "the audit trail holds ids only, never the e-mail or the name")

    def test_platoon_is_optional(self):
        self.assertIsNone(repo.get_user(self.path, reg(self.path))["platoon_id"])

    def test_the_role_is_not_a_parameter(self):
        for role in ("instructor", "chief", "admin"):
            with self.assertRaises(TypeError):
                reg(self.path, role=role)
        self.assertEqual(repo.get_user(self.path, reg(self.path, "x@example.com"))["role"], "cadet")

    def test_duplicates_malformed_and_released_look_the_same(self):
        reg(self.path, "used@example.com")
        keys = set()
        for email in ("used@example.com", "USED@example.com", " used@example.com ", "not-an-email", "", None, 7,
                      "a b@example.com", "x" * 300 + "@example.com"):
            with self.assertRaises(RegistrationDeniedError, msg=repr(email)[:20]) as cm:
                reg(self.path, email)
            keys.add((cm.exception.message_key, str(cm.exception.params)))
            self.assertNotIn("used@example.com", str(cm.exception.detail))
        self.assertEqual(keys, {("REGISTRATION_DENIED", "{}")})
        self.assertEqual(len(events(self.path)), 1, "a refusal is not a sign-up")

    def test_field_problems_are_validation_errors_and_create_nothing(self):
        cases = [dict(name=""), dict(name="Noa1"), dict(name="<b>x</b>"), dict(name="x" * 81), dict(name=None),
                 dict(gender="X"), dict(gender=None), dict(platoon_id=999), dict(platoon_id=True),
                 dict(platoon_id="1"), dict(platoon_id=0), dict(platoon_id=-3), dict(platoon_id=2**70)]
        for kw in cases:
            with self.assertRaises(ValidationError, msg=str(kw)):
                reg(self.path, **kw)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM pseudonyms").fetchone()[0], 0)
        self.assertEqual(events(self.path), [])

    def test_a_failure_rolls_everything_back(self):
        with db.transaction(self.path) as c:
            c.execute("INSERT INTO platoons(name) VALUES('A')")
        reg(self.path, "used@example.com")
        with self.assertRaises(RegistrationDeniedError):
            reg(self.path, "used@example.com", platoon_id=1)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM pseudonyms").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM audit_log").fetchone()[0], 1)

    def test_same_email_race_gives_one_account_and_generic_refusals(self):
        out, lock = [], threading.Lock()

        def go():
            try:
                reg(self.path, "race@example.com", now=None)
                r = "ok"
            except RegistrationDeniedError:
                r = "denied"
            except Exception as exc:                  # noqa: BLE001
                r = repr(exc)
            with lock:
                out.append(r)
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(out), ["denied"] * 7 + ["ok"], out)

    def test_a_deactivated_account_still_blocks_its_address(self):
        uid = reg(self.path, "gone@example.com")
        with db.transaction(self.path) as c:
            c.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
        with self.assertRaises(RegistrationDeniedError):
            reg(self.path, "gone@example.com")


class JoinToken(DbCase):
    def setUp(self):
        super().setUp()
        with db.transaction(self.path) as c:
            self.pid = int(c.execute("INSERT INTO platoons(name) VALUES('A')").lastrowid)
        self.exp = int(NOW.timestamp()) + 3600
        self.tok = joinlink.sign(SECRET, self.pid, 0, self.exp)

    def test_valid_token(self):
        self.assertEqual(signup.platoon_from_join_token(self.path, SECRET, self.tok, NOW), self.pid)

    def test_expired_wrong_secret_off_and_unknown_platoon(self):
        self.assertIsNone(signup.platoon_from_join_token(self.path, SECRET, self.tok, NOW + dt.timedelta(hours=2)))
        self.assertIsNone(signup.platoon_from_join_token(self.path, "w" * 40, self.tok, NOW))
        for off in (None, "", "short"):
            self.assertIsNone(signup.platoon_from_join_token(self.path, off, self.tok, NOW), repr(off))
        self.assertIsNone(signup.platoon_from_join_token(self.path, SECRET, joinlink.sign(SECRET, 99, 0, self.exp), NOW))

    def test_bumping_the_epoch_revokes_old_links(self):
        with db.transaction(self.path) as c:
            c.execute("UPDATE platoons SET join_epoch=join_epoch+1 WHERE id=?", (self.pid,))
        self.assertIsNone(signup.platoon_from_join_token(self.path, SECRET, self.tok, NOW))
        fresh = joinlink.sign(SECRET, self.pid, 1, self.exp)
        self.assertEqual(signup.platoon_from_join_token(self.path, SECRET, fresh, NOW), self.pid)

    def test_a_deleted_platoon_gives_none(self):
        with db.transaction(self.path) as c:
            c.execute("DELETE FROM platoons WHERE id=?", (self.pid,))
        self.assertIsNone(signup.platoon_from_join_token(self.path, SECRET, self.tok, NOW))

    def test_it_never_raises(self):
        for token in (None, 5, b"x", "", "junk", "1.2.3", "x" * 9999, "1.0." + "9" * 40 + "." + "a" * 32, "\x00", ["a"]):
            self.assertIsNone(signup.platoon_from_join_token(self.path, SECRET, token, NOW), repr(token)[:20])
        self.assertIsNone(signup.platoon_from_join_token("/nonexistent-dir/none.db", SECRET, self.tok, NOW))
        self.assertIsNone(signup.platoon_from_join_token(self.path, SECRET, self.tok, "not a date"))
        self.assertIsNone(signup.platoon_from_join_token(self.path, 12345, self.tok, NOW))

    def test_tokens_made_by_people_join_link_work_and_rotation_revokes_them(self):
        admin = repo.create_user(self.path, email="adm@example.com", name="Adm", gender="M", is_km=False,
                                 pw_hash="x$h", role="admin")
        token = people.join_link(self.path, admin, self.pid, secret=SECRET, days=14, now=NOW)
        self.assertEqual(signup.platoon_from_join_token(self.path, SECRET, token, NOW), self.pid)
        self.assertIsNone(signup.platoon_from_join_token(self.path, SECRET, token, NOW + dt.timedelta(days=15)))
        people.rotate_join_links(self.path, admin, self.pid, now=NOW)
        self.assertIsNone(signup.platoon_from_join_token(self.path, SECRET, token, NOW))


class NoStaffPath(unittest.TestCase):
    def test_signup_has_no_role_parameter_and_the_allowlist_is_gone(self):
        import inspect
        self.assertNotIn("role", inspect.signature(signup.register_cadet).parameters)
        self.assertFalse(hasattr(repo, "register_with_allowlist"))
        with self.assertRaises(ImportError):
            from baror.storage import allowlist  # noqa: F401


if __name__ == "__main__":
    unittest.main()
