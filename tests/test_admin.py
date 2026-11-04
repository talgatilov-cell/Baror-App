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
        self.ins = self.staff("i@x.com", platoons=[self.p], title="cmdr")

    def calls(self, actor):
        P = self.path
        return {
            "deactivate_user": lambda: admin.deactivate_user(P, actor, self.cad),
            "reactivate_user": lambda: admin.reactivate_user(P, actor, self.cad),
            "create_platoon": lambda: admin.create_platoon(P, actor, "Z"),
        }

    def state(self):
        with db.reader(self.path) as c:
            return [tuple(r) for t in ("users", "platoons", "instructor_platoons", "cadet_change_requests", "audit_log",
                                       "sessions") for r in c.execute(f"SELECT * FROM {t}")]


class AdminAuthorisation(AdminBase):
    def test_every_public_function_is_covered_by_the_matrix(self):
        # schema v9: roles, titles, grants and the account table moved to baror.storage.people (admin AND chief)
        pub = {n for n, f in inspect.getmembers(admin, inspect.isfunction)
               if not n.startswith("_") and f.__module__ == admin.__name__}
        self.assertEqual(pub, set(self.calls(self.adm)))

    def test_the_removed_web_functions_are_gone(self):
        for name in ("set_role", "grant_platoon", "revoke_platoon", "set_user_platoon", "list_accounts",
                     "add_allowed_email", "remove_allowed_email", "list_allowed_emails"):
            self.assertFalse(hasattr(admin, name), name)

    def test_non_admin_actors_refused_and_nothing_changes(self):
        dead = self.staff("dead@x.com", role="admin")
        ops.deactivate_user(self.path, dead)
        chief = self.staff("chief@x.com", role="chief")
        for label, actor in [("cadet", self.cad), ("instructor", self.ins), ("chief", chief), ("deactivated admin", dead),
                             ("unknown", 9999), ("None", None), ("str", "1"), ("bool", True), ("zero", 0)]:
            before = self.state()
            for name, f in self.calls(actor).items():
                with self.subTest(actor=label, fn=name), self.assertRaises(AuthError):
                    f()
            self.assertEqual(self.state(), before, label)

    def test_role_reread_each_call(self):
        admin.create_platoon(self.path, self.adm, "Z1")
        other = self.staff("a2@x.com", role="admin")
        ops.set_role(self.path, self.adm, "instructor", title="cmdr", platoon_id=self.p)  # other admin remains
        with self.assertRaises(AuthError):
            admin.create_platoon(self.path, self.adm, "Z2")
        self.assertIsInstance(admin.create_platoon(self.path, other, "Z3"), int)

    def test_admin_happy_paths_audit_ids_only(self):
        a = self.adm
        pid = admin.create_platoon(self.path, a, "B")
        admin.deactivate_user(self.path, a, self.ins)
        admin.reactivate_user(self.path, a, self.ins)
        acts = [r[1] for r in self.audit_rows(actor_id=a)]
        for want in ("admin.platoon_create", "admin.deactivate", "admin.reactivate"):
            self.assertIn(want, acts)
        self.assertIn((a, "admin.platoon_create", None, pid, "ok"), self.audit_rows())
        with db.reader(self.path) as c:
            self.assertNotIn("i@x.com", repr([tuple(r) for r in c.execute("SELECT * FROM audit_log")]))

    def test_failed_operation_leaves_no_audit_row(self):
        before = len(self.audit_rows())
        with self.assertRaises(NotFoundError):
            admin.deactivate_user(self.path, self.adm, 9999)
        with self.assertRaises(NotFoundError):
            admin.reactivate_user(self.path, self.adm, 9999)
        with self.assertRaises(ValidationError):
            ops.grant_platoon(self.path, self.cad, self.p)                 # cadets get no platoon grant
        with self.assertRaises(ValidationError):
            ops.grant_platoon(self.path, self.ins, 9999)                   # unknown platoon
        with self.assertRaises(ValidationError):
            ops.set_user_platoon(self.path, self.ins, self.p)              # staff have no cadet platoon
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
                  lambda: ops.deactivate_user(self.path, self.adm),
                  lambda: ops.set_role(self.path, self.adm, "cadet"),
                  lambda: ops.set_role(self.path, self.adm, "instructor", title="cmdr", platoon_id=self.p)):
            with self.assertRaises(ConflictError):
                f()
        self.assertEqual(ops.list_accounts(self.path)[0]["role"], "admin")

    def test_second_admin_allows_then_guard_returns(self):
        a2 = self.staff("a2@x.com", role="admin")
        admin.deactivate_user(self.path, self.adm, a2)  # fine, adm remains
        with self.assertRaises(ConflictError):
            admin.deactivate_user(self.path, self.adm, self.adm)
        admin.reactivate_user(self.path, self.adm, a2)
        ops.set_role(self.path, self.adm, "cadet")      # a2 remains
        with self.assertRaises(ConflictError):
            ops.set_role(self.path, a2, "cadet")

    def test_inactive_admin_does_not_count_as_other_admin(self):
        a2 = self.staff("a2@x.com", role="admin")
        ops.deactivate_user(self.path, a2)
        with self.assertRaises(ConflictError):
            admin.deactivate_user(self.path, self.adm, self.adm)

    def test_set_admin_role_noop_on_last_admin_ok(self):
        ops.set_role(self.path, self.adm, "admin")

    def test_there_are_at_most_two_active_admins(self):
        self.staff("a2@x.com", role="admin")
        with self.assertRaises(ConflictError) as cm:
            ops.set_role(self.path, self.cad, "admin")
        self.assertEqual(cm.exception.message_key, "ADMIN_LIMIT")
        self.assertEqual(repo.get_user(self.path, self.cad)["role"], "cadet")

    def test_an_inactive_admin_does_not_count_toward_the_cap_but_comes_back_only_if_there_is_room(self):
        a2 = self.staff("a2@x.com", role="admin")
        ops.deactivate_user(self.path, a2)
        ops.set_role(self.path, self.cad, "admin")                       # adm + cad = 2 active
        with self.assertRaises(ConflictError) as cm:
            admin.reactivate_user(self.path, self.adm, a2)
        self.assertEqual(cm.exception.message_key, "ADMIN_LIMIT")

    def test_deactivate_revokes_sessions(self):
        with db.transaction(self.path) as c:
            c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES('h',?,'2099-01-01T00:00:00Z')", (self.cad,))
        admin.deactivate_user(self.path, self.adm, self.cad)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT revoked FROM sessions").fetchone()[0], 1)

    def test_role_change_away_from_staff_drops_grants(self):
        ops.set_role(self.path, self.ins, "cadet")
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM instructor_platoons").fetchone()[0], 0)

    def test_promoting_cadet_clears_platoon_membership(self):
        ops.set_role(self.path, self.cad, "instructor", title="cmdr", platoon_id=self.p)
        with db.reader(self.path) as c:
            self.assertIsNone(c.execute("SELECT platoon_id FROM users WHERE id=?", (self.cad,)).fetchone()[0])
            self.assertEqual(c.execute("SELECT platoon_id FROM instructor_platoons WHERE instructor_id=?",
                                       (self.cad,)).fetchone()[0], self.p)

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

    def test_grant_platoon_sets_the_one_platoon_and_does_not_add_one(self):
        pid = ops.create_platoon(self.path, "Q")
        ops.grant_platoon(self.path, self.ins, pid)
        with db.reader(self.path) as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT instructor_id,platoon_id FROM instructor_platoons")],
                             [(self.ins, pid)])
        self.assertEqual({a["id"]: (a["platoon_id"], a["title"]) for a in ops.list_accounts(self.path)}[self.ins], (pid, "cmdr"))

    def test_ops_set_title_and_the_title_rules(self):
        ops.set_title(self.path, self.ins, "mm")
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT is_mm FROM instructor_platoons WHERE instructor_id=?", (self.ins,)).fetchone()[0], 1)
        with self.assertRaises(ValidationError):
            ops.set_title(self.path, self.ins, "mp")                       # a chief title on an instructor
        with self.assertRaises(ValidationError):
            ops.set_title(self.path, self.cad, "cmdr")                     # a cadet carries no title


if __name__ == "__main__":
    unittest.main()
