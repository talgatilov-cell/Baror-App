"""The approval step of accommodations: a cadet's own record starts 'pending', only the chief decides, only an approved
record is in effect, a rejected one is kept (and shown), every decision is audited and every refusal is 'denied'."""
import datetime as dt
import unittest

from baror import export_pdf, messages_he
from baror.domain import accommodation as rules
from baror.errors import AuthError, NotFoundError
from baror.storage import accommodations as acc
from baror.export_pdf import visual_order
from baror.storage import chief, cohort, db, ops, repo
from tests.test_accommodations import GOOD, NOW, TODAY, Base
from tests.test_export_pdf import data_for, parse

LATER = NOW + dt.timedelta(hours=2)


class InEffect(unittest.TestCase):
    REC = {"scope": "both", "start_date": "2026-11-01", "end_date": "2026-11-30"}

    def test_pending_and_rejected_are_never_active_or_covering(self):
        for approval in ("pending", "rejected"):
            rec = dict(self.REC, approval=approval)
            self.assertEqual(rules.status(rec, TODAY), approval)
            self.assertNotEqual(rules.status(rec, TODAY), "active")
            self.assertFalse(rules.covers(rec, "run") or rules.covers(rec, "pushups") or rules.covers(rec, "walk"))
            self.assertFalse(rules.in_effect(rec))

    def test_status_ignores_the_dates_of_a_record_that_is_not_approved(self):
        for approval in ("pending", "rejected"):
            for start, end in (("2027-01-01", "2027-02-01"), ("2026-01-01", "2026-02-01")):
                self.assertEqual(rules.status(dict(self.REC, approval=approval, start_date=start, end_date=end), TODAY), approval)

    def test_an_unknown_approval_value_is_never_in_effect(self):
        rec = dict(self.REC, approval="whatever")
        self.assertFalse(rules.in_effect(rec) or rules.covers(rec, "run"))
        self.assertEqual(rules.status(rec, TODAY), "pending")

    def test_approved_records_behave_as_before(self):
        rec = dict(self.REC, approval="approved")
        self.assertEqual(rules.status(rec, TODAY), "active")
        self.assertEqual(rules.status(rec, dt.date(2026, 10, 1)), "upcoming")
        self.assertEqual(rules.status(rec, dt.date(2026, 12, 1)), "expired")
        self.assertTrue(rules.covers(rec, "pushups"))


class Export(unittest.TestCase):
    def test_the_pdf_labels_a_pending_and_a_rejected_record_and_never_calls_them_active(self):
        for approval in ("pending", "rejected"):
            rec = dict(data_for()["accommodations"][0], approval=approval, delete_requested=False)
            text = "\n".join(parse(export_pdf.build_pdf(data_for(accommodations=[rec]), today=TODAY))[1])
            first_word = messages_he.t("acc_approval_" + approval).split()[0]      # a long cell wraps, so check its first word
            self.assertIn(visual_order(first_word), text)
            self.assertNotIn(visual_order(messages_he.t("acc_status_active")), text)


class Created(Base):
    def approvals(self, cadet):
        with db.reader(self.path) as c:
            return [tuple(r) for r in c.execute("SELECT source, approval, decided_at, decided_by FROM accommodations "
                                                "WHERE user_id=? ORDER BY id", (cadet,))]

    def test_a_cadets_record_starts_pending_and_staff_records_start_approved(self):
        for actor in (self.ca, self.ins, self.adm, self.boss):
            self.add(actor, self.ca)
        self.assertEqual(self.approvals(self.ca), [("cadet", "pending", None, None), ("instructor", "approved", None, None),
                                                   ("admin", "approved", None, None), ("chief", "approved", None, None)])

    def test_the_public_dict_carries_the_approval_and_still_hides_created_by(self):
        rid = self.add(self.ca, self.ca)
        mine = acc.list_own(self.path, self.ca)[0]
        self.assertEqual((mine["id"], mine["approval"]), (rid, "pending"))
        self.assertNotIn("created_by", mine)
        self.assertEqual(cohort.get_cadet_results(self.path, self.ins, self.ca, now=NOW)["accommodations"][0]["approval"], "pending")
        self.assertEqual(chief.get_cadet_results(self.path, self.boss, self.ca, now=NOW)["accommodations"][0]["approval"], "pending")
        self.assertEqual(repo.export_user_data(self.path, self.ca)["accommodations"][0]["approval"], "pending")

    def test_the_operator_adds_an_approved_record(self):
        rid = ops.add_accommodation(self.path, self.ca, today=TODAY, now=NOW, **GOOD)
        self.assertEqual(self.approvals(self.ca), [("admin", "approved", None, None)])
        self.assertEqual([r["approval"] for r in ops.list_accommodations(self.path) if r["id"] == rid], ["approved"])


class Decide(Base):
    def setUp(self):
        super().setUp()
        self.rid = self.add(self.ca, self.ca)
        self.rid_b = self.add(self.cb, self.cb)
        self.staff_rid = self.add(self.ins, self.ca)

    def row(self, rid):
        with db.reader(self.path) as c:
            return tuple(c.execute("SELECT approval, decided_at, decided_by FROM accommodations WHERE id=?", (rid,)).fetchone())

    def test_the_chief_approves_and_it_is_recorded_and_audited(self):
        acc.approve_request(self.path, self.boss, self.rid, now=LATER)
        self.assertEqual(self.row(self.rid), ("approved", "2026-11-03T10:00:00Z", self.boss))
        self.assertEqual(self.row(self.rid_b)[0], "pending", "only the named record changes")
        self.assertEqual(self.n_audit("accommodation.approve", "ok"), 1)
        self.assertIn((self.boss, "accommodation.approve", self.ca, None, "ok"), self.audit_rows())
        self.assertEqual(rules.status(acc.list_own(self.path, self.ca)[-1], TODAY), "active")

    def test_the_chief_rejects_the_row_is_kept_and_still_visible_to_everybody_who_saw_it(self):
        acc.reject_request(self.path, self.boss, self.rid, now=LATER)
        self.assertEqual(self.row(self.rid), ("rejected", "2026-11-03T10:00:00Z", self.boss))
        self.assertEqual(self.n_audit("accommodation.reject", "ok"), 1)
        seen = {self.rid: [a for a in acc.list_own(self.path, self.ca) if a["id"] == self.rid]}
        self.assertEqual(seen[self.rid][0]["approval"], "rejected")
        for fetched in (cohort.get_cadet_results(self.path, self.ins, self.ca, now=NOW)["accommodations"],
                        chief.get_cadet_results(self.path, self.boss, self.ca, now=NOW)["accommodations"]):
            self.assertEqual([a["approval"] for a in fetched if a["id"] == self.rid], ["rejected"])
        self.assertNotIn(self.rid, [p["id"] for p in acc.pending_requests(self.path, self.boss, now=NOW)])
        self.assertFalse(rules.covers(seen[self.rid][0], "run"))

    def test_nobody_but_the_chief_may_decide(self):
        for actor in (self.ca, self.cb, self.ins, self.adm, self.ins0, self.adm0, self.dead):
            for fn in (acc.approve_request, acc.reject_request):
                with self.assertRaises(AuthError, msg=(actor, fn.__name__)):
                    fn(self.path, actor, self.rid, now=NOW)
        self.assertEqual(self.row(self.rid)[0], "pending", "a cadet cannot approve their own record")
        self.assertEqual(self.n_audit("accommodation.approve", "denied"), 7)
        self.assertEqual(self.n_audit("accommodation.reject", "denied"), 7, "a deactivated account still exists: its refusal is logged")
        self.assertEqual(self.n_audit("accommodation.approve", "ok") + self.n_audit("accommodation.reject", "ok"), 0)

    def test_strangers_get_auth_error_and_leave_no_audit_row(self):
        before = self.n_audit()
        for actor in (9999, None, 0, -1, "1", True, 2 ** 70):
            for fn in (acc.approve_request, acc.reject_request):
                with self.assertRaises(AuthError, msg=actor):
                    fn(self.path, actor, self.rid, now=NOW)
        self.assertEqual(self.n_audit(), before)

    def test_a_decision_is_final_and_a_missing_or_bad_id_is_not_visible(self):
        acc.approve_request(self.path, self.boss, self.rid, now=NOW)
        for fn in (acc.approve_request, acc.reject_request):
            for rid in (self.rid, self.staff_rid, 9999, None, 0, -1, True, "1", 2 ** 70):
                with self.assertRaises(NotFoundError, msg=(fn.__name__, rid)) as cm:
                    fn(self.path, self.boss, rid, now=NOW)
                self.assertEqual(cm.exception.message_key, "COHORT_NOT_FOUND")
        self.assertEqual(self.row(self.rid)[0], "approved", "a rejected-after-approved flip is impossible")
        acc.reject_request(self.path, self.boss, self.rid_b, now=NOW)
        with self.assertRaises(NotFoundError):
            acc.approve_request(self.path, self.boss, self.rid_b, now=NOW)
        self.assertEqual(self.row(self.rid_b)[0], "rejected")

    def test_a_staff_account_row_cannot_be_decided(self):
        with db.transaction(self.path) as c:                     # a pending row that belongs to a non-cadet is not reachable
            c.execute("INSERT INTO accommodations(user_id,kind,scope,start_date,end_date,source,approval) "
                      "VALUES(?,?,?,?,?,?,?)", (self.ins, "relief", "run", "2026-11-01", "2026-11-30", "cadet", "pending"))
            rid = c.execute("SELECT max(id) FROM accommodations").fetchone()[0]
        with self.assertRaises(NotFoundError):
            acc.approve_request(self.path, self.boss, rid, now=NOW)
        self.assertEqual(self.row(rid)[0], "pending")

    def test_deleting_the_decider_keeps_the_decision(self):
        boss2 = self.staff("boss2@x.com", role="chief", name="Boss2")
        acc.approve_request(self.path, boss2, self.rid, now=NOW)
        with db.transaction(self.path) as c:
            c.execute("DELETE FROM users WHERE id=?", (boss2,))
        self.assertEqual(self.row(self.rid), ("approved", "2026-11-03T08:00:00Z", None))

    def test_deletion_requests_still_work_on_pending_and_rejected_records(self):
        acc.reject_request(self.path, self.boss, self.rid, now=NOW)
        acc.request_delete(self.path, self.ins, self.rid, now=NOW)
        self.assertEqual([g["id"] for g in acc.pending_deletions(self.path, self.boss, now=NOW)], [self.rid])
        acc.reject_delete(self.path, self.boss, self.rid, now=NOW)
        self.assertEqual(self.row(self.rid)[0], "rejected", "clearing a deletion request does not touch the approval")
        acc.approve_delete(self.path, self.boss, self.rid_b, now=NOW)
        self.assertEqual(self.rows(self.cb), [])


class PendingList(Base):
    def test_shape_order_scope_and_audit(self):
        first = self.add(self.ca, self.ca, now=NOW)
        second = self.add(self.cb, self.cb, now=NOW + dt.timedelta(minutes=5), scope="both")
        third = self.add(self.cn, self.cn, now=NOW - dt.timedelta(minutes=5), kind="exemption")
        self.add(self.ins, self.ca)                                    # approved: not listed
        self.add(self.boss, self.cb)                                   # chief-entered, auto-approved: not listed
        got = acc.pending_requests(self.path, self.boss, now=NOW)
        self.assertEqual([g["id"] for g in got], [third, first, second], "oldest first")
        self.assertEqual(set(got[0]), {"id", "cadet_id", "cadet_name", "platoon", "kind", "scope", "start_date", "end_date",
                                       "note", "source", "created_at"})
        self.assertEqual([(g["cadet_name"], g["platoon"], g["source"]) for g in got],
                         [("Nun", None, "cadet"), ("Alef", "A", "cadet"), ("Bet", "B", "cadet")])
        self.assertEqual(self.n_audit("accommodation.pending_requests", "ok"), 1)
        acc.approve_request(self.path, self.boss, first, now=NOW)
        acc.reject_request(self.path, self.boss, second, now=NOW)
        self.assertEqual([g["id"] for g in acc.pending_requests(self.path, self.boss, now=NOW)], [third])

    def test_an_empty_list_is_a_success_not_a_refusal(self):
        self.assertEqual(acc.pending_requests(self.path, self.boss, now=NOW), [])
        self.assertEqual(self.n_audit("accommodation.pending_requests", "ok"), 1)

    def test_only_the_chief_may_read_it(self):
        self.add(self.ca, self.ca)
        for actor in (self.ca, self.ins, self.adm, self.ins0, self.adm0, self.dead):
            with self.assertRaises(AuthError, msg=actor):
                acc.pending_requests(self.path, actor, now=NOW)
        for actor in (9999, None, True):
            with self.assertRaises(AuthError):
                acc.pending_requests(self.path, actor, now=NOW)
        self.assertEqual(self.n_audit("accommodation.pending_requests", "denied"), 6, "existing accounts only; strangers leave no row")

    def test_pending_deletions_is_a_separate_list(self):
        rid = self.add(self.ca, self.ca)
        acc.request_delete(self.path, self.ins, rid, now=NOW)
        self.assertEqual([g["id"] for g in acc.pending_deletions(self.path, self.boss, now=NOW)], [rid])
        acc.approve_request(self.path, self.boss, rid, now=NOW)
        self.assertEqual([g["id"] for g in acc.pending_deletions(self.path, self.boss, now=NOW)], [rid])
        self.assertEqual(acc.pending_requests(self.path, self.boss, now=NOW), [])


if __name__ == "__main__":
    unittest.main()
