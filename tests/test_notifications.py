"""baror.storage.notifications.counts: the numbers behind the top-bar bell (chief: three kinds, admin: change requests)."""
import datetime as dt
import unittest

from baror.errors import AuthError
from baror.storage import accommodations as acc
from baror.storage import notifications, ops, people
from tests.v2base import V2Case

NOW = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.timezone.utc)
TODAY = dt.date(2026, 11, 4)
GOOD = dict(kind="relief", scope="run", start_date="2026-11-01", end_date="2026-12-01", note=None)


class CountsCase(V2Case):
    def setUp(self):
        super().setUp()
        self.p1, self.p2 = self.platoon("P1"), self.platoon("P2")
        self.adm = self.staff("adm@x.com", "admin")
        self.boss = self.staff("boss@x.com", "chief")
        self.ins = self.staff("ins@x.com", platoons=[self.p1], title="mm")
        self.c1 = self.cadet("c1@x.com", self.p1)
        self.c2 = self.cadet("c2@x.com", self.p1)
        self.c3 = self.cadet("c3@x.com", self.p2)

    def add_accommodation(self, actor, cadet):
        return acc.add(self.path, actor, cadet, now=NOW, today=TODAY, **GOOD)


class WhatIsCounted(CountsCase):
    def test_nothing_pending_is_all_zero(self):
        zero = {"accommodation_requests": 0, "deletion_requests": 0, "change_requests": 0, "total": 0}
        self.assertEqual(notifications.counts(self.path, self.boss), zero)
        self.assertEqual(notifications.counts(self.path, self.adm), zero)

    def test_the_chief_sees_all_three_kinds_and_the_total(self):
        self.add_accommodation(self.c1, self.c1)                       # a cadet's own entry: awaits approval
        self.add_accommodation(self.c2, self.c2)
        done = self.add_accommodation(self.ins, self.c1)               # instructor's: approved at once, then a deletion request
        acc.request_delete(self.path, self.ins, done, now=NOW)
        people.delete_cadet(self.path, self.ins, self.c1)              # a cadet-change request
        people.edit_cadet(self.path, self.ins, self.c2, {"name": "Bet"})
        people.nominate_cmdr(self.path, self.ins, "c3@x.com")
        self.assertEqual(notifications.counts(self.path, self.boss),
                         {"accommodation_requests": 2, "deletion_requests": 1, "change_requests": 3, "total": 6})

    def test_an_admin_sees_the_change_requests_only(self):
        self.add_accommodation(self.c1, self.c1)
        done = self.add_accommodation(self.ins, self.c1)
        acc.request_delete(self.path, self.ins, done, now=NOW)
        people.delete_cadet(self.path, self.ins, self.c1)
        self.assertEqual(notifications.counts(self.path, self.adm),
                         {"accommodation_requests": 0, "deletion_requests": 0, "change_requests": 1, "total": 1})

    def test_decided_requests_drop_out(self):
        rid = people.delete_cadet(self.path, self.ins, self.c1)["request_id"]
        people.reject_change(self.path, self.boss, rid)
        acc_id = self.add_accommodation(self.c1, self.c1)
        acc.approve_request(self.path, self.boss, acc_id, now=NOW)
        self.assertEqual(notifications.counts(self.path, self.boss)["total"], 0)

    def test_numbers_agree_with_the_lists_they_announce(self):
        self.add_accommodation(self.c1, self.c1)
        people.delete_cadet(self.path, self.ins, self.c1)
        c = notifications.counts(self.path, self.boss)
        self.assertEqual(c["accommodation_requests"], len(acc.pending_requests(self.path, self.boss, now=NOW)))
        self.assertEqual(c["change_requests"], len(people.pending_changes(self.path, self.boss)))
        self.assertEqual(c["deletion_requests"], len(acc.pending_deletions(self.path, self.boss, now=NOW)))

    def test_a_request_about_an_admin_is_counted_for_an_admin_only(self):
        people.delete_cadet(self.path, self.ins, self.c1)
        from baror.storage import db
        with db.transaction(self.path) as c:
            c.execute("UPDATE users SET role='admin' WHERE id=?", (self.c1,))
        self.assertEqual(notifications.counts(self.path, self.boss)["change_requests"], 0)
        self.assertEqual(notifications.counts(self.path, self.adm)["change_requests"], 1)


class WhoMayAsk(CountsCase):
    def test_everybody_else_gets_auth_error_and_a_denied_row_when_the_account_exists(self):
        dead = self.staff("dead@x.com", "chief")
        ops.deactivate_user(self.path, dead)
        for actor in (self.ins, self.c1, dead):
            with self.assertRaises(AuthError):
                notifications.counts(self.path, actor)
        denied = self.audit_rows(outcome="denied")
        self.assertEqual([(r[0], r[1]) for r in denied], [(self.ins, "notifications.counts"), (self.c1, "notifications.counts"),
                                                           (dead, "notifications.counts")])
        n = len(self.audit_rows())
        for stranger in (9999, None, 0, True, "1", 2 ** 70):
            with self.assertRaises(AuthError):
                notifications.counts(self.path, stranger)
        self.assertEqual(len(self.audit_rows()), n, "strangers leave nothing")

    def test_a_successful_call_writes_no_audit_row(self):
        n = len(self.audit_rows())
        notifications.counts(self.path, self.boss)
        notifications.counts(self.path, self.adm)
        self.assertEqual(len(self.audit_rows()), n)

    def test_the_role_is_reread_on_every_call(self):
        notifications.counts(self.path, self.boss)
        people.set_role(self.path, self.adm, self.boss, "cadet")
        with self.assertRaises(AuthError):
            notifications.counts(self.path, self.boss)


if __name__ == "__main__":
    unittest.main()
