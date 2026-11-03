import ast
import datetime as dt
import inspect
import os
import unittest

from baror.errors import AuthError, ConflictError, NotFoundError, ValidationError
from baror.storage import admin, audit, db, ops, repo
from tests.v2base import V2Case

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AdminBase(V2Case):
    def setUp(self):
        super().setUp()
        self.adm = self.staff("adm@x.com", role="admin")
        self.p = self.platoon("A")
        self.cad = self.cadet("c@x.com", self.p)
        self.ins = self.staff("i@x.com")

    def calls(self, actor):
        P = self.path
        return {
            "add_allowed_email": lambda: admin.add_allowed_email(P, actor, "n@x.com"),
            "remove_allowed_email": lambda: admin.remove_allowed_email(P, actor, "n@x.com"),
            "list_allowed_emails": lambda: admin.list_allowed_emails(P, actor),
            "list_accounts": lambda: admin.list_accounts(P, actor),
            "deactivate_user": lambda: admin.deactivate_user(P, actor, self.cad),
            "reactivate_user": lambda: admin.reactivate_user(P, actor, self.cad),
            "set_role": lambda: admin.set_role(P, actor, self.cad, "instructor"),
            "create_platoon": lambda: admin.create_platoon(P, actor, "Z"),
            "set_user_platoon": lambda: admin.set_user_platoon(P, actor, self.cad, None),
            "grant_platoon": lambda: admin.grant_platoon(P, actor, self.ins, self.p),
            "revoke_platoon": lambda: admin.revoke_platoon(P, actor, self.ins, self.p),
        }

    def state(self):
        with db.reader(self.path) as c:
            return [tuple(r) for t in ("users", "platoons", "instructor_platoons", "allowed_emails", "audit_log", "sessions")
                    for r in c.execute(f"SELECT * FROM {t}")]


class AdminAuthorisation(AdminBase):
    def test_every_public_function_is_covered_by_the_matrix(self):
        pub = {n for n, f in inspect.getmembers(admin, inspect.isfunction)
               if not n.startswith("_") and f.__module__ == admin.__name__}
        self.assertEqual(pub, set(self.calls(self.adm)))

    def test_non_admin_actors_refused_and_nothing_changes(self):
        dead = self.staff("dead@x.com", role="admin")
        ops.deactivate_user(self.path, dead)
        for label, actor in [("cadet", self.cad), ("instructor", self.ins), ("deactivated admin", dead),
                             ("unknown", 9999), ("None", None), ("str", "1"), ("bool", True), ("zero", 0)]:
            before = self.state()
            for name, f in self.calls(actor).items():
                with self.subTest(actor=label, fn=name), self.assertRaises(AuthError):
                    f()
            self.assertEqual(self.state(), before, label)

    def test_role_reread_each_call(self):
        admin.create_platoon(self.path, self.adm, "Z1")
        other = self.staff("a2@x.com", role="admin")
        ops.set_role(self.path, self.adm, "instructor")  # other admin remains
        with self.assertRaises(AuthError):
            admin.create_platoon(self.path, self.adm, "Z2")
        self.assertIsInstance(admin.create_platoon(self.path, other, "Z3"), int)

    def test_admin_happy_paths_audit_ids_only(self):
        a = self.adm
        admin.add_allowed_email(self.path, a, "New@X.com", platoon_id=self.p)
        self.assertEqual([r["email"] for r in admin.list_allowed_emails(self.path, a)], ["new@x.com"])
        self.assertTrue(admin.remove_allowed_email(self.path, a, "new@x.com"))
        pid = admin.create_platoon(self.path, a, "B")
        admin.set_role(self.path, a, self.cad, "instructor")
        admin.grant_platoon(self.path, a, self.cad, pid)
        self.assertTrue(admin.revoke_platoon(self.path, a, self.cad, pid))
        self.assertFalse(admin.revoke_platoon(self.path, a, self.cad, pid))
        admin.deactivate_user(self.path, a, self.ins)
        admin.reactivate_user(self.path, a, self.ins)
        self.assertEqual(len(admin.list_accounts(self.path, a)), 3)
        acts = [r[1] for r in self.audit_rows(actor_id=a)]
        for want in ("admin.allow_add", "admin.allow_remove", "admin.platoon_create", "admin.set_role", "admin.grant",
                     "admin.revoke", "admin.deactivate", "admin.reactivate"):
            self.assertIn(want, acts)
        with db.reader(self.path) as c:
            self.assertNotIn("new@x.com", repr([tuple(r) for r in c.execute("SELECT * FROM audit_log")]))

    def test_failed_operation_leaves_no_audit_row(self):
        before = len(self.audit_rows())
        with self.assertRaises(NotFoundError):
            admin.deactivate_user(self.path, self.adm, 9999)
        with self.assertRaises(ValidationError):
            admin.set_role(self.path, self.adm, self.cad, "root")
        with self.assertRaises(ValidationError):
            admin.grant_platoon(self.path, self.adm, self.cad, self.p)  # cadets get no grants
        with self.assertRaises(NotFoundError):
            admin.grant_platoon(self.path, self.adm, self.ins, 9999)
        with self.assertRaises(ValidationError):
            admin.set_user_platoon(self.path, self.adm, self.ins, self.p)  # staff have no platoon
        self.assertEqual(len(self.audit_rows()), before)

    def test_ops_is_not_imported_by_app_code(self):
        offenders = []
        for d, _, files in os.walk(os.path.join(ROOT, "baror")):
            for fn in files:
                if not fn.endswith(".py") or fn == "ops.py":
                    continue
                with open(os.path.join(d, fn)) as fh:
                    tree = ast.parse(fh.read())
                for n in ast.walk(tree):
                    names = []
                    if isinstance(n, ast.Import):
                        names = [a.name for a in n.names]
                    elif isinstance(n, ast.ImportFrom):
                        names = [(n.module or "")] + [f"{n.module}.{a.name}" for a in n.names]
                    if any(x.endswith("storage.ops") or x == "ops" for x in names):
                        offenders.append(fn)
        self.assertEqual(offenders, [])


class AdminSafety(AdminBase):
    def test_last_admin_cannot_be_deactivated_or_demoted(self):
        for f in (lambda: admin.deactivate_user(self.path, self.adm, self.adm),
                  lambda: admin.set_role(self.path, self.adm, self.adm, "instructor"),
                  lambda: admin.set_role(self.path, self.adm, self.adm, "cadet"),
                  lambda: ops.deactivate_user(self.path, self.adm),
                  lambda: ops.set_role(self.path, self.adm, "cadet")):
            with self.assertRaises(ConflictError):
                f()
        self.assertEqual(admin.list_accounts(self.path, self.adm)[0]["role"], "admin")

    def test_second_admin_allows_then_guard_returns(self):
        a2 = self.staff("a2@x.com", role="admin")
        admin.deactivate_user(self.path, self.adm, a2)  # fine, adm remains
        with self.assertRaises(ConflictError):
            admin.deactivate_user(self.path, self.adm, self.adm)
        admin.reactivate_user(self.path, self.adm, a2)
        admin.set_role(self.path, a2, self.adm, "instructor")
        with self.assertRaises(ConflictError):
            admin.set_role(self.path, a2, a2, "cadet")

    def test_inactive_admin_does_not_count_as_other_admin(self):
        a2 = self.staff("a2@x.com", role="admin")
        ops.deactivate_user(self.path, a2)
        with self.assertRaises(ConflictError):
            admin.deactivate_user(self.path, self.adm, self.adm)

    def test_set_admin_role_noop_on_last_admin_ok(self):
        admin.set_role(self.path, self.adm, self.adm, "admin")

    def test_deactivate_revokes_sessions(self):
        with db.transaction(self.path) as c:
            c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES('h',?,'2099-01-01T00:00:00Z')", (self.cad,))
        admin.deactivate_user(self.path, self.adm, self.cad)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT revoked FROM sessions").fetchone()[0], 1)

    def test_role_change_away_from_staff_drops_grants(self):
        ops.grant_platoon(self.path, self.ins, self.p)
        admin.set_role(self.path, self.adm, self.ins, "cadet")
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM instructor_platoons").fetchone()[0], 0)

    def test_promoting_cadet_clears_platoon_membership(self):
        admin.set_role(self.path, self.adm, self.cad, "instructor")
        with db.reader(self.path) as c:
            self.assertIsNone(c.execute("SELECT platoon_id FROM users WHERE id=?", (self.cad,)).fetchone()[0])

    def test_duplicate_platoon_and_bad_names(self):
        from baror.errors import BarorError
        with self.assertRaises(BarorError):
            admin.create_platoon(self.path, self.adm, "A")
        for bad in ("", " x", "x" * 61, None, 5):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                admin.create_platoon(self.path, self.adm, bad)


class AuditPurge(AdminBase):
    NOW = dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc)

    def seed(self, days):
        t = audit.ts(self.NOW - dt.timedelta(days=days))
        with db.transaction(self.path) as c:
            c.execute("INSERT INTO audit_log(ts,actor_id,action) VALUES(?,?,'old')", (t, self.adm))

    def test_purge_deletes_only_old_rows_and_records_itself(self):
        self.seed(400); self.seed(366); self.seed(10)
        n = audit.purge(self.path, now=self.NOW)
        self.assertEqual(n, 2)
        self.assertEqual(len(self.audit_rows(action="old")), 1)
        self.assertEqual(self.audit_rows(action="audit.purge"), [(None, "audit.purge", None, None, "ok")])

    def test_purge_boundary_row_exactly_at_cutoff_is_kept(self):
        self.seed(365)
        self.assertEqual(audit.purge(self.path, now=self.NOW), 0)

    def test_purge_rejects_dangerous_arguments(self):
        self.seed(10)
        for bad in (0, -5, True, 1.5, "30", None):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                audit.purge(self.path, older_than_days=bad, now=self.NOW)
        self.assertEqual(len(self.audit_rows(action="old")), 1)

    def test_deleting_account_keeps_history_with_null_actor(self):
        self.seed(1)
        repo.delete_user(self.path, self.adm)
        self.assertEqual(self.audit_rows(action="old"), [(None, "old", None, None, "ok")])

    def test_record_stores_null_for_unknown_ids(self):
        with db.transaction(self.path) as c:
            audit.record(c, actor_id=9999, action="x", target_user_id=8888, platoon_id=7777, outcome="denied")
        self.assertEqual(self.audit_rows(action="x"), [(None, "x", None, None, "denied")])


class OpsPath(AdminBase):
    def test_ops_runs_without_actor_and_audits_null_actor(self):
        pid = ops.create_platoon(self.path, "Q")
        ops.grant_platoon(self.path, self.ins, pid)
        self.assertIn((None, "admin.grant", self.ins, pid, "ok"), self.audit_rows())
        self.assertEqual(len(ops.list_accounts(self.path)), 3)


if __name__ == "__main__":
    unittest.main()
