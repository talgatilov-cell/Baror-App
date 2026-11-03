import os
import subprocess
import sys
import threading
import unittest

from baror.auth import passwords, service, sessions
from baror.errors import AuthError, RegistrationDeniedError, ValidationError
from baror.storage import allowlist, db, ops, repo
from tests.v2base import V2Case

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def reg(path, email, **kw):
    args = dict(email=email, name="Dana", gender="F", is_km=False, pw_hash="scrypt$x")
    args.update(kw)
    return repo.register_with_allowlist(path, **args)


class AllowlistEntries(V2Case):
    def test_add_normalises_and_is_idempotent(self):
        self.assertEqual(allowlist.add_entry(self.path, "  Dana@Example.COM "), allowlist.ADDED)
        self.assertEqual(allowlist.add_entry(self.path, "dana@example.com"), allowlist.UNCHANGED)
        rows = allowlist.list_entries(self.path)
        self.assertEqual([(r["email"], r["status"], r["role"]) for r in rows], [("dana@example.com", "pending", "cadet")])

    def test_update_role_or_platoon_of_pending_entry(self):
        p = self.platoon("A")
        allowlist.add_entry(self.path, "d@example.com")
        self.assertEqual(allowlist.add_entry(self.path, "d@example.com", platoon_id=p), allowlist.UPDATED)
        self.assertEqual(allowlist.list_entries(self.path)[0]["platoon"], "A")
        self.assertEqual(allowlist.add_entry(self.path, "d@example.com", role="instructor"), allowlist.UPDATED)

    def test_validation(self):
        p = self.platoon("A")
        for kw in [dict(email="nope"), dict(email="a@b.com", role="admin"), dict(email="a@b.com", role="root"),
                   dict(email="a@b.com", role="instructor", platoon_id=p), dict(email="a@b.com", platoon_id=999),
                   dict(email=None), dict(email="a b@c.com")]:
            with self.subTest(kw=kw), self.assertRaises(ValidationError):
                allowlist.add_entry(self.path, **kw)
        self.assertEqual(allowlist.list_entries(self.path), [])

    def test_admin_role_is_not_grantable_through_the_allowlist_even_with_raw_sql(self):
        with self.assertRaises(ValidationError):
            with db.transaction(self.path) as c:
                c.execute("INSERT INTO allowed_emails(email,role) VALUES('x@y.com','admin')")

    def test_add_for_existing_account_reports_registered_and_adds_nothing(self):
        self.user("have@example.com")
        self.assertEqual(allowlist.add_entry(self.path, "HAVE@example.com"), allowlist.REGISTERED)
        self.assertEqual(allowlist.list_entries(self.path), [])

    def test_remove(self):
        allowlist.add_entry(self.path, "a@example.com")
        self.assertTrue(allowlist.remove_entry(self.path, "A@example.com"))
        self.assertFalse(allowlist.remove_entry(self.path, "a@example.com"))
        with self.assertRaises(RegistrationDeniedError):
            reg(self.path, "a@example.com")

    def test_removing_entry_never_deletes_the_account_or_its_data(self):
        allowlist.add_entry(self.path, "a@example.com")
        uid = reg(self.path, "a@example.com")
        repo.add_result(self.path, uid, test_date="2026-01-01", pushups=9)
        tok = sessions.issue(self.path, uid)
        self.assertTrue(allowlist.remove_entry(self.path, "a@example.com"))
        self.assertEqual(repo.get_user(self.path, uid)["email"], "a@example.com")
        self.assertEqual(len(repo.list_results(self.path, uid)), 1)
        self.assertIsNotNone(sessions.resolve(self.path, tok))
        self.assertEqual(repo.get_user(self.path, uid)["active"], 1, "removal is not deactivation")

    def test_entry_consumed_by_deleted_account_can_be_rearmed_only_by_explicit_add(self):
        allowlist.add_entry(self.path, "a@example.com")
        uid = reg(self.path, "a@example.com")
        repo.delete_user(self.path, uid)
        with self.assertRaises(RegistrationDeniedError):
            reg(self.path, "a@example.com")
        self.assertEqual(allowlist.add_entry(self.path, "a@example.com"), allowlist.ADDED)
        self.assertIsInstance(reg(self.path, "a@example.com"), int)


class Registration(V2Case):
    def test_listed_email_registers_and_entry_is_consumed(self):
        p = self.platoon("A")
        allowlist.add_entry(self.path, "dana@example.com", platoon_id=p)
        uid = reg(self.path, " Dana@Example.com ")
        u = repo.get_user(self.path, uid)
        self.assertEqual((u["email"], u["role"], u["platoon_id"], u["active"]), ("dana@example.com", "cadet", p, 1))
        e = allowlist.list_entries(self.path)[0]
        self.assertEqual(e["status"], "used")
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT used_by FROM allowed_emails").fetchone()[0], uid)

    def test_instructor_entry_gives_role_but_NO_platoon_access(self):
        allowlist.add_entry(self.path, "i@example.com", role="instructor")
        uid = reg(self.path, "i@example.com")
        self.assertEqual(repo.get_user(self.path, uid)["role"], "instructor")
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM instructor_platoons").fetchone()[0], 0)

    def test_all_refusals_are_one_indistinguishable_error(self):
        allowlist.add_entry(self.path, "used@example.com")
        reg(self.path, "used@example.com")
        self.user("legacy@example.com")                                   # account without an allowlist entry
        allowlist.add_entry(self.path, "dummy@example.com")               # a different listed one, to prove list is non-empty
        allowlist.add_entry(self.path, "both@example.com"); self.user("both@example.com")   # listed AND already has an account
        seen, reasons = set(), set()
        for e in ["unlisted@example.com", "used@example.com", "legacy@example.com", "both@example.com", "not-an-email", "", None, "a b@c.d"]:
            with self.subTest(email=e), self.assertRaises(RegistrationDeniedError) as cm:
                reg(self.path, e)
            x = cm.exception
            seen.add((type(x), x.message_key, x.field, tuple(x.params.items())))
            reasons.add(x.detail)
            self.assertNotIn("@", x.detail, "internal reason must not carry the e-mail")
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual(reasons, {"not_listed", "entry_already_used", "account_exists", "invalid_email"})

    def test_field_errors_do_not_depend_on_list_membership(self):
        allowlist.add_entry(self.path, "listed@example.com")
        for email in ("listed@example.com", "unlisted@example.com"):
            with self.assertRaises(ValidationError) as cm:
                reg(self.path, email, gender="X")
            self.assertEqual(cm.exception.field, "gender")
        self.assertEqual(allowlist.list_entries(self.path)[0]["status"], "pending", "bad input must not burn the entry")

    def test_failed_attempt_does_not_consume_the_entry(self):
        allowlist.add_entry(self.path, "a@example.com")
        with self.assertRaises(ValidationError):
            reg(self.path, "a@example.com", name="   ")
        self.assertIsInstance(reg(self.path, "a@example.com"), int)

    def test_second_registration_of_same_email_is_denied_and_first_account_untouched(self):
        allowlist.add_entry(self.path, "a@example.com")
        uid = reg(self.path, "a@example.com", name="First", pw_hash="h1")
        with self.assertRaises(RegistrationDeniedError):
            reg(self.path, "a@example.com", name="Second", pw_hash="h2")
        self.assertEqual(repo.get_login_record(self.path, "a@example.com")["pw_hash"], "h1")
        self.assertEqual(repo.get_user(self.path, uid)["name"], "First")

    def test_unique_backstop_if_entry_and_account_disagree(self):
        # entry pending but an account with that e-mail exists (created by the trusted path after listing)
        allowlist.add_entry(self.path, "a@example.com")
        self.user("a@example.com")
        with self.assertRaises(RegistrationDeniedError):
            reg(self.path, "a@example.com")

    def test_registration_writes_audit_row_with_ids_only(self):
        allowlist.add_entry(self.path, "a@example.com")
        uid = reg(self.path, "a@example.com")
        self.assertEqual(self.audit_rows(action="auth.register"), [(uid, "auth.register", uid, None, "ok")])

    def test_create_user_is_the_trusted_path_and_ignores_the_allowlist(self):
        self.assertIsInstance(self.user("direct@example.com"), int)   # existing 95 tests rely on this


class ServiceRegisterAndLogin(V2Case):
    def test_service_register_hashes_and_login_works(self):
        allowlist.add_entry(self.path, "dana@example.com")
        u = service.register(self.path, email="dana@example.com", name="Dana", gender="F", is_km=False, password="correct horse")
        self.assertEqual(service.login(self.path, "dana@example.com", "correct horse")["id"], u["id"])
        self.assertNotIn("pw_hash", u)

    def test_service_register_denied_still_pays_for_scrypt(self):
        import time
        t = time.perf_counter()
        with self.assertRaises(RegistrationDeniedError):
            service.register(self.path, email="nobody@example.com", name="N", gender="M", is_km=False, password="pw-long-enough")
        self.assertGreater(time.perf_counter() - t, 0.05, "refusal must cost about one hash (timing equalisation)")

    def test_password_problems_are_field_errors_for_listed_and_unlisted_alike(self):
        allowlist.add_entry(self.path, "l@example.com")
        for e in ("l@example.com", "u@example.com"):
            with self.assertRaises(ValidationError) as cm:
                service.register(self.path, email=e, name="N", gender="M", is_km=False, password="")
            self.assertEqual(cm.exception.field, "password")

    def test_short_password_is_refused_for_listed_and_unlisted_alike_and_keeps_the_entry(self):
        allowlist.add_entry(self.path, "l@example.com")
        for e in ("l@example.com", "u@example.com"):
            with self.assertRaises(ValidationError) as cm:
                service.register(self.path, email=e, name="N", gender="M", is_km=False, password="1234567")
            self.assertEqual((cm.exception.message_key, cm.exception.field), ("INVALID_PASSWORD", "password"))
        self.assertEqual(allowlist.list_entries(self.path, status="pending")[0]["email"], "l@example.com")
        self.assertEqual(repo.get_login_record(self.path, "l@example.com"), None)
        service.register(self.path, email="l@example.com", name="N", gender="M", is_km=False, password="12345678")

    def test_deactivated_user_cannot_log_in_and_error_is_the_generic_one(self):
        uid = self.user("d@example.com", pw_hash=passwords.hash_password("right-password"))
        ops.deactivate_user(self.path, uid)
        keys = set()
        for pw in ("right-password", "wrong-password"):
            with self.assertRaises(AuthError) as cm:
                service.login(self.path, "d@example.com", pw)
            keys.add((type(cm.exception), cm.exception.message_key, str(cm.exception.params)))
        with self.assertRaises(AuthError) as cm:
            service.login(self.path, "ghost@example.com", "x")
        keys.add((type(cm.exception), cm.exception.message_key, str(cm.exception.params)))
        self.assertEqual(len(keys), 1, keys)
        self.assertEqual(repo.get_login_record(self.path, "d@example.com")["failed_logins"], 1,
                         "only the wrong-password attempt counts; the right password on a disabled account does not")

    def test_deactivated_user_login_costs_a_real_scrypt(self):
        import time
        uid = self.user("d@example.com", pw_hash=passwords.hash_password("right-password"))
        ops.deactivate_user(self.path, uid)
        t = time.perf_counter()
        with self.assertRaises(AuthError):
            service.login(self.path, "d@example.com", "right-password")
        self.assertGreater(time.perf_counter() - t, 0.05)

    def test_deactivate_revokes_sessions_and_resolve_refuses_even_unrevoked_tokens(self):
        uid = self.user("d@example.com")
        t1, t2 = sessions.issue(self.path, uid), sessions.issue(self.path, uid)
        ops.deactivate_user(self.path, uid)
        self.assertIsNone(sessions.resolve(self.path, t1)); self.assertIsNone(sessions.resolve(self.path, t2))
        ops.reactivate_user(self.path, uid)
        self.assertIsNone(sessions.resolve(self.path, t1), "revoked sessions stay revoked after reactivation")
        t3 = sessions.issue(self.path, uid)
        with db.transaction(self.path) as c:        # simulate: deactivated by raw SQL WITHOUT revoking
            c.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
        self.assertIsNone(sessions.resolve(self.path, t3), "resolve must check users.active itself")

    def test_role_is_reread_by_resolve(self):
        uid = self.user("i@example.com", role="instructor")
        tok = sessions.issue(self.path, uid)
        self.assertEqual(sessions.resolve(self.path, tok)["role"], "instructor")
        ops.set_role(self.path, uid, "cadet")
        self.assertEqual(sessions.resolve(self.path, tok)["role"], "cadet")


class RegistrationRaces(V2Case):
    def test_same_listed_email_20_threads_exactly_one_winner(self):
        allowlist.add_entry(self.path, "race@example.com")
        outcomes, barrier = [], threading.Barrier(20)

        def go(i):
            barrier.wait()
            try:
                reg(self.path, "race@example.com", name=f"racer{i}", pw_hash=f"hash{i}")
                outcomes.append("ok")
            except RegistrationDeniedError:
                outcomes.append("denied")
            except Exception as e:  # noqa: BLE001
                outcomes.append(repr(e))
        ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual((outcomes.count("ok"), outcomes.count("denied")), (1, 19), outcomes)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], 1)
            name, h = c.execute("SELECT name,pw_hash FROM users").fetchone()
            used_by = c.execute("SELECT used_by FROM allowed_emails").fetchone()[0]
            self.assertEqual(c.execute("SELECT count(*) FROM audit_log WHERE action='auth.register'").fetchone()[0], 1)
        self.assertEqual(name, f"racer{h.removeprefix('hash')}", "name and hash must belong to the same winner")
        self.assertIsNotNone(used_by)

    def test_same_listed_email_10_processes_exactly_one_winner(self):
        allowlist.add_entry(self.path, "race@example.com")
        procs = [subprocess.Popen([sys.executable, "-m", "tests.workers", "register_allowlisted", self.path, "race@example.com"],
                                  cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(10)]
        outs = sorted(p.communicate(timeout=120)[0].strip() for p in procs)
        self.assertEqual(outs.count("OK"), 1, outs)
        self.assertEqual(outs.count("RegistrationDeniedError"), 9, outs)
        self.assertEqual(db.integrity_check(self.path), [])

    def test_many_distinct_listed_emails_all_register_concurrently(self):
        for i in range(15):
            allowlist.add_entry(self.path, f"n{i}@example.com")
        errors, barrier = [], threading.Barrier(15)

        def go(i):
            barrier.wait()
            try:
                reg(self.path, f"n{i}@example.com")
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
        ts = [threading.Thread(target=go, args=(i,)) for i in range(15)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(len(allowlist.list_entries(self.path, status="used")), 15)

    def test_removal_racing_registration_leaves_a_consistent_state(self):
        for i in range(10):
            allowlist.add_entry(self.path, f"r{i}@example.com")
        res, barrier = {}, threading.Barrier(20)

        def reg_(i):
            barrier.wait()
            try:
                reg(self.path, f"r{i}@example.com"); res[("reg", i)] = True
            except RegistrationDeniedError:
                res[("reg", i)] = False

        def rem(i):
            barrier.wait()
            res[("rem", i)] = allowlist.remove_entry(self.path, f"r{i}@example.com")
        ts = [threading.Thread(target=reg_, args=(i,)) for i in range(10)] + [threading.Thread(target=rem, args=(i,)) for i in range(10)]
        [t.start() for t in ts]; [t.join() for t in ts]
        with db.reader(self.path) as c:
            users = {r[0] for r in c.execute("SELECT email FROM users")}
        for i in range(10):
            self.assertEqual(f"r{i}@example.com" in users, res[("reg", i)], "a user exists iff registration reported success")
        self.assertEqual(db.integrity_check(self.path), [])


if __name__ == "__main__":
    unittest.main()
