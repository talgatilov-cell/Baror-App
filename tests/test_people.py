"""baror.storage.people (+ repo.set_own_platoon): roles, titles, platoons, cadet management and change requests.

The world every test starts from (``PeopleCase``): platoons P1..P3; admin ``adm``; chief ``mp`` (title mp); P1: מ"מ ``mm1``,
מ"כ ``cm1``, cadets ``a1`` ``a2``; P2: מ"מ ``mm2``, cadet ``b1``; unassigned cadets ``n1`` and ``reg``. The structural
invariant (is_mm <-> title mm AND active, one row per instructor, titles fit roles) is checked after EVERY test and
explicitly after the operations of the flow tests."""
import threading
import unittest
from unittest import mock

from baror import messages_he
from baror.auth import sessions
from baror.domain import roles
from baror.errors import AuthError, ConflictError, NotFoundError, ValidationError
from baror.storage import admin, db, ops, people, repo
from tests.v2base import V2Case

NOW = None


class PeopleCase(V2Case):
    def setUp(self):
        super().setUp()
        self.p1, self.p2, self.p3 = self.platoon("P1"), self.platoon("P2"), self.platoon("P3")
        self.adm = self.make("adm@x.com", "admin")
        self.mp = self.make("mp@x.com", "chief", title="mp")
        self.mm1 = self.make("mm1@x.com", "instructor", title="mm", platoon=self.p1)
        self.cm1 = self.make("cm1@x.com", "instructor", title="cmdr", platoon=self.p1)
        self.mm2 = self.make("mm2@x.com", "instructor", title="mm", platoon=self.p2)
        self.a1 = self.make("a1@x.com", name="Alef", platoon=self.p1)
        self.a2 = self.make("a2@x.com", name="Bet", platoon=self.p1)
        self.b1 = self.make("b1@x.com", name="Gimel", platoon=self.p2)
        self.n1 = self.make("n1@x.com", name="Dalet")
        self.reg = self.make("reg@x.com", name="Hey")
        self.addCleanup(self.check_invariants)

    # ---------------------------------------------------------------- helpers
    def make(self, email, role="cadet", *, title=None, platoon=None, name=None):
        uid = self.user(email, name=name or email.split("@")[0].title())
        if role == "cadet":
            if platoon is not None:
                ops.set_user_platoon(self.path, uid, platoon)
        else:
            ops.set_role(self.path, uid, role, title=title, platoon_id=platoon)
        return uid

    def identity(self, uid):
        u = repo.get_user(self.path, uid)
        return {"expected_name": u["name"], "expected_created_at": u["created_at"]}

    def grant(self, actor, uid, role, title=None, platoon=None):
        return people.set_role(self.path, actor, uid, role, title=title, platoon_id=platoon, **self.identity(uid))

    def u(self, uid):
        with db.reader(self.path) as c:
            r = c.execute("SELECT role,title,platoon_id,active FROM users WHERE id=?", (uid,)).fetchone()
        return tuple(r) if r else None

    def ip(self, uid):
        with db.reader(self.path) as c:
            r = c.execute("SELECT platoon_id,is_mm FROM instructor_platoons WHERE instructor_id=?", (uid,)).fetchone()
        return tuple(r) if r else None

    def reqs(self, **where):
        sql = ("SELECT id,kind,requested_by,target_user_id,platoon_id,new_name,new_gender,new_is_km,new_platoon_id,status,"
               "decided_by FROM cadet_change_requests")
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
        with db.reader(self.path) as c:
            return [tuple(r) for r in c.execute(sql + " ORDER BY id", tuple(where.values()))]

    def state(self):
        with db.reader(self.path) as c:
            return [tuple(r) for t in ("users", "platoons", "instructor_platoons", "cadet_change_requests", "sessions")
                    for r in c.execute(f"SELECT * FROM {t}")]

    def n_audit(self, action=None, outcome=None):
        return len([r for r in self.audit_rows() if (action is None or r[1] == action) and (outcome is None or r[4] == outcome)])

    def check_invariants(self):
        with db.reader(self.path) as c:
            for r in c.execute("SELECT ip.instructor_id AS i, ip.is_mm, u.role, u.title, u.active, u.platoon_id AS upid "
                               "FROM instructor_platoons ip JOIN users u ON u.id=ip.instructor_id"):
                self.assertEqual(r["role"], "instructor", f"row of a {r['role']}")
                self.assertEqual(r["is_mm"], int(r["title"] == "mm" and r["active"] == 1), f"is_mm of {r['i']}")
                self.assertIsNone(r["upid"], "an instructor has no cadet platoon")
            for r in c.execute("SELECT id FROM users WHERE role='instructor' AND title='mm' AND active=1 AND id NOT IN "
                               "(SELECT instructor_id FROM instructor_platoons WHERE is_mm=1)"):
                self.fail(f"active mm {r['id']} without the is_mm row")
            for r in c.execute("SELECT id,role,title FROM users WHERE title IS NOT NULL"):
                self.assertIn(r["title"], roles.TITLES_BY_ROLE.get(r["role"], ()), f"title of {r['id']}")
            self.assertEqual(c.execute("SELECT count(*) FROM users WHERE role NOT IN ('instructor') AND id IN "
                                       "(SELECT instructor_id FROM instructor_platoons)").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM users WHERE role='cadet' AND platoon_id IS NOT NULL AND "
                                       "platoon_id NOT IN (SELECT id FROM platoons)").fetchone()[0], 0)


# ===================================================================================================== set_role
class SetRoleRules(PeopleCase):
    def test_admin_promotes_an_already_registered_cadet_who_keeps_results_and_session(self):
        repo.add_result(self.path, self.a1, test_date="2026-01-01", run_tenths=900, pushups=30)
        tok = sessions.issue(self.path, self.a1)
        self.grant(self.adm, self.a1, "instructor", "cmdr", self.p1)
        self.assertEqual(self.u(self.a1), ("instructor", "cmdr", None, 1))
        self.assertEqual(self.ip(self.a1), (self.p1, 0))
        self.assertEqual(len(repo.list_results(self.path, self.a1)), 1)
        got = sessions.resolve(self.path, tok)
        self.assertEqual((got["role"], got["title"], got["platoon_id"]), ("instructor", "cmdr", self.p1))
        self.assertIn((self.adm, "role.to_instructor", self.a1, self.p1, "ok"), self.audit_rows())

    def test_a_staff_member_who_registered_as_a_cadet_is_simply_promoted_never_blocked(self):
        for actor, uid, role, title, platoon in ((self.adm, self.n1, "chief", "smp", None),
                                                 (self.mp, self.reg, "instructor", "cmdr", self.p3)):
            self.grant(actor, uid, role, title, platoon)
            self.assertEqual(self.u(uid)[0], role)

    def test_the_audit_rows_hold_ids_and_codes_only(self):
        self.grant(self.adm, self.reg, "instructor", "cmdr", self.p3)
        self.grant(self.adm, self.n1, "chief", "smp")
        people.set_role(self.path, self.adm, self.reg, "cadet")
        with db.reader(self.path) as c:
            dump = repr([tuple(r) for r in c.execute("SELECT * FROM audit_log")])
        for secret in ("reg@x.com", "n1@x.com", "Hey", "Dalet"):
            self.assertNotIn(secret, dump)
        self.assertEqual(sorted(r[1] for r in self.audit_rows(actor_id=self.adm) if r[1].startswith("role.")),
                         ["role.to_cadet", "role.to_chief", "role.to_instructor"])

    def test_promotion_clears_the_cadet_platoon_and_demotion_clears_title_and_grant(self):
        self.grant(self.mp, self.a1, "instructor", "cmdr", self.p2)
        self.assertEqual((self.u(self.a1)[2], self.ip(self.a1)), (None, (self.p2, 0)))
        people.set_role(self.path, self.mp, self.a1, "cadet")
        self.assertEqual(self.u(self.a1), ("cadet", None, None, 1))
        self.assertIsNone(self.ip(self.a1))

    def test_chief_to_instructor_and_back_by_admin(self):
        self.grant(self.adm, self.mp, "instructor", "cmdr", self.p3)
        self.assertEqual((self.u(self.mp), self.ip(self.mp)), (("instructor", "cmdr", None, 1), (self.p3, 0)))
        self.grant(self.adm, self.mp, "chief", "mp")
        self.assertEqual((self.u(self.mp), self.ip(self.mp)), (("chief", "mp", None, 1), None))

    def test_inactive_and_unknown_targets_are_not_found(self):
        ops.deactivate_user(self.path, self.reg)
        for target in (self.reg, 9999, None, True, "1", 0, -1, 2 ** 70):
            with self.subTest(target=target), self.assertRaises(NotFoundError):
                people.set_role(self.path, self.adm, target, "cadet")

    def test_unknown_role_is_a_validation_error_after_authorisation(self):
        with self.assertRaises(ValidationError):
            people.set_role(self.path, self.adm, self.reg, "commander")
        with self.assertRaises(AuthError):
            people.set_role(self.path, self.reg, self.n1, "commander")

    def test_nobody_grants_admin_and_nobody_touches_an_admin_or_themselves(self):
        a2 = self.make("adm2@x.com", "admin")
        for actor in (self.adm, self.mp):
            with self.assertRaises(AuthError) as cm:
                people.set_role(self.path, actor, self.reg, "admin")
            self.assertEqual(cm.exception.message_key, "ROLE_NOT_ALLOWED")
        for role in ("cadet", "instructor", "chief"):
            with self.assertRaises(AuthError):
                people.set_role(self.path, self.adm, a2, role, title="cmdr", platoon_id=self.p1)   # admin target, even for an admin
            with self.assertRaises(NotFoundError):
                people.set_role(self.path, self.mp, a2, role, title="cmdr", platoon_id=self.p1)    # a chief does not even see it
        with self.assertRaises(AuthError):
            people.set_role(self.path, self.mp, self.mp, "cadet")                                    # nobody changes their own role
        self.assertEqual(self.u(a2)[0], "admin")

    def test_a_chief_cannot_grant_or_remove_the_chief_role_and_cannot_touch_a_chief(self):
        smp = self.make("smp@x.com", "chief", title="smp")
        for call in (lambda: self.grant(self.mp, self.reg, "chief", "mp"),
                     lambda: people.set_role(self.path, self.mp, smp, "cadet"),
                     lambda: people.set_role(self.path, self.mp, smp, "instructor", title="cmdr", platoon_id=self.p1)):
            with self.assertRaises(AuthError) as cm:
                call()
            self.assertEqual(cm.exception.message_key, "ROLE_NOT_ALLOWED")
        self.assertEqual(self.u(smp)[0], "chief")

    def test_a_grant_failing_the_limits_changes_nothing_and_keeps_pending_requests(self):
        for i in range(roles.MAX_CMDRS_PER_PLATOON - 1):                 # cm1 + 4 = 5 cmdrs in P1
            self.make(f"fill{i}@x.com", "instructor", title="cmdr", platoon=self.p1)
        people.add_cadet(self.path, self.mm1, "b1@x.com", self.p1)       # a pending request about b1
        before = self.state()
        with self.assertRaises(ConflictError) as cm:
            self.grant(self.adm, self.b1, "instructor", "cmdr", self.p1)
        self.assertEqual(cm.exception.message_key, "CMDR_LIMIT")
        self.assertEqual(self.state(), before)
        self.assertEqual(self.reqs(target_user_id=self.b1)[0][9], "pending")


class GrantIdentityCheck(PeopleCase):
    def test_grants_to_instructor_or_chief_need_both_expected_values(self):
        ident = self.identity(self.reg)
        for role, title, plat in (("instructor", "cmdr", self.p3), ("chief", "smp", None)):
            for kw in ({}, {"expected_name": ident["expected_name"]}, {"expected_created_at": ident["expected_created_at"]},
                       {"expected_name": None, "expected_created_at": None}, {"expected_name": 5, "expected_created_at": 6}):
                with self.subTest(role=role, kw=kw), self.assertRaises(ValidationError):
                    people.set_role(self.path, self.adm, self.reg, role, title=title, platoon_id=plat, **kw)
        self.assertEqual(self.u(self.reg)[0], "cadet")

    def test_account_changed_when_the_name_or_the_registration_time_no_longer_match(self):
        ident = self.identity(self.reg)
        for kw in ({"expected_name": ident["expected_name"] + "x", "expected_created_at": ident["expected_created_at"]},
                   {"expected_name": ident["expected_name"], "expected_created_at": "2000-01-01T00:00:00Z"}):
            with self.assertRaises(ConflictError) as cm:
                people.set_role(self.path, self.adm, self.reg, "instructor", title="cmdr", platoon_id=self.p3, **kw)
            self.assertEqual(cm.exception.message_key, "ACCOUNT_CHANGED")
        self.assertEqual(self.u(self.reg)[0], "cadet")

    def test_the_account_is_replaced_under_the_same_row_id_is_caught(self):
        """The cadet verified out-of-band was Hey; by the time of the click the person behind the row changed."""
        ident = self.identity(self.reg)
        repo.update_profile(self.path, self.reg, name="Somebody Else")
        with self.assertRaises(ConflictError) as cm:
            people.set_role(self.path, self.adm, self.reg, "chief", title="smp", **ident)
        self.assertEqual(cm.exception.message_key, "ACCOUNT_CHANGED")

    def test_a_demotion_needs_no_identity_check(self):
        people.set_role(self.path, self.adm, self.cm1, "cadet")
        self.assertEqual(self.u(self.cm1)[0], "cadet")


class StructureLimits(PeopleCase):
    def test_at_most_two_active_chiefs_with_unique_titles(self):
        smp = self.make("smp@x.com", "chief", title="smp")
        with self.assertRaises(ConflictError) as cm:
            self.grant(self.adm, self.reg, "chief", "mp")
        self.assertEqual(cm.exception.message_key, "CHIEF_LIMIT")
        self.assertEqual(cm.exception.params, {"max": roles.MAX_CHIEFS})
        people.set_role(self.path, self.adm, smp, "cadet")                        # a seat frees up...
        with self.assertRaises(ConflictError) as cm:
            self.grant(self.adm, self.reg, "chief", "mp")                         # ...the title is still taken
        self.assertEqual(cm.exception.message_key, "TITLE_TAKEN")
        self.grant(self.adm, self.reg, "chief", "smp")
        self.assertEqual(self.u(self.reg), ("chief", "smp", None, 1))

    def test_a_deactivated_chief_does_not_hold_a_seat_or_a_title(self):
        ops.deactivate_user(self.path, self.mp)
        self.grant(self.adm, self.reg, "chief", "mp")
        self.grant(self.adm, self.n1, "chief", "smp")

    def test_titles_must_fit_the_role(self):
        for role, title, plat in (("chief", None, None), ("chief", "mm", None), ("chief", "cmdr", None),
                                  ("instructor", None, self.p3), ("instructor", "mp", self.p3), ("instructor", "smp", self.p3),
                                  ("instructor", "boss", self.p3)):
            with self.subTest(role=role, title=title), self.assertRaises(ValidationError) as cm:
                self.grant(self.adm, self.reg, role, title, plat)
            self.assertEqual(cm.exception.message_key, "TITLE_REQUIRED")
        self.assertEqual(self.u(self.reg)[0], "cadet")

    def test_an_instructor_needs_an_existing_platoon(self):
        for plat in (None, 9999, 0, True, "1"):
            with self.subTest(platoon=plat), self.assertRaises(ValidationError) as cm:
                self.grant(self.adm, self.reg, "instructor", "cmdr", plat)
            self.assertEqual(cm.exception.message_key, "PLATOON_REQUIRED")

    def test_one_mm_per_platoon_and_replacing_him_takes_two_steps(self):
        with self.assertRaises(ConflictError) as cm:
            self.grant(self.adm, self.reg, "instructor", "mm", self.p1)
        self.assertEqual(cm.exception.message_key, "MM_TAKEN")
        self.grant(self.adm, self.reg, "instructor", "mm", self.p3)               # another platoon has none
        with self.assertRaises(ConflictError):
            people.set_title(self.path, self.adm, self.cm1, "mm")                 # P1 still has mm1
        people.set_title(self.path, self.adm, self.mm1, "cmdr")                   # step 1: the old one steps down
        self.check_invariants()
        people.set_title(self.path, self.adm, self.cm1, "mm")                     # step 2: the new one
        self.assertEqual((self.ip(self.mm1), self.ip(self.cm1)), ((self.p1, 0), (self.p1, 1)))

    def test_a_platoon_may_have_no_mm_for_a_while(self):
        people.set_title(self.path, self.mp, self.mm1, "cmdr")
        self.assertIsNone([p for p in people.list_platoons(self.path, self.adm) if p["id"] == self.p1][0]["mm_user_id"])

    def test_at_most_five_cmdrs_per_platoon_and_the_message_carries_the_number(self):
        for i in range(roles.MAX_CMDRS_PER_PLATOON - 1):
            self.make(f"fill{i}@x.com", "instructor", title="cmdr", platoon=self.p1)
        self.assertEqual([p for p in people.list_platoons(self.path, self.adm) if p["id"] == self.p1][0]["cmdrs"], 5)
        with self.assertRaises(ConflictError) as cm:
            self.grant(self.mp, self.reg, "instructor", "cmdr", self.p1)
        self.assertEqual(cm.exception.message_key, "CMDR_LIMIT")
        self.assertIn(str(roles.MAX_CMDRS_PER_PLATOON), messages_he.render_exception(cm.exception))
        self.grant(self.mp, self.reg, "instructor", "cmdr", self.p3)              # another platoon has room
        self.assertEqual(self.u(self.reg)[1], "cmdr")

    def test_the_cmdr_limit_comes_from_the_constant_not_from_a_literal(self):
        with mock.patch.object(roles, "MAX_CMDRS_PER_PLATOON", 2):
            self.grant(self.adm, self.reg, "instructor", "cmdr", self.p1)         # cm1 + reg = 2
            with self.assertRaises(ConflictError) as cm:
                self.grant(self.adm, self.n1, "instructor", "cmdr", self.p1)
            self.assertEqual(cm.exception.params, {"max": 2})
            self.assertIn("2", messages_he.render_exception(cm.exception))
            self.assertNotIn("5", messages_he.render_exception(cm.exception))

    def test_a_legacy_instructor_without_a_title_counts_toward_the_cmdr_limit(self):
        legacy = [self.staff(f"leg{i}@x.com", platoons=[self.p3]) for i in range(roles.MAX_CMDRS_PER_PLATOON)]
        self.assertEqual(self.ip(legacy[0]), (self.p3, 0))
        self.assertEqual([p for p in people.list_platoons(self.path, self.adm) if p["id"] == self.p3][0]["cmdrs"], 5)
        with self.assertRaises(ConflictError) as cm:
            self.grant(self.adm, self.reg, "instructor", "cmdr", self.p3)
        self.assertEqual(cm.exception.message_key, "CMDR_LIMIT")
        people.set_title(self.path, self.adm, legacy[0], "cmdr")                  # giving a title does not need a free seat
        people.set_title(self.path, self.adm, legacy[1], "mm")                    # the platoon has no mm: one becomes it
        self.grant(self.adm, self.reg, "instructor", "cmdr", self.p3)             # ...which frees a cmdr seat

    def test_platoons_already_over_the_limits_are_left_alone(self):
        legacy = []
        for i in range(8):                                  # what the migration leaves behind: written below the limit checks
            uid = self.user(f"over{i}@x.com", role="instructor")
            with db.transaction(self.path) as c:
                c.execute("INSERT INTO instructor_platoons(instructor_id,platoon_id) VALUES(?,?)", (uid, self.p3))
            legacy.append(uid)
        self.assertEqual([p for p in people.list_platoons(self.path, self.adm) if p["id"] == self.p3][0]["cmdrs"], 8)
        people.set_title(self.path, self.adm, legacy[0], "cmdr")                  # changing a title in such a platoon works
        with self.assertRaises(ConflictError):
            self.grant(self.adm, self.reg, "instructor", "cmdr", self.p3)         # new grants are what the limit stops

    def test_a_failed_chief_grant_by_a_chief_is_a_role_refusal_not_a_limit_message(self):
        with self.assertRaises(AuthError):
            self.grant(self.mp, self.reg, "chief", "smp")


class FullMatrix(V2Case):
    """Every actor role x every (from, to) role pair against what ``roles.may_change`` says, in a minimal world."""

    def setUp(self):
        super().setUp()
        self.p = self.platoon("P")
        self.actors = {"admin": self.user("adm@x.com", role="admin"), "chief": self.user("ch@x.com", role="chief"),
                       "instructor": self.user("in@x.com", role="instructor"), "cadet": self.user("ca@x.com")}

    def target(self, role, n):
        return self.user(f"t{n}@x.com", name=f"T {n}", role=role)

    def test_matrix(self):
        n = 0
        for actor_role, actor in self.actors.items():
            for src in ("cadet", "instructor", "chief", "admin"):
                for dst in ("cadet", "instructor", "chief", "admin"):
                    n += 1
                    t = self.target(src, n)
                    u = repo.get_user(self.path, t)
                    title = {"instructor": "cmdr", "chief": "smp"}.get(dst)
                    with self.subTest(actor=actor_role, src=src, dst=dst):
                        call = lambda: people.set_role(self.path, actor, t, dst, title=title, platoon_id=self.p,
                                                       expected_name=u["name"], expected_created_at=u["created_at"])
                        if actor_role in ("instructor", "cadet"):
                            with self.assertRaises(AuthError):
                                call()
                        elif src == "admin" and actor_role == "chief":
                            with self.assertRaises(NotFoundError):
                                call()
                        elif not roles.may_change(actor_role, src, dst):
                            with self.assertRaises(AuthError):
                                call()
                        else:
                            call()
                            self.assertEqual(repo.get_user(self.path, t)["role"], dst)
                        if repo.get_user(self.path, t)["role"] == "admin":
                            continue
                    repo.delete_user(self.path, t)        # keep the chief / seat counts the same for the next case


class PendingRequestsOnRoleChange(PeopleCase):
    def test_pending_requests_about_the_user_and_made_by_them_are_rejected(self):
        people.edit_cadet(self.path, self.mm1, self.a1, {"name": "Aleph"})
        people.delete_cadet(self.path, self.mm1, self.a2)
        people.nominate_cmdr(self.path, self.mm1, "reg@x.com")
        self.assertEqual({r[9] for r in self.reqs()}, {"pending"})
        self.grant(self.adm, self.a1, "instructor", "cmdr", self.p3)              # about a1: closed
        self.assertEqual([r[9] for r in self.reqs(target_user_id=self.a1)], ["rejected"])
        people.set_role(self.path, self.adm, self.mm1, "cadet")                   # made by mm1: all closed
        self.assertEqual({r[9] for r in self.reqs()}, {"rejected"})
        self.assertEqual(self.n_audit("request.approve"), 0)

    def test_deactivating_an_account_closes_the_requests_about_it(self):
        people.delete_cadet(self.path, self.mm1, self.a1)
        admin.deactivate_user(self.path, self.adm, self.a1)
        self.assertEqual([r[9] for r in self.reqs()], ["rejected"])


# ================================================================================================ titles etc.
class SetTitle(PeopleCase):
    def test_admin_sets_chief_and_instructor_titles(self):
        self.grant(self.adm, self.reg, "chief", "smp")
        people.set_title(self.path, self.adm, self.reg, "smp")           # same title again: harmless
        ops.deactivate_user(self.path, self.mp)
        people.set_role(self.path, self.adm, self.reg, "cadet")
        ops.reactivate_user(self.path, self.mp)
        people.set_title(self.path, self.adm, self.mp, "smp")
        self.assertEqual(self.u(self.mp)[1], "smp")
        self.assertIn((self.adm, "title.set", self.mp, None, "ok"), self.audit_rows())

    def test_chief_title_clash(self):
        smp = self.make("smp@x.com", "chief", title="smp")
        with self.assertRaises(ConflictError) as cm:
            people.set_title(self.path, self.adm, smp, "mp")
        self.assertEqual(cm.exception.message_key, "TITLE_TAKEN")

    def test_a_chief_can_title_instructors_only_and_never_themselves(self):
        people.set_title(self.path, self.mp, self.cm1, "cmdr")
        smp = self.make("smp@x.com", "chief", title="smp")
        for target in (self.mp, smp):
            with self.assertRaises(AuthError):
                people.set_title(self.path, self.mp, target, "smp")
        with self.assertRaises(AuthError):                       # an instructor titles nobody
            people.set_title(self.path, self.mm1, self.cm1, "cmdr")

    def test_wrong_title_for_the_role_and_untitled_roles(self):
        for target, title in ((self.cm1, "mp"), (self.cm1, None), (self.cm1, "boss"), (self.mp, "mm"), (self.mp, None)):
            with self.subTest(target=target, title=title), self.assertRaises(ValidationError):
                people.set_title(self.path, self.adm, target, title)
        for target in (self.a1, self.adm, 9999, None):
            with self.subTest(target=target), self.assertRaises((NotFoundError, AuthError)):
                people.set_title(self.path, self.adm, target, "cmdr")

    def test_mm_to_cmdr_and_back_keeps_the_flag_in_step(self):
        people.set_title(self.path, self.adm, self.mm1, "cmdr")
        self.assertEqual(self.ip(self.mm1), (self.p1, 0))
        people.set_title(self.path, self.adm, self.mm1, "mm")
        self.assertEqual(self.ip(self.mm1), (self.p1, 1))

    def test_cmdr_limit_when_a_mm_steps_down_into_a_full_platoon(self):
        for i in range(roles.MAX_CMDRS_PER_PLATOON - 1):
            self.make(f"fill{i}@x.com", "instructor", title="cmdr", platoon=self.p1)
        with self.assertRaises(ConflictError) as cm:
            people.set_title(self.path, self.adm, self.mm1, "cmdr")
        self.assertEqual(cm.exception.message_key, "CMDR_LIMIT")
        self.assertEqual(self.ip(self.mm1), (self.p1, 1))


class MoveInstructor(PeopleCase):
    def test_moves_a_cmdr_and_rejects_the_instructors_open_requests(self):
        people.delete_cadet(self.path, self.cm1, self.a1)
        self.assertTrue(people.move_instructor(self.path, self.mp, self.cm1, self.p3))
        self.assertEqual(self.ip(self.cm1), (self.p3, 0))
        self.assertEqual([r[9] for r in self.reqs()], ["rejected"])
        self.assertIn((self.mp, "instructor.move", self.cm1, self.p3, "ok"), self.audit_rows())

    def test_same_platoon_changes_nothing(self):
        before = self.n_audit()
        self.assertFalse(people.move_instructor(self.path, self.adm, self.cm1, self.p1))
        self.assertEqual(self.n_audit(), before)

    def test_the_destination_limits_apply(self):
        with self.assertRaises(ConflictError) as cm:
            people.move_instructor(self.path, self.adm, self.mm1, self.p2)         # P2 already has mm2
        self.assertEqual(cm.exception.message_key, "MM_TAKEN")
        for i in range(roles.MAX_CMDRS_PER_PLATOON):
            self.make(f"fill{i}@x.com", "instructor", title="cmdr", platoon=self.p3)
        with self.assertRaises(ConflictError) as cm:
            people.move_instructor(self.path, self.adm, self.cm1, self.p3)
        self.assertEqual(cm.exception.message_key, "CMDR_LIMIT")
        self.assertEqual((self.ip(self.mm1), self.ip(self.cm1)), ((self.p1, 1), (self.p1, 0)))

    def test_a_mm_moves_into_a_platoon_without_one_and_stays_mm(self):
        people.move_instructor(self.path, self.adm, self.mm1, self.p3)
        self.assertEqual(self.ip(self.mm1), (self.p3, 1))
        self.assertEqual(self.u(self.mm1)[1], "mm")

    def test_only_active_instructors_and_real_platoons(self):
        for target in (self.a1, self.mp, self.adm, 9999, None):
            with self.subTest(target=target), self.assertRaises(NotFoundError):
                people.move_instructor(self.path, self.adm, target, self.p3)
        for plat in (None, 9999, True):
            with self.subTest(platoon=plat), self.assertRaises(ValidationError):
                people.move_instructor(self.path, self.adm, self.cm1, plat)
        ops.deactivate_user(self.path, self.cm1)
        with self.assertRaises(NotFoundError):
            people.move_instructor(self.path, self.adm, self.cm1, self.p3)


class ListPlatoons(PeopleCase):
    def test_counts_and_shape(self):
        ops.deactivate_user(self.path, self.a2)
        rows = {p["name"]: p for p in people.list_platoons(self.path, self.mp)}
        self.assertEqual(set(rows["P1"]), {"id", "name", "cadets", "mm_user_id", "cmdrs"})
        self.assertEqual((rows["P1"]["cadets"], rows["P1"]["mm_user_id"], rows["P1"]["cmdrs"]), (1, self.mm1, 1))
        self.assertEqual((rows["P2"]["cadets"], rows["P2"]["mm_user_id"], rows["P2"]["cmdrs"]), (1, self.mm2, 0))
        self.assertEqual((rows["P3"]["cadets"], rows["P3"]["mm_user_id"], rows["P3"]["cmdrs"]), (0, None, 0))
        self.assertEqual([p["name"] for p in people.list_platoons(self.path, self.adm)], ["P1", "P2", "P3"])

    def test_a_deactivated_instructor_is_not_counted_and_success_is_not_audited(self):
        ops.deactivate_user(self.path, self.cm1)
        n = self.n_audit()
        self.assertEqual({p["name"]: p["cmdrs"] for p in people.list_platoons(self.path, self.adm)}["P1"], 0)
        self.assertEqual(self.n_audit(), n)

    def test_instructors_and_cadets_are_refused(self):
        for actor in (self.mm1, self.a1):
            with self.assertRaises(AuthError):
                people.list_platoons(self.path, actor)


class ActiveFlagKeepsTheDatabaseRuleInStep(PeopleCase):
    def test_deactivate_frees_the_mm_seat_and_reactivate_restores_or_refuses(self):
        admin.deactivate_user(self.path, self.adm, self.mm1)
        self.assertEqual(self.ip(self.mm1), (self.p1, 0))
        self.check_invariants()
        people.set_title(self.path, self.adm, self.cm1, "mm")                     # the seat is free
        with self.assertRaises(ConflictError) as cm:
            admin.reactivate_user(self.path, self.adm, self.mm1)
        self.assertEqual(cm.exception.message_key, "MM_TAKEN")
        self.assertEqual(self.u(self.mm1)[3], 0, "a refused reactivation leaves the account inactive")
        people.set_title(self.path, self.adm, self.cm1, "cmdr")
        admin.reactivate_user(self.path, self.adm, self.mm1)
        self.assertEqual(self.ip(self.mm1), (self.p1, 1))

    def test_reactivating_a_chief_over_a_taken_title_or_seat_is_refused(self):
        admin.deactivate_user(self.path, self.adm, self.mp)
        self.grant(self.adm, self.reg, "chief", "mp")
        with self.assertRaises(ConflictError) as cm:
            admin.reactivate_user(self.path, self.adm, self.mp)
        self.assertEqual(cm.exception.message_key, "TITLE_TAKEN")
        self.grant(self.adm, self.n1, "chief", "smp")
        with self.assertRaises(ConflictError) as cm:
            admin.reactivate_user(self.path, self.adm, self.mp)
        self.assertIn(cm.exception.message_key, ("TITLE_TAKEN", "CHIEF_LIMIT"))

    def test_reactivating_a_cmdr_into_a_full_platoon_is_refused(self):
        admin.deactivate_user(self.path, self.adm, self.cm1)
        for i in range(roles.MAX_CMDRS_PER_PLATOON):
            self.make(f"fill{i}@x.com", "instructor", title="cmdr", platoon=self.p1)
        with self.assertRaises(ConflictError) as cm:
            admin.reactivate_user(self.path, self.adm, self.cm1)
        self.assertEqual(cm.exception.message_key, "CMDR_LIMIT")

    def test_a_role_grant_to_an_inactive_account_keeps_the_flag_zero(self):
        ops.deactivate_user(self.path, self.reg)
        ops.set_role(self.path, self.reg, "instructor", title="mm", platoon_id=self.p3)
        self.assertEqual(self.ip(self.reg), (self.p3, 0))


class InvariantAfterEveryOperation(PeopleCase):
    def test_a_long_flow(self):
        steps = [
            lambda: self.grant(self.adm, self.reg, "instructor", "cmdr", self.p1),
            lambda: people.set_title(self.path, self.adm, self.mm1, "cmdr"),
            lambda: people.set_title(self.path, self.mp, self.reg, "mm"),
            lambda: people.move_instructor(self.path, self.mp, self.reg, self.p3),
            lambda: admin.deactivate_user(self.path, self.adm, self.reg),
            lambda: self.grant(self.adm, self.n1, "instructor", "mm", self.p3),
            lambda: ops.set_title(self.path, self.mm1, "mm"),
            lambda: people.set_role(self.path, self.mp, self.n1, "cadet"),
            lambda: admin.reactivate_user(self.path, self.adm, self.reg),
            lambda: people.set_role(self.path, self.adm, self.reg, "cadet"),
            lambda: ops.set_role(self.path, self.reg, "instructor", title="mm", platoon_id=self.p3),
            lambda: ops.revoke_platoon(self.path, self.reg, self.p3),
            lambda: ops.grant_platoon(self.path, self.mm1, self.p3),
            lambda: people.set_role(self.path, self.adm, self.mm2, "chief", title="smp", expected_name="Mm2",
                                    expected_created_at=repo.get_user(self.path, self.mm2)["created_at"]),
            lambda: people.set_role(self.path, self.adm, self.mm2, "instructor", title="mm", platoon_id=self.p2,
                                    expected_name="Mm2", expected_created_at=repo.get_user(self.path, self.mm2)["created_at"]),
        ]
        for i, step in enumerate(steps):
            try:
                step()
            except ConflictError:
                pass                                                        # a refused step must also leave a clean state
            self.check_invariants()


# ================================================================================================== accounts
class SearchAccounts(PeopleCase):
    def search(self, actor=None, **kw):
        return people.search_accounts(self.path, actor or self.adm, **kw)

    def test_shape_total_and_order_newest_first(self):
        r = self.search()
        self.assertEqual(r["total"], len(r["rows"]))
        self.assertEqual(set(r["rows"][0]), {"id", "email", "name", "gender", "is_km", "role", "title", "platoon_id", "platoon",
                                             "active", "created_at"})
        keys = [(x["created_at"], x["id"]) for x in r["rows"]]
        self.assertEqual(keys, sorted(keys, reverse=True))
        self.assertNotIn("pw_hash", r["rows"][0])

    def test_query_matches_email_or_name_case_insensitively(self):
        self.assertEqual([x["id"] for x in self.search(query="  A1@X.com ")["rows"]], [self.a1])
        self.assertEqual([x["id"] for x in self.search(query="gimel")["rows"]], [self.b1])
        self.assertEqual(self.search(query="zzz-none")["total"], 0)

    def test_like_metacharacters_are_literal(self):
        weird = self.make("p%q@x.com", name="Pct")
        under = self.make("a_b@x.com", name="Und")
        self.make("axb@x.com", name="Plain")
        self.make("back\\slash@x.com", name="Back")
        self.assertEqual([x["id"] for x in self.search(query="p%q")["rows"]], [weird])
        self.assertEqual(self.search(query="%")["total"], 1)
        self.assertEqual([x["id"] for x in self.search(query="a_b")["rows"]], [under])
        self.assertEqual(self.search(query="_")["total"], 1)
        self.assertEqual(self.search(query="\\")["total"], 1)

    def test_role_and_platoon_filters_use_the_instructors_platoon_too(self):
        self.assertEqual({x["email"] for x in self.search(role="instructor")["rows"]}, {"mm1@x.com", "cm1@x.com", "mm2@x.com"})
        self.assertEqual({x["email"] for x in self.search(platoon_id=self.p1)["rows"]},
                         {"mm1@x.com", "cm1@x.com", "a1@x.com", "a2@x.com"})
        row = [x for x in self.search(platoon_id=self.p1)["rows"] if x["id"] == self.mm1][0]
        self.assertEqual((row["platoon"], row["title"]), ("P1", "mm"))
        with self.assertRaises(ValidationError):
            self.search(role="boss")
        with self.assertRaises(ValidationError):
            self.search(platoon_id="1")

    def test_a_chief_never_sees_admin_rows_an_admin_does(self):
        self.assertIn(self.adm, [x["id"] for x in self.search(self.adm)["rows"]])
        for kw in ({}, {"role": "admin"}, {"query": "adm"}):
            r = self.search(self.mp, **kw)
            self.assertNotIn("admin", {x["role"] for x in r["rows"]})
        self.assertEqual(self.search(self.mp, role="admin"), {"rows": [], "total": 0})
        self.assertEqual(self.search(self.mp)["total"], self.search(self.adm)["total"] - 1)

    def test_limits_offset_and_query_length(self):
        for i in range(120):
            self.make(f"bulk{i}@x.com", name=f"Bulk {i}")
        self.assertEqual(len(self.search(limit=1000)["rows"]), 100)
        self.assertEqual(len(self.search(limit=0)["rows"]), 1)
        self.assertEqual(len(self.search(limit=-5)["rows"]), 1)
        self.assertEqual(self.search(limit=10, offset=5)["rows"][0]["id"], self.search(limit=15)["rows"][5]["id"])
        self.assertEqual(self.search(offset=-3)["rows"][0]["id"], self.search()["rows"][0]["id"])
        self.assertGreater(self.search()["total"], 120)
        self.search(query="x" * 80)
        with self.assertRaises(ValidationError):
            self.search(query="x" * 81)
        with self.assertRaises(ValidationError):
            self.search(query=None)

    def test_inactive_accounts_are_listed_with_their_flag(self):
        ops.deactivate_user(self.path, self.a2)
        self.assertEqual([x["active"] for x in self.search(query="a2@x")["rows"]], [0])

    def test_one_audit_row_per_search_without_the_query(self):
        before = self.n_audit("people.search", "ok")
        self.search(query="secret-needle@x.com")
        self.search(self.mp, query="hey")
        self.assertEqual(self.n_audit("people.search", "ok"), before + 2)
        with db.reader(self.path) as c:
            self.assertNotIn("needle", repr([tuple(r) for r in c.execute("SELECT * FROM audit_log")]))

    def test_instructors_and_cadets_are_refused(self):
        for actor in (self.mm1, self.a1):
            with self.assertRaises(AuthError):
                self.search(actor)


# ============================================================================================ cadet flows
class AddCadet(PeopleCase):
    def add(self, actor, email, platoon):
        return people.add_cadet(self.path, actor, email, platoon)

    def test_instructor_assigns_a_registered_cadet_without_a_platoon(self):
        r = self.add(self.mm1, "  N1@X.com ", self.p1)
        self.assertEqual(r, {"status": "assigned", "request_id": None})
        self.assertEqual(self.u(self.n1)[2], self.p1)
        self.assertIn((self.mm1, "cadet.move", self.n1, self.p1, "ok"), self.audit_rows())

    def test_a_cmdr_has_the_same_rights_as_the_mm(self):
        self.assertEqual(self.add(self.cm1, "n1@x.com", self.p1)["status"], "assigned")

    def test_same_platoon_is_unchanged_and_writes_nothing(self):
        before, n = self.state(), self.n_audit()
        self.assertEqual(self.add(self.mm1, "a1@x.com", self.p1), {"status": "unchanged", "request_id": None})
        self.assertEqual((self.state(), self.n_audit()), (before, n))

    def test_a_cadet_of_another_platoon_becomes_one_request_for_the_chief_or_admin(self):
        r = self.add(self.mm1, "b1@x.com", self.p1)
        self.assertEqual(r["status"], "requested")
        self.assertEqual(self.u(self.b1)[2], self.p2, "nothing moved yet")
        self.assertEqual(self.reqs(), [(r["request_id"], "add_conflict", self.mm1, self.b1, self.p1, None, None, None, None,
                                        "pending", None)])
        self.assertEqual(self.add(self.mm1, "b1@x.com", self.p1)["request_id"], r["request_id"], "no second request")
        self.assertEqual(len(self.reqs()), 1)
        self.assertEqual(self.n_audit("request.create"), 1)

    def test_unknown_staff_and_inactive_emails_give_the_same_not_found(self):
        ops.deactivate_user(self.path, self.a2)
        seen = set()
        for email in ("nobody@x.com", "mm2@x.com", "mp@x.com", "adm@x.com", "a2@x.com"):
            with self.assertRaises(NotFoundError) as cm:
                self.add(self.mm1, email, self.p1)
            seen.add((type(cm.exception), cm.exception.message_key))
        self.assertEqual(seen, {(NotFoundError, "CADET_NOT_REGISTERED")})
        self.assertEqual(self.n_audit(outcome="denied"), 5)

    def test_malformed_email_is_a_validation_error(self):
        with self.assertRaises(ValidationError):
            self.add(self.mm1, "not-an-email", self.p1)

    def test_an_instructor_cannot_add_to_a_platoon_that_is_not_theirs(self):
        for platoon in (self.p2, self.p3, 9999, None, True):
            with self.subTest(platoon=platoon), self.assertRaises(NotFoundError):
                self.add(self.mm1, "n1@x.com", platoon)
        self.assertIsNone(self.u(self.n1)[2])

    def test_a_legacy_instructor_without_a_platoon_cannot_add(self):
        legacy = self.staff("legacy@x.com")
        with self.assertRaises(NotFoundError):
            self.add(legacy, "n1@x.com", self.p1)

    def test_admin_and_chief_assign_move_and_leave_unchanged_without_a_request(self):
        for actor in (self.adm, self.mp):
            self.assertEqual(self.add(actor, "n1@x.com", self.p3), {"status": "assigned", "request_id": None})
            self.assertEqual(self.add(actor, "n1@x.com", self.p3), {"status": "unchanged", "request_id": None})
            self.assertEqual(self.add(actor, "n1@x.com", self.p2), {"status": "moved", "request_id": None})
            self.assertEqual(self.u(self.n1)[2], self.p2)
            ops.set_user_platoon(self.path, self.n1, None)
        self.assertEqual(self.reqs(), [])
        with self.assertRaises(NotFoundError):
            self.add(self.adm, "n1@x.com", 9999)

    def test_a_direct_move_closes_the_requests_about_the_cadet(self):
        people.delete_cadet(self.path, self.mm2, self.b1)
        self.add(self.adm, "b1@x.com", self.p3)
        self.assertEqual([r[9] for r in self.reqs()], ["rejected"])


class EditCadet(PeopleCase):
    def test_admin_and_chief_apply_at_once(self):
        for actor in (self.adm, self.mp):
            r = people.edit_cadet(self.path, actor, self.a1, {"name": "Aleph", "gender": "F", "is_km": True, "platoon_id": self.p3})
            self.assertEqual(r, {"status": "applied", "request_id": None})
            row = repo.get_user(self.path, self.a1)
            self.assertEqual((row["name"], row["gender"], row["is_km"], row["platoon_id"]), ("Aleph", "F", 1, self.p3))
            people.edit_cadet(self.path, actor, self.a1, {"name": "Alef", "gender": "M", "is_km": 0, "platoon_id": self.p1})
        self.assertEqual(self.n_audit("cadet.edit"), 4)

    def test_only_the_four_fields_are_editable(self):
        for changes in ({}, None, [], {"role": "chief"}, {"email": "x@y.com"}, {"name": "A", "pw_hash": "x"}, {"active": 0},
                        {"results": []}, {"title": "mm"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                people.edit_cadet(self.path, self.adm, self.a1, changes)
        self.assertEqual(self.reqs(), [])

    def test_values_are_validated(self):
        for changes in ({"name": ""}, {"name": "Bad<tag>"}, {"name": "x" * 81}, {"name": 5}, {"gender": "X"}, {"gender": None},
                        {"is_km": "yes"}, {"is_km": 2}, {"platoon_id": 9999}, {"platoon_id": None}, {"platoon_id": True}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                people.edit_cadet(self.path, self.mp, self.a1, changes)

    def test_results_are_never_touched_by_an_edit(self):
        repo.add_result(self.path, self.a1, test_date="2026-01-01", run_tenths=900, pushups=30)
        people.edit_cadet(self.path, self.adm, self.a1, {"platoon_id": self.p2})
        self.assertEqual(len(repo.list_results(self.path, self.a1)), 1)

    def test_an_instructor_always_files_a_request_for_own_platoon_cadets_only(self):
        r = people.edit_cadet(self.path, self.mm1, self.a1, {"name": "Aleph", "platoon_id": self.p2})
        self.assertEqual(r["status"], "requested")
        self.assertEqual(repo.get_user(self.path, self.a1)["name"], "Alef", "nothing applied")
        self.assertEqual(self.reqs()[0][1:9], ("edit", self.mm1, self.a1, self.p1, "Aleph", None, None, self.p2))
        self.assertEqual(people.edit_cadet(self.path, self.mm1, self.a1, {"gender": "F"})["request_id"], r["request_id"])
        for target in (self.b1, self.n1, self.mm2, 9999):
            with self.subTest(target=target), self.assertRaises(NotFoundError):
                people.edit_cadet(self.path, self.mm1, target, {"name": "X"})

    def test_approving_applies_the_requested_values(self):
        r = people.edit_cadet(self.path, self.mm1, self.a1, {"name": "Aleph", "gender": "F", "is_km": 1, "platoon_id": self.p3})
        people.approve_change(self.path, self.mp, r["request_id"])
        row = repo.get_user(self.path, self.a1)
        self.assertEqual((row["name"], row["gender"], row["is_km"], row["platoon_id"]), ("Aleph", "F", 1, self.p3))
        self.assertEqual(self.reqs()[0][9], "approved")


class RemoveFromPlatoon(PeopleCase):
    def test_delete_means_remove_from_platoon_and_keeps_everything_else(self):
        repo.add_result(self.path, self.a1, test_date="2026-01-01", run_tenths=900, pushups=30)
        tok = sessions.issue(self.path, self.a1)
        r = people.delete_cadet(self.path, self.adm, self.a1)
        self.assertEqual(r, {"status": "applied", "request_id": None})
        self.assertEqual(self.u(self.a1), ("cadet", None, None, 1))
        self.assertIsNotNone(sessions.resolve(self.path, tok), "the session is kept")
        self.assertEqual(len(repo.list_results(self.path, self.a1)), 1)
        self.assertIn((self.adm, "cadet.remove", self.a1, self.p1, "ok"), self.audit_rows())
        self.assertEqual(people.delete_cadet(self.path, self.mp, self.a1), {"status": "unchanged", "request_id": None})

    def test_open_requests_are_closed_delete_as_approved_the_rest_as_rejected(self):
        e = people.edit_cadet(self.path, self.mm1, self.a1, {"name": "Aleph"})["request_id"]
        d = people.delete_cadet(self.path, self.cm1, self.a1)["request_id"]
        people.delete_cadet(self.path, self.mp, self.a1)
        got = {r[0]: r[9] for r in self.reqs()}
        self.assertEqual((got[e], got[d]), ("rejected", "approved"))

    def test_an_instructor_files_a_request_and_the_chief_decides(self):
        r = people.delete_cadet(self.path, self.mm1, self.a1)
        self.assertEqual(r["status"], "requested")
        self.assertEqual(self.u(self.a1)[2], self.p1)
        self.assertEqual(people.delete_cadet(self.path, self.mm1, self.a1)["request_id"], r["request_id"])
        people.reject_change(self.path, self.mp, r["request_id"])
        self.assertEqual(self.u(self.a1)[2], self.p1)
        r2 = people.delete_cadet(self.path, self.mm1, self.a1)
        self.assertNotEqual(r2["request_id"], r["request_id"], "a closed request does not block a new one")
        people.approve_change(self.path, self.adm, r2["request_id"])
        self.assertEqual(self.u(self.a1), ("cadet", None, None, 1))

    def test_an_instructor_cannot_remove_other_platoons_unassigned_or_staff_accounts(self):
        for target in (self.b1, self.n1, self.mm2, self.mp, 9999, None):
            with self.subTest(target=target), self.assertRaises(NotFoundError):
                people.delete_cadet(self.path, self.mm1, target)
        self.assertEqual(self.reqs(), [])

    def test_admin_and_chief_cannot_remove_staff_or_inactive_accounts(self):
        ops.deactivate_user(self.path, self.a2)
        for target in (self.mm1, self.mp, self.adm, self.a2):
            with self.subTest(target=target), self.assertRaises(NotFoundError):
                people.delete_cadet(self.path, self.adm, target)

    def test_move_cadet(self):
        people.edit_cadet(self.path, self.mm1, self.a1, {"name": "Aleph"})
        self.assertEqual(people.move_cadet(self.path, self.mp, self.a1, self.p3), {"status": "moved"})
        self.assertEqual(self.u(self.a1)[2], self.p3)
        self.assertEqual([r[9] for r in self.reqs()], ["rejected"])
        self.assertEqual(people.move_cadet(self.path, self.adm, self.a1, self.p3), {"status": "unchanged"})
        self.assertEqual(people.move_cadet(self.path, self.adm, self.a1, None), {"status": "moved"})
        self.assertIsNone(self.u(self.a1)[2])
        for target, plat in ((self.mm1, self.p1), (self.a1, 9999), (9999, self.p1)):
            with self.assertRaises(NotFoundError):
                people.move_cadet(self.path, self.adm, target, plat)


# =============================================================================================== requests
class Decisions(PeopleCase):
    def test_approve_and_reject_are_decided_once(self):
        r1 = people.delete_cadet(self.path, self.mm1, self.a1)["request_id"]
        people.approve_change(self.path, self.mp, r1)
        for again in (lambda: people.approve_change(self.path, self.adm, r1), lambda: people.reject_change(self.path, self.adm, r1)):
            with self.assertRaises(NotFoundError):
                again()
        r2 = people.delete_cadet(self.path, self.mm2, self.b1)["request_id"]
        people.reject_change(self.path, self.mp, r2)
        for again in (lambda: people.approve_change(self.path, self.adm, r2), lambda: people.reject_change(self.path, self.mp, r2)):
            with self.assertRaises(NotFoundError):
                again()
        self.assertEqual(self.u(self.b1)[2], self.p2, "a rejected request changed nothing")
        self.assertEqual([r[9] for r in self.reqs()], ["approved", "rejected"])
        self.assertEqual(self.reqs()[0][10], self.mp)

    def test_unknown_ids(self):
        for rid in (9999, 0, -1, None, True, "1", 2 ** 70):
            for fn in (people.approve_change, people.reject_change):
                with self.subTest(rid=rid, fn=fn.__name__), self.assertRaises(NotFoundError):
                    fn(self.path, self.adm, rid)

    def test_add_conflict_moves_the_cadet_on_approval(self):
        r = people.add_cadet(self.path, self.mm1, "b1@x.com", self.p1)["request_id"]
        self.assertEqual(people.approve_change(self.path, self.adm, r), {"status": "approved", "kind": "add_conflict"})
        self.assertEqual(self.u(self.b1)[2], self.p1)
        self.assertEqual(self.n_audit("request.approve"), 1)

    def test_the_target_changed_in_the_meantime_the_approval_is_refused_and_reject_still_clears_it(self):
        r = people.delete_cadet(self.path, self.mm1, self.a1)["request_id"]
        with db.transaction(self.path) as c:                              # e.g. an operator deactivated the account directly
            c.execute("UPDATE users SET active=0 WHERE id=?", (self.a1,))
        with self.assertRaises(NotFoundError):
            people.approve_change(self.path, self.mp, r)
        people.reject_change(self.path, self.mp, r)
        self.assertEqual(self.reqs()[0][9], "rejected")

    def test_approving_a_delete_or_a_move_closes_the_other_open_requests_about_the_cadet(self):
        e = people.edit_cadet(self.path, self.mm1, self.a1, {"name": "Aleph"})["request_id"]
        d = people.delete_cadet(self.path, self.mm1, self.a1)["request_id"]
        people.approve_change(self.path, self.mp, d)
        self.assertEqual({r[0]: r[9] for r in self.reqs()}, {e: "rejected", d: "approved"})

    def test_a_name_only_edit_leaves_the_other_requests_alone(self):
        e = people.edit_cadet(self.path, self.mm1, self.a1, {"name": "Aleph"})["request_id"]
        d = people.delete_cadet(self.path, self.mm1, self.a1)["request_id"]
        people.approve_change(self.path, self.mp, e)
        self.assertEqual({r[0]: r[9] for r in self.reqs()}, {e: "approved", d: "pending"})

    def test_approval_by_instructors_and_cadets_is_refused(self):
        r = people.delete_cadet(self.path, self.mm1, self.a1)["request_id"]
        for actor in (self.mm1, self.mm2, self.a1):
            for fn in (people.approve_change, people.reject_change):
                with self.assertRaises(AuthError):
                    fn(self.path, actor, r)
        self.assertEqual(self.reqs()[0][9], "pending")

    def test_race_only_one_of_many_concurrent_approvals_wins(self):
        r = people.delete_cadet(self.path, self.mm1, self.a1)["request_id"]
        out, barrier = [], threading.Barrier(6)

        def worker(i):
            barrier.wait()
            try:
                (people.approve_change if i % 2 else people.reject_change)(self.path, self.adm if i % 3 else self.mp, r)
                out.append("ok")
            except NotFoundError:
                out.append("nf")
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(out), ["nf"] * 5 + ["ok"])
        self.assertEqual(len([x for x in self.reqs() if x[9] != "pending"]), 1)
        self.assertEqual(self.n_audit("request.approve", "ok") + self.n_audit("request.reject", "ok"), 1)
        self.assertEqual(self.n_audit("request.approve", "denied") + self.n_audit("request.reject", "denied"), 5)

    def test_the_approval_audit_row_holds_ids_only(self):
        r = people.edit_cadet(self.path, self.mm1, self.a1, {"name": "Secret Name"})["request_id"]
        people.approve_change(self.path, self.mp, r)
        with db.reader(self.path) as c:
            dump = repr([tuple(x) for x in c.execute("SELECT * FROM audit_log")])
        self.assertNotIn("Secret", dump)
        self.assertIn((self.mp, "request.approve", self.a1, self.p1, "ok"), self.audit_rows())


class RequestLimits(PeopleCase):
    def test_at_most_thirty_open_requests_per_requester(self):
        ids = [self.make(f"lim{i}@x.com", name=f"Lim {i}", platoon=self.p1) for i in range(31)]
        for uid in ids[:30]:
            people.delete_cadet(self.path, self.mm1, uid)
        with self.assertRaises(ValidationError) as cm:
            people.delete_cadet(self.path, self.mm1, ids[30])
        self.assertEqual(cm.exception.message_key, "REQUEST_LIMIT")
        self.assertEqual(people.delete_cadet(self.path, self.mm1, ids[0])["status"], "requested", "a duplicate is not a new one")
        people.delete_cadet(self.path, self.cm1, ids[30])                       # the limit is per requester
        people.reject_change(self.path, self.mp, self.reqs(requested_by=self.mm1)[0][0])
        people.delete_cadet(self.path, self.mm1, ids[30])


class Nominations(PeopleCase):
    def nominate(self, email="reg@x.com", actor=None):
        return people.nominate_cmdr(self.path, actor or self.mm1, email)

    def approve(self, rid, actor=None, who=None, **kw):
        who = who or self.reg
        return people.approve_change(self.path, actor or self.mp, rid, **{**self.identity(who), **kw})

    def test_a_mm_nominates_a_registered_cadet_for_his_own_platoon(self):
        r = self.nominate("  REG@x.com ")
        self.assertEqual(r["status"], "requested")
        self.assertEqual(self.reqs(), [(r["request_id"], "nominate_cmdr", self.mm1, self.reg, self.p1, None, None, None, None,
                                        "pending", None)])
        self.assertIn((self.mm1, "request.create", self.reg, self.p1, "ok"), self.audit_rows())
        self.assertEqual(self.nominate()["request_id"], r["request_id"])
        self.assertEqual(self.u(self.reg)[0], "cadet", "nothing is granted by the nomination")

    def test_only_an_active_mm_may_nominate(self):
        for actor in (self.cm1, self.a1, self.mp, self.adm, self.staff("legacy@x.com", platoons=[self.p3])):
            with self.assertRaises(AuthError):
                self.nominate(actor=actor)
        ops.deactivate_user(self.path, self.mm1)
        with self.assertRaises(AuthError):
            self.nominate()
        self.assertEqual(self.reqs(), [])

    def test_the_platoon_is_the_nominators_and_cannot_be_chosen(self):
        self.assertEqual(self.nominate(actor=self.mm2)["status"], "requested")
        self.assertEqual(self.reqs()[0][4], self.p2)

    def test_unknown_staff_and_inactive_emails_give_the_same_answer(self):
        ops.deactivate_user(self.path, self.a2)
        seen = set()
        for email in ("nobody@x.com", "mm2@x.com", "mp@x.com", "adm@x.com", "a2@x.com"):
            with self.assertRaises(NotFoundError) as cm:
                self.nominate(email)
            seen.add((cm.exception.message_key, str(cm.exception)))
        self.assertEqual(len(seen), 1)
        self.assertEqual(next(iter(seen))[0], "CADET_NOT_REGISTERED")

    def test_at_most_five_open_nominations(self):
        ids = [self.make(f"nom{i}@x.com", name=f"Nom {i}") for i in range(6)]
        for i in range(5):
            self.nominate(f"nom{i}@x.com")
        with self.assertRaises(ValidationError) as cm:
            self.nominate("nom5@x.com")
        self.assertEqual(cm.exception.message_key, "REQUEST_LIMIT")
        self.assertEqual(cm.exception.params, {"max": 5})
        self.assertEqual(len(ids), 6)

    def test_approval_needs_the_verified_identity_and_makes_a_cmdr_of_that_platoon(self):
        rid = self.nominate()["request_id"]
        for kw in ({"expected_name": None, "expected_created_at": None},):
            with self.assertRaises(ValidationError):
                people.approve_change(self.path, self.mp, rid, **kw)
        with self.assertRaises(ValidationError):
            people.approve_change(self.path, self.mp, rid)
        with self.assertRaises(ConflictError) as cm:
            people.approve_change(self.path, self.mp, rid, expected_name="Other", expected_created_at=self.identity(self.reg)["expected_created_at"])
        self.assertEqual(cm.exception.message_key, "ACCOUNT_CHANGED")
        with self.assertRaises(ConflictError):
            people.approve_change(self.path, self.mp, rid, expected_name="Hey", expected_created_at="2000-01-01T00:00:00Z")
        self.assertEqual((self.u(self.reg)[0], self.reqs()[0][9]), ("cadet", "pending"))
        self.assertEqual(self.approve(rid), {"status": "approved", "kind": "nominate_cmdr"})
        self.assertEqual(self.u(self.reg), ("instructor", "cmdr", None, 1))
        self.assertEqual(self.ip(self.reg), (self.p1, 0))
        self.assertEqual(self.reqs()[0][9:11], ("approved", self.mp))
        acts = [r[1] for r in self.audit_rows(target_user_id=self.reg)]
        self.assertIn("request.approve", acts)
        self.assertIn("role.to_instructor", acts)

    def test_an_admin_may_approve_too(self):
        self.approve(self.nominate()["request_id"], actor=self.adm)
        self.assertEqual(self.u(self.reg)[0], "instructor")

    def test_assigning_the_nominee_to_a_platoon_meanwhile_closes_the_nomination(self):
        self.nominate()
        people.add_cadet(self.path, self.mm2, "reg@x.com", self.p2)                    # unassigned cadet: simply assigned
        self.assertEqual(self.reqs()[0][9], "rejected")

    def test_approving_closes_the_other_open_requests_about_the_nominee(self):
        rid = self.nominate()["request_id"]
        other = people.nominate_cmdr(self.path, self.mm2, "reg@x.com")["request_id"]    # a second mm wants the same cadet
        self.approve(rid)
        self.assertEqual({r[0]: r[9] for r in self.reqs()}, {rid: "approved", other: "rejected"})

    def test_the_cmdr_limit_applies_at_approval_and_the_request_stays_open(self):
        rid = self.nominate()["request_id"]
        for i in range(roles.MAX_CMDRS_PER_PLATOON - 1):
            self.make(f"fill{i}@x.com", "instructor", title="cmdr", platoon=self.p1)
        with self.assertRaises(ConflictError) as cm:
            self.approve(rid)
        self.assertEqual(cm.exception.message_key, "CMDR_LIMIT")
        self.assertEqual((self.u(self.reg)[0], self.reqs()[0][9]), ("cadet", "pending"))
        people.reject_change(self.path, self.adm, rid)

    def test_approval_is_refused_when_the_nominator_is_no_longer_a_mm_of_that_platoon(self):
        rid = self.nominate()["request_id"]
        people.set_title(self.path, self.adm, self.mm1, "cmdr")
        with self.assertRaises(NotFoundError):
            self.approve(rid)
        self.assertEqual(self.u(self.reg)[0], "cadet")
        people.reject_change(self.path, self.mp, rid)

    def test_approval_is_refused_when_the_nominee_is_no_longer_an_active_cadet(self):
        rid = self.nominate()["request_id"]
        with db.transaction(self.path) as c:
            c.execute("UPDATE users SET active=0 WHERE id=?", (self.reg,))
        with self.assertRaises(NotFoundError):
            self.approve(rid)

    def test_rejecting_changes_nothing(self):
        rid = self.nominate()["request_id"]
        with db.reader(self.path) as c:
            users_before = [tuple(r) for r in c.execute("SELECT * FROM users")]
            ip_before = [tuple(r) for r in c.execute("SELECT * FROM instructor_platoons")]
        people.reject_change(self.path, self.adm, rid)
        with db.reader(self.path) as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM users")], users_before)
            self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM instructor_platoons")], ip_before)
        self.assertEqual(self.reqs()[0][9], "rejected")

    def test_a_mm_cannot_decide_his_own_nomination_and_a_cmdr_cannot_nominate_via_requests(self):
        rid = self.nominate()["request_id"]
        with self.assertRaises(AuthError):
            people.approve_change(self.path, self.mm1, rid, **self.identity(self.reg))
        with self.assertRaises(AuthError):
            people.set_role(self.path, self.mm1, self.reg, "instructor", title="cmdr", platoon_id=self.p1, **self.identity(self.reg))


class ReadingRequests(PeopleCase):
    def setUp(self):
        super().setUp()
        self.r_del = people.delete_cadet(self.path, self.mm1, self.a1)["request_id"]
        self.r_edit = people.edit_cadet(self.path, self.mm2, self.b1, {"name": "Gimmel", "platoon_id": self.p3})["request_id"]
        self.r_nom = people.nominate_cmdr(self.path, self.mm1, "reg@x.com")["request_id"]
        self.r_add = people.add_cadet(self.path, self.mm1, "b1@x.com", self.p1)["request_id"]

    def test_pending_changes_for_admin_and_chief(self):
        for actor in (self.adm, self.mp):
            rows = people.pending_changes(self.path, actor)
            self.assertEqual([r["id"] for r in rows], sorted(r["id"] for r in rows), "oldest first")
            by = {r["id"]: r for r in rows}
            self.assertEqual(set(by), {self.r_del, self.r_edit, self.r_nom, self.r_add})
            self.assertEqual((by[self.r_del]["kind"], by[self.r_del]["target_name"], by[self.r_del]["requester_name"],
                              by[self.r_del]["platoon"]), ("delete", "Alef", "Mm1", "P1"))
            self.assertEqual((by[self.r_edit]["new_name"], by[self.r_edit]["new_platoon"], by[self.r_edit]["target_platoon"]),
                             ("Gimmel", "P3", "P2"))
            self.assertEqual((by[self.r_nom]["target_email"], by[self.r_nom]["target_created_at"]),
                             ("reg@x.com", repo.get_user(self.path, self.reg)["created_at"]))
            for rid in (self.r_del, self.r_edit, self.r_add):
                self.assertIsNone(by[rid]["target_email"], "the e-mail is carried only by nominations")
                self.assertIsNone(by[rid]["target_created_at"])
            for r in rows:
                self.assertFalse({"pw_hash", "email", "pseudonym"} & set(r))

    def test_decided_requests_are_not_pending(self):
        people.approve_change(self.path, self.mp, self.r_del)
        self.assertNotIn(self.r_del, [r["id"] for r in people.pending_changes(self.path, self.adm)])

    def test_a_chief_does_not_see_requests_about_an_admin(self):
        with db.transaction(self.path) as c:
            c.execute("UPDATE users SET role='admin' WHERE id=?", (self.reg,))      # the operator promoted him meanwhile
        self.assertNotIn(self.r_nom, [r["id"] for r in people.pending_changes(self.path, self.mp)])
        self.assertIn(self.r_nom, [r["id"] for r in people.pending_changes(self.path, self.adm)])

    def test_my_changes_are_the_instructors_own_with_every_status(self):
        people.reject_change(self.path, self.mp, self.r_del)
        mine = people.my_changes(self.path, self.mm1)
        self.assertEqual({r["id"] for r in mine}, {self.r_del, self.r_nom, self.r_add})
        self.assertEqual({r["id"]: r["status"] for r in mine}[self.r_del], "rejected")
        self.assertEqual([r["id"] for r in mine], sorted((r["id"] for r in mine), reverse=True), "newest first")
        for r in mine:
            self.assertIsNone(r["target_email"])
            self.assertIsNone(r["target_created_at"])
        self.assertEqual({r["id"] for r in people.my_changes(self.path, self.mm2)}, {self.r_edit})
        self.assertEqual(people.my_changes(self.path, self.cm1), [])

    def test_who_may_read(self):
        for actor in (self.mm1, self.a1):
            with self.assertRaises(AuthError):
                people.pending_changes(self.path, actor)
        for actor in (self.mp, self.adm, self.a1):
            with self.assertRaises(AuthError):
                people.my_changes(self.path, actor)

    def test_reads_are_not_audited_on_success(self):
        n = self.n_audit()
        people.pending_changes(self.path, self.adm)
        people.my_changes(self.path, self.mm1)
        self.assertEqual(self.n_audit(), n)


# ===================================================================================== server-side checks
class ServerSideRoleChecks(PeopleCase):
    def table(self):
        P = self.path
        ident = self.identity(self.reg)
        return {
            "search_accounts": ("admin chief", lambda a: people.search_accounts(P, a)),
            "set_role": ("admin chief", lambda a: people.set_role(P, a, self.reg, "instructor", title="cmdr", platoon_id=self.p3, **ident)),
            "set_title": ("admin chief", lambda a: people.set_title(P, a, self.cm1, "cmdr")),
            "move_instructor": ("admin chief", lambda a: people.move_instructor(P, a, self.cm1, self.p3)),
            "list_platoons": ("admin chief", lambda a: people.list_platoons(P, a)),
            "move_cadet": ("admin chief", lambda a: people.move_cadet(P, a, self.n1, self.p3)),
            "add_cadet": ("admin chief instructor", lambda a: people.add_cadet(P, a, "n1@x.com", self.p1)),
            "edit_cadet": ("admin chief instructor", lambda a: people.edit_cadet(P, a, self.a1, {"name": "Z"})),
            "delete_cadet": ("admin chief instructor", lambda a: people.delete_cadet(P, a, self.a1)),
            "nominate_cmdr": ("mm", lambda a: people.nominate_cmdr(P, a, "reg@x.com")),
            "pending_changes": ("admin chief", lambda a: people.pending_changes(P, a)),
            "my_changes": ("instructor", lambda a: people.my_changes(P, a)),
            "approve_change": ("admin chief", lambda a: people.approve_change(P, a, 9999)),
            "reject_change": ("admin chief", lambda a: people.reject_change(P, a, 9999)),
            "release_email": ("admin", lambda a: people.release_email(P, a, self.n1)),
            "join_link": ("admin chief instructor", lambda a: people.join_link(P, a, self.p1, secret="x" * 40)),
            "rotate_join_links": ("admin chief instructor", lambda a: people.rotate_join_links(P, a, self.p1)),
        }

    def actors(self):
        return {"admin": self.adm, "chief": self.mp, "instructor": self.cm1, "mm": self.mm1, "cadet": self.a1}

    def test_every_public_function_is_in_the_table(self):
        self.assertEqual(set(self.table()), set(people.__all__))
        for name in people.__all__:
            self.assertTrue(callable(getattr(people, name)))

    def test_a_cadet_gets_auth_error_and_one_denied_audit_row_for_every_function(self):
        for name, (_, call) in self.table().items():
            before, n = self.state(), self.n_audit()
            with self.subTest(fn=name), self.assertRaises(AuthError):
                call(self.a1)
            self.assertEqual(self.state(), before)
            self.assertEqual(self.n_audit(), n + 1, name)
            self.assertEqual(self.audit_rows()[-1][0::4], (self.a1, "denied"), name)
            with db.transaction(self.path) as c:
                c.execute("DELETE FROM audit_log")

    def test_every_role_outside_the_allowed_ones_is_refused_and_nothing_changes(self):
        for name, (allowed, call) in self.table().items():
            for label, actor in self.actors().items():
                ok = label in allowed.split() or (label == "mm" and "instructor" in allowed.split())
                if ok:
                    continue
                before = self.state()
                with self.subTest(fn=name, actor=label), self.assertRaises(AuthError):
                    call(actor)
                self.assertEqual(self.state(), before, (name, label))

    def test_strangers_and_inactive_accounts_are_refused_and_strangers_leave_no_audit_row(self):
        ops.deactivate_user(self.path, self.a2)
        dead_staff = self.make("dead@x.com", "instructor", title="cmdr", platoon=self.p3)
        ops.deactivate_user(self.path, dead_staff)
        dead_admin = self.make("deadadm@x.com", "admin")
        ops.deactivate_user(self.path, dead_admin)
        for name, (_, call) in self.table().items():
            n = self.n_audit()
            for stranger in (9999, None, 0, -1, True, "1", 2 ** 70):
                with self.subTest(fn=name, actor=stranger), self.assertRaises(AuthError):
                    call(stranger)
            self.assertEqual(self.n_audit(), n, "strangers cannot fill the log")
            for dead in (self.a2, dead_staff, dead_admin):
                with self.subTest(fn=name, actor=dead), self.assertRaises(AuthError):
                    call(dead)

    def test_the_allowed_roles_can_use_their_functions(self):
        """Smoke test that the table's 'allowed' column is right: the role gate (NOT_AUTHORISED) never refuses these
        (the call may still be refused for DATA reasons, e.g. an earlier call in this loop changed the account)."""
        for name, (allowed, call) in self.table().items():
            for label in allowed.split():
                actor = self.actors()["instructor" if label == "instructor" else label]
                try:
                    call(actor)
                except AuthError as exc:
                    self.assertNotEqual(exc.message_key, "NOT_AUTHORISED", f"{name} refused {label} at the gate")
                except (NotFoundError, ValidationError, ConflictError):
                    pass
                except ImportError:                           # join_link needs baror.domain.joinlink (registration workstream)
                    self.assertEqual(name, "join_link")

    def test_role_is_reread_on_every_call(self):
        people.search_accounts(self.path, self.mp)
        people.set_role(self.path, self.adm, self.mp, "cadet")
        with self.assertRaises(AuthError):
            people.search_accounts(self.path, self.mp)

    def test_an_instructor_cannot_reach_the_account_table_or_grant_anything(self):
        for call in (lambda: people.search_accounts(self.path, self.mm1),
                     lambda: people.set_role(self.path, self.mm1, self.reg, "instructor", title="cmdr", platoon_id=self.p1,
                                             **self.identity(self.reg)),
                     lambda: people.set_title(self.path, self.mm1, self.cm1, "mm"),
                     lambda: people.move_instructor(self.path, self.mm1, self.cm1, self.p3)):
            with self.assertRaises(AuthError):
                call()


# ================================================================================================ release
class ReleaseEmail(PeopleCase):
    def test_admin_frees_the_address_of_a_cadet_and_keeps_the_results(self):
        repo.add_result(self.path, self.a1, test_date="2026-01-01", run_tenths=900, pushups=30)
        tok = sessions.issue(self.path, self.a1)
        ps = self.pseudonym_of(self.a1)
        people.delete_cadet(self.path, self.mm1, self.a1)
        people.release_email(self.path, self.adm, self.a1)
        u = repo.get_user(self.path, self.a1)
        self.assertRegex(u["email"], r"^released-[0-9a-f]{8}@released\.invalid$")
        self.assertEqual((u["active"], u["platoon_id"]), (0, None))
        self.assertIsNone(sessions.resolve(self.path, tok))
        self.assertEqual(len(repo.list_results(self.path, self.a1)), 1)
        self.assertEqual(self.pseudonym_of(self.a1), ps)
        self.assertEqual([r[9] for r in self.reqs()], ["rejected"])
        self.assertIn((self.adm, "people.release", self.a1, None, "ok"), self.audit_rows())
        with db.reader(self.path) as c:
            self.assertNotIn("a1@x.com", repr([tuple(r) for r in c.execute("SELECT * FROM audit_log")]))
        new = self.user("a1@x.com", name="Same Person Again")                          # the address is free again
        self.assertNotEqual(new, self.a1)

    def test_admin_only_and_cadets_only(self):
        for actor in (self.mp, self.mm1, self.a2):
            with self.assertRaises(AuthError):
                people.release_email(self.path, actor, self.a1)
        for target in (self.mm1, self.mp):
            with self.assertRaises(AuthError) as cm:
                people.release_email(self.path, self.adm, target)
            self.assertEqual(cm.exception.message_key, "ROLE_NOT_ALLOWED")
        a2 = self.make("adm2@x.com", "admin")
        with self.assertRaises(AuthError):
            people.release_email(self.path, self.adm, a2)
        for target in (9999, None, True):
            with self.assertRaises(NotFoundError):
                people.release_email(self.path, self.adm, target)
        self.assertEqual(repo.get_user(self.path, self.mm1)["email"], "mm1@x.com")

    def test_an_already_released_address_is_not_released_again_and_a_deactivated_cadet_can_be(self):
        ops.deactivate_user(self.path, self.a2)
        people.release_email(self.path, self.adm, self.a2)
        first = repo.get_user(self.path, self.a2)["email"]
        with self.assertRaises(NotFoundError):
            people.release_email(self.path, self.adm, self.a2)
        self.assertEqual(repo.get_user(self.path, self.a2)["email"], first)


# ==================================================================================================== join
class JoinLinks(PeopleCase):
    SECRET = "s" * 40

    def link(self, actor, platoon, **kw):
        return people.join_link(self.path, actor, platoon, secret=kw.pop("secret", self.SECRET), **kw)

    def epoch(self, p):
        with db.reader(self.path) as c:
            return c.execute("SELECT join_epoch FROM platoons WHERE id=?", (p,)).fetchone()[0]

    def test_rotate_bumps_the_epoch_and_is_audited(self):
        self.assertEqual(people.rotate_join_links(self.path, self.adm, self.p1), 1)
        self.assertEqual(people.rotate_join_links(self.path, self.mp, self.p1), 2)
        self.assertEqual(people.rotate_join_links(self.path, self.mm1, self.p1), 3)         # own platoon
        self.assertEqual((self.epoch(self.p1), self.epoch(self.p2)), (3, 0))
        self.assertEqual(self.n_audit("join.rotate", "ok"), 3)

    def test_who_may_make_or_rotate_links_for_which_platoon(self):
        for fn in (lambda a, p: self.link(a, p), lambda a, p: people.rotate_join_links(self.path, a, p)):
            for actor in (self.mm1, self.cm1):
                with self.assertRaises(NotFoundError):
                    fn(actor, self.p2)                                      # not their platoon
                with self.assertRaises(NotFoundError):
                    fn(actor, 9999)
            with self.assertRaises(AuthError):
                fn(self.a1, self.p1)
            with self.assertRaises(NotFoundError):
                fn(self.adm, 9999)
        self.assertEqual(self.epoch(self.p2), 0)

    def test_the_feature_is_off_without_a_long_secret_and_days_are_bounded(self):
        for secret in (None, "", "short", "s" * 31, 123):
            with self.subTest(secret=secret), self.assertRaises(ValidationError):
                self.link(self.adm, self.p1, secret=secret)
        for days in (0, -1, 31, 1.5, "7", None, True):
            with self.subTest(days=days), self.assertRaises(ValidationError):
                self.link(self.adm, self.p1, days=days)
        self.assertEqual(self.n_audit("join.create"), 0)

    @unittest.skipUnless(__import__("importlib").util.find_spec("baror.domain.joinlink"), "baror.domain.joinlink not built yet")
    def test_a_link_is_a_signed_token_for_that_platoon_and_epoch(self):
        import datetime as dt
        from baror.domain import joinlink
        now = dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.timezone.utc)
        token = self.link(self.mm1, self.p1, days=7, now=now)
        self.assertIsInstance(token, str)
        parsed = joinlink.parse(token)
        self.assertIsNotNone(parsed)
        self.assertTrue(joinlink.verify(self.SECRET, token, 0, int(now.timestamp()) + 60))
        self.assertFalse(joinlink.verify(self.SECRET, token, 1, int(now.timestamp()) + 60), "rotated epoch revokes it")
        self.assertFalse(joinlink.verify(self.SECRET, token, 0, int((now + dt.timedelta(days=8)).timestamp())), "expired")
        self.assertEqual(self.n_audit("join.create", "ok"), 1)
        with db.reader(self.path) as c:
            self.assertNotIn(token, repr([tuple(r) for r in c.execute("SELECT * FROM audit_log")]))


# ============================================================================================ own platoon
class SetOwnPlatoon(PeopleCase):
    def test_a_cadet_without_a_platoon_picks_one_exactly_once(self):
        repo.set_own_platoon(self.path, self.n1, self.p2)
        self.assertEqual(self.u(self.n1)[2], self.p2)
        self.assertIn((self.n1, "cadet.set_platoon", self.n1, self.p2, "ok"), self.audit_rows())
        with self.assertRaises(ConflictError) as cm:
            repo.set_own_platoon(self.path, self.n1, self.p3)
        self.assertEqual(cm.exception.message_key, "PLATOON_ALREADY_SET")
        self.assertEqual(self.u(self.n1)[2], self.p2)

    def test_after_staff_remove_the_cadet_the_cadet_may_pick_again(self):
        people.delete_cadet(self.path, self.mp, self.a1)
        repo.set_own_platoon(self.path, self.a1, self.p3)
        self.assertEqual(self.u(self.a1)[2], self.p3)

    def test_a_cadet_who_has_a_platoon_cannot_change_it(self):
        with self.assertRaises(ConflictError):
            repo.set_own_platoon(self.path, self.a1, self.p2)
        self.assertEqual(self.u(self.a1)[2], self.p1)

    def test_unknown_platoon_and_wrong_accounts(self):
        for plat in (9999, None, 0, True, "1", 2 ** 70):
            with self.subTest(platoon=plat), self.assertRaises(ValidationError):
                repo.set_own_platoon(self.path, self.n1, plat)
        for uid in (self.mm1, self.mp, self.adm, 9999, None, True):
            with self.subTest(user=uid), self.assertRaises(AuthError):
                repo.set_own_platoon(self.path, uid, self.p1)
        ops.deactivate_user(self.path, self.n1)
        with self.assertRaises(AuthError):
            repo.set_own_platoon(self.path, self.n1, self.p1)
        self.assertEqual(self.reqs(), [])

    def test_get_user_and_sessions_carry_title_and_platoon(self):
        u = repo.get_user(self.path, self.mm1)
        self.assertEqual((u["title"], u["platoon_id"], u["role"]), ("mm", self.p1, "instructor"))
        u = repo.get_user(self.path, self.a1)
        self.assertEqual((u["title"], u["platoon_id"]), (None, self.p1))
        self.assertEqual(repo.get_user(self.path, self.mp)["title"], "mp")
        self.assertIsNone(repo.get_user(self.path, self.n1)["platoon_id"])
        for uid, want in ((self.cm1, ("cmdr", self.p1)), (self.b1, (None, self.p2)), (self.adm, (None, None))):
            got = sessions.resolve(self.path, sessions.issue(self.path, uid))
            self.assertEqual((got["title"], got["platoon_id"]), want)
            self.assertNotIn("pw_hash", got)


if __name__ == "__main__":
    unittest.main()
