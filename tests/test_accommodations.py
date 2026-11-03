"""Accommodations (הקלות ופטורים): the domain rules, the authorisation matrix (every role x every action), the audit
trail, the staff reads that carry them, and what erasure and retention do to them."""
import datetime as dt
import inspect
import re
import sqlite3
import unittest

from baror.domain import accommodation as rules
from baror.errors import AuthError, NotFoundError, ValidationError
from baror.storage import accommodations as acc
from baror.storage import audit, chief, cohort, db, ops, repo, retention
from tests.v2base import V2Case

NOW = dt.datetime(2026, 11, 3, 8, 0, tzinfo=dt.timezone.utc)
TODAY = dt.date(2026, 11, 3)
GOOD = dict(kind="relief", scope="run", start_date="2026-11-01", end_date="2027-01-31", note="כאב ברך (קל), 3 שבועות")


class DomainRules(unittest.TestCase):
    def v(self, **kw):
        args = dict(GOOD, **kw)
        return rules.validate(args["kind"], args["scope"], args["start_date"], args["end_date"], args["note"], TODAY)

    def test_accepts_and_normalises(self):
        out = self.v(note="  כאב ברך  ")
        self.assertEqual(out["note"], "כאב ברך")
        self.assertIsNone(self.v(note=None)["note"])
        self.assertIsNone(self.v(note="   ")["note"])
        self.assertEqual(self.v(start_date=dt.date(2026, 11, 1))["start_date"], "2026-11-01")

    def test_kind_and_scope_are_closed_sets(self):
        for kw in (dict(kind="x"), dict(kind=None), dict(scope="all"), dict(scope="")):
            with self.assertRaises(ValidationError, msg=kw) as cm:
                self.v(**kw)
            self.assertEqual(cm.exception.message_key, "INVALID_ACCOMMODATION")

    def test_dates(self):
        bad = [dict(end_date="2026-10-31"), dict(end_date="2029-11-02"), dict(start_date="2026-02-31"), dict(end_date="2027-1-5"),
               dict(start_date="2026-11-01T10:00"), dict(start_date=None), dict(end_date=5),
               dict(start_date="2020-01-01", end_date="2020-02-01"), dict(start_date="2031-01-01", end_date="2031-02-01"),
               dict(start_date=dt.datetime(2026, 11, 1, 10))]
        for kw in bad:
            with self.assertRaises(ValidationError, msg=kw) as cm:
                self.v(**kw)
            self.assertEqual(cm.exception.message_key, "ACCOMMODATION_DATES", kw)
        self.v(end_date="2029-11-01")                       # exactly three years: allowed
        self.v(start_date="2026-11-03", end_date="2026-11-03")   # one day

    def test_note_holds_text_and_digits_but_no_markup_or_control_characters(self):
        self.v(note="פטור עד 15.11, אחרי ניתוח (רגל) - ג׳ון 3")
        for note in ("<b>x</b>", "[a](b)", "*x*", "x:y", "a&b", "a/b", "a\\b", "a`b", "דנה\nכהן", "a\tb", "a\u202Eb", "a​b",
                     "x" * 101, 5, b"abc"):
            with self.assertRaises(ValidationError, msg=repr(note)) as cm:
                self.v(note=note)
            self.assertEqual(cm.exception.message_key, "ACCOMMODATION_NOTE")

    def test_status_is_computed_from_today_and_end_dates_are_inclusive(self):
        rec = {"start_date": "2026-11-03", "end_date": "2026-11-10"}
        self.assertEqual(rules.status(rec, dt.date(2026, 11, 2)), "upcoming")
        self.assertEqual(rules.status(rec, dt.date(2026, 11, 3)), "active")
        self.assertEqual(rules.status(rec, dt.date(2026, 11, 10)), "active")
        self.assertEqual(rules.status(rec, dt.date(2026, 11, 11)), "expired")

    def test_covers(self):
        self.assertTrue(rules.covers({"scope": "both"}, "pushups") and rules.covers({"scope": "both"}, "walk"))
        self.assertTrue(rules.covers({"scope": "run"}, "run") and rules.covers({"scope": "run"}, "walk"))
        self.assertFalse(rules.covers({"scope": "run"}, "pushups"))
        self.assertTrue(rules.covers({"scope": "pushups"}, "pushups") and not rules.covers({"scope": "pushups"}, "run"))


class Base(V2Case):
    def setUp(self):
        super().setUp()
        self.pa, self.pb = self.platoon("A"), self.platoon("B")
        self.ca = self.cadet("ca@x.com", self.pa, name="Alef")
        self.cb = self.cadet("cb@x.com", self.pb, name="Bet")
        self.cn = self.cadet("cn@x.com", None, name="Nun")                       # no platoon
        self.ins = self.staff("i@x.com", platoons=[self.pa], name="Ins")
        self.ins0 = self.staff("i0@x.com", name="Ins None")                     # instructor without grants
        self.adm = self.staff("adm@x.com", role="admin", platoons=[self.pa], name="Adm")
        self.adm0 = self.staff("adm0@x.com", role="admin", name="Adm None")     # admin without grants: no implicit access
        self.boss = self.staff("boss@x.com", role="chief", name="Boss")
        self.dead = self.staff("dead@x.com", platoons=[self.pa], name="Dead")
        ops.deactivate_user(self.path, self.dead)

    def add(self, actor, cadet, now=NOW, **kw):
        return acc.add(self.path, actor, cadet, now=now, today=TODAY, **dict(GOOD, **kw))

    def rows(self, cadet=None):
        with db.reader(self.path) as c:
            sql = "SELECT id,user_id,source,created_by,delete_requested_at,delete_requested_by FROM accommodations"
            return [tuple(r) for r in c.execute(sql + (" WHERE user_id=?" if cadet else "") + " ORDER BY id", (cadet,) if cadet else ())]

    def n_audit(self, action=None, outcome=None):
        return len([r for r in self.audit_rows() if (action is None or r[1] == action) and (outcome is None or r[4] == outcome)])


class AddMatrix(Base):
    def test_who_may_add_for_whom_and_the_source_label(self):
        cases = [(self.ca, self.ca, "cadet"), (self.ins, self.ca, "instructor"), (self.adm, self.ca, "admin"),
                 (self.boss, self.ca, "chief"), (self.boss, self.cb, "chief"), (self.boss, self.cn, "chief"),
                 (self.cb, self.cb, "cadet")]
        for actor, cadet, source in cases:
            rid = self.add(actor, cadet)
            row = [r for r in self.rows(cadet) if r[0] == rid][0]
            self.assertEqual((row[2], row[3]), (source, actor))
        self.assertEqual(self.n_audit("accommodation.add", "ok"), len(cases))

    def test_a_cadet_cannot_record_for_somebody_else_nor_a_staff_account_for_a_cadet_outside_their_scope(self):
        for actor, cadet, exc in [(self.ca, self.cb, AuthError), (self.ca, self.cn, AuthError),
                                  (self.ins, self.cb, NotFoundError), (self.ins, self.cn, NotFoundError),
                                  (self.ins0, self.ca, NotFoundError), (self.adm0, self.ca, NotFoundError),
                                  (self.adm, self.cb, NotFoundError)]:
            with self.assertRaises(exc, msg=(actor, cadet)):
                self.add(actor, cadet)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.n_audit("accommodation.add", "denied"), 7)

    def test_unknown_inactive_and_staff_targets_are_refused_the_same_way_as_a_hidden_cadet(self):
        codes = set()
        for actor, cadet in [(self.ins, self.cb), (self.ins, 9999), (self.ins, self.ins0), (self.ins, self.boss),
                             (self.boss, 9999), (self.boss, self.ins), (self.boss, self.adm), (self.ins, None), (self.boss, True)]:
            with self.assertRaises(NotFoundError) as cm:
                self.add(actor, cadet)
            codes.add((cm.exception.message_key, str(cm.exception)[:12]))
        self.assertEqual(len({c[0] for c in codes}), 1)
        ops.deactivate_user(self.path, self.ca)
        with self.assertRaises(NotFoundError):
            self.add(self.ins, self.ca)
        with self.assertRaises(NotFoundError):
            self.add(self.boss, self.ca)
        self.assertEqual(self.rows(), [])

    def test_unknown_inactive_or_malformed_actors_get_auth_error_and_leave_no_audit_row_for_strangers(self):
        before = self.n_audit()
        for actor in (9999, None, 0, -1, "1", True, 2 ** 70):
            with self.assertRaises(AuthError, msg=actor):
                self.add(actor, self.ca)
        self.assertEqual(self.n_audit(), before, "a stranger cannot fill the log")
        with self.assertRaises(AuthError):
            self.add(self.dead, self.ca)
        self.assertEqual(self.n_audit("accommodation.add", "denied"), 1, "an existing account's refusal is logged")

    def test_a_deactivated_cadet_cannot_record_either(self):
        ops.deactivate_user(self.path, self.ca)
        with self.assertRaises(AuthError):
            self.add(self.ca, self.ca)

    def test_a_staff_role_cannot_record_for_itself_as_if_it_were_a_cadet(self):
        for staff in (self.ins, self.adm, self.boss):
            with self.assertRaises(NotFoundError):
                self.add(staff, staff)

    def test_validation_happens_after_authorisation_and_stores_nothing(self):
        with self.assertRaises(ValidationError):
            self.add(self.ca, self.ca, note="<b>")
        with self.assertRaises(NotFoundError):                    # not visible wins over bad input
            self.add(self.ins, self.cb, note="<b>")
        self.assertEqual(self.rows(), [])

    def test_one_account_cannot_hold_more_than_the_cap(self):
        for _ in range(rules.MAX_PER_CADET):
            self.add(self.ca, self.ca)
        with self.assertRaises(ValidationError) as cm:
            self.add(self.ca, self.ca)
        self.assertEqual(cm.exception.message_key, "ACCOMMODATION_LIMIT")
        self.assertEqual(len(self.rows(self.ca)), rules.MAX_PER_CADET)
        self.add(self.cb, self.cb)                                   # the cap is per cadet

    def test_the_database_checks_are_a_backstop(self):
        conn = db.connect(self.path)
        try:
            ok = ("INSERT INTO accommodations(user_id,kind,scope,start_date,end_date,source) VALUES(?,?,?,?,?,?)")
            for args in [(self.ca, "x", "run", "2026-11-01", "2026-11-02", "cadet"), (self.ca, "relief", "x", "2026-11-01", "2026-11-02", "cadet"),
                         (self.ca, "relief", "run", "2026-02-31", "2026-03-02", "cadet"), (self.ca, "relief", "run", "2026-11-05", "2026-11-01", "cadet"),
                         (self.ca, "relief", "run", "2026-11-01", "2030-01-01", "cadet"), (self.ca, "relief", "run", "2026-11-01", "2026-11-02", "nurse"),
                         (9999, "relief", "run", "2026-11-01", "2026-11-02", "cadet")]:
                with self.assertRaises(sqlite3.IntegrityError, msg=args):
                    conn.execute(ok, args)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO accommodations(user_id,kind,scope,start_date,end_date,source,note) VALUES(?,?,?,?,?,?,?)",
                             (self.ca, "relief", "run", "2026-11-01", "2026-11-02", "cadet", "x" * 101))
        finally:
            conn.close()


class OwnRead(Base):
    def test_list_own_is_scoped_newest_first_without_created_by_and_not_audited(self):
        first, second = self.add(self.ca, self.ca), self.add(self.ins, self.ca, scope="both")
        self.add(self.cb, self.cb)
        before = self.n_audit()
        got = acc.list_own(self.path, self.ca)
        self.assertEqual([r["id"] for r in got], [second, first])
        self.assertEqual(set(got[0]), {"id", "kind", "scope", "start_date", "end_date", "note", "source", "created_at", "delete_requested"})
        self.assertEqual((got[0]["source"], got[1]["source"]), ("instructor", "cadet"))
        self.assertFalse(any(r["id"] in (x["id"] for x in acc.list_own(self.path, self.cb)) for r in got))
        self.assertEqual(self.n_audit(), before)
        for bad in (None, 0, -1, "1", True, 9999):
            self.assertEqual(acc.list_own(self.path, bad), [])

    def test_there_is_no_update_function_and_no_cadet_path_to_delete(self):
        names = [n for n, f in inspect.getmembers(acc, inspect.isfunction) if f.__module__ == acc.__name__ and not n.startswith("_")]
        self.assertEqual(sorted(names), ["add", "approve_delete", "fetch_public", "list_own", "pending_deletions", "reject_delete",
                                         "request_delete"])
        src = inspect.getsource(acc)
        self.assertEqual(re.findall(r"\bUPDATE\s+(\w+)", src), ["accommodations", "accommodations"], "only the request flags change")
        self.assertNotRegex(src, r"UPDATE accommodations SET (kind|scope|start_date|end_date|note|source|user_id)")


class DeleteFlow(Base):
    def setUp(self):
        super().setUp()
        self.rid = self.add(self.ca, self.ca)
        self.rid_b = self.add(self.cb, self.cb)

    def test_instructor_and_granted_admin_may_only_request(self):
        for staff in (self.ins, self.adm):
            acc.request_delete(self.path, staff, self.rid, now=NOW)
            self.assertEqual(len(self.rows(self.ca)), 1, "a request deletes nothing")
        row = self.rows(self.ca)[0]
        self.assertIsNotNone(row[4])
        self.assertEqual(row[5], self.ins, "the first request keeps its requester")
        self.assertEqual(self.n_audit("accommodation.delete_request", "ok"), 2)

    def test_request_is_refused_for_everybody_else(self):
        for actor, exc in [(self.ca, AuthError), (self.cb, AuthError), (self.boss, AuthError), (self.dead, AuthError),
                           (self.ins, NotFoundError), (self.ins0, NotFoundError), (self.adm0, NotFoundError)]:
            target = self.rid_b if actor == self.ins else self.rid
            with self.assertRaises(exc, msg=actor):
                acc.request_delete(self.path, actor, target, now=NOW)
        for bad in (9999, None, 0, "1", True):
            with self.assertRaises(NotFoundError):
                acc.request_delete(self.path, self.ins, bad, now=NOW)
        self.assertTrue(all(r[4] is None for r in self.rows()))

    def test_chief_approves_or_rejects_only(self):
        acc.request_delete(self.path, self.ins, self.rid, now=NOW)
        acc.reject_delete(self.path, self.boss, self.rid, now=NOW)
        self.assertEqual(self.rows(self.ca)[0][4:], (None, None), "the request is cleared and the record stays")
        with self.assertRaises(NotFoundError):                        # nothing pending any more
            acc.reject_delete(self.path, self.boss, self.rid, now=NOW)
        acc.request_delete(self.path, self.ins, self.rid, now=NOW)
        acc.approve_delete(self.path, self.boss, self.rid, now=NOW)
        self.assertEqual(self.rows(self.ca), [])
        self.assertEqual((self.n_audit("accommodation.delete_reject", "ok"), self.n_audit("accommodation.delete_approve", "ok")), (1, 1))

    def test_chief_may_delete_directly_without_a_request(self):
        acc.approve_delete(self.path, self.boss, self.rid_b, now=NOW)
        self.assertEqual(self.rows(self.cb), [])

    def test_nobody_else_may_approve_or_reject(self):
        acc.request_delete(self.path, self.ins, self.rid, now=NOW)
        for actor in (self.ca, self.ins, self.adm, self.ins0, self.adm0, self.dead):
            for fn in (acc.approve_delete, acc.reject_delete):
                with self.assertRaises(AuthError, msg=(actor, fn.__name__)):
                    fn(self.path, actor, self.rid, now=NOW)
        for fn in (acc.approve_delete, acc.reject_delete):
            for bad in (9999, None, True, "1"):
                with self.assertRaises(NotFoundError):
                    fn(self.path, self.boss, bad, now=NOW)
        self.assertEqual(len(self.rows(self.ca)), 1)

    def test_a_stranger_cannot_probe_ids_through_the_error_type(self):
        codes = set()
        for fn, actor, rid in [(acc.request_delete, self.ins, self.rid_b), (acc.request_delete, self.ins, 9999),
                               (acc.approve_delete, self.boss, 9999), (acc.reject_delete, self.boss, self.rid)]:
            with self.assertRaises(NotFoundError) as cm:
                fn(self.path, actor, rid, now=NOW)
            codes.add(cm.exception.message_key)
        self.assertEqual(len(codes), 1)

    def test_pending_deletions_is_chief_only_and_shows_who_asked(self):
        acc.request_delete(self.path, self.ins, self.rid, now=NOW)
        got = acc.pending_deletions(self.path, self.boss, now=NOW)
        self.assertEqual([(g["id"], g["cadet_name"], g["platoon"], g["requested_by_name"], g["source"]) for g in got],
                         [(self.rid, "Alef", "A", "Ins", "cadet")])
        self.assertEqual(set(got[0]), {"id", "cadet_id", "cadet_name", "platoon", "kind", "scope", "start_date", "end_date", "note",
                                       "source", "requested_at", "requested_by_name"})
        self.assertEqual(self.n_audit("accommodation.pending", "ok"), 1)
        acc.approve_delete(self.path, self.boss, self.rid, now=NOW)
        self.assertEqual(acc.pending_deletions(self.path, self.boss, now=NOW), [])
        for actor in (self.ca, self.ins, self.adm, self.dead):
            with self.assertRaises(AuthError):
                acc.pending_deletions(self.path, actor, now=NOW)


class StaffReads(Base):
    def setUp(self):
        super().setUp()
        repo.add_result(self.path, self.ca, test_date="2026-10-01", run_tenths=9000, pushups=30)
        self.r1 = self.add(self.ca, self.ca)
        self.r2 = self.add(self.ins, self.ca, kind="exemption", scope="pushups", note=None)
        self.add(self.cb, self.cb)

    def test_instructor_reads_carry_gender_track_and_accommodations(self):
        s = cohort.cohort_summary(self.path, self.ins, self.pa, now=NOW)[0]
        self.assertEqual((s["gender"], s["is_km"]), ("M", 0))
        self.assertEqual([a["id"] for a in s["accommodations"]], [self.r2, self.r1])
        self.assertEqual(s["accommodations"][0]["source"], "instructor")
        self.assertNotIn("created_by", s["accommodations"][0])
        r = cohort.get_cadet_results(self.path, self.ins, self.ca, now=NOW)
        self.assertEqual([a["id"] for a in r["accommodations"]], [self.r2, self.r1])
        self.assertEqual((r["cadet"]["gender"], r["cadet"]["is_km"]), ("M", 0))

    def test_an_instructor_never_sees_another_platoons_accommodations(self):
        with self.assertRaises(NotFoundError):
            cohort.get_cadet_results(self.path, self.ins, self.cb, now=NOW)
        self.assertEqual(sum(len(s["accommodations"]) for s in cohort.cohort_summary(self.path, self.ins, self.pa, now=NOW)), 2)

    def test_chief_platoon_summary_shape_scope_and_audit(self):
        before = self.n_audit("chief.summary", "ok")
        rows = chief.platoon_summary(self.path, self.boss, self.pa, now=NOW)
        self.assertEqual([r["name"] for r in rows], ["Alef"])
        self.assertEqual(set(rows[0]), {"id", "name", "gender", "is_km", "result_count", "last_test_date", "latest_run", "latest_pushups",
                                        "best_run_tenths", "best_pushups", "accommodations"})
        self.assertEqual(len(rows[0]["accommodations"]), 2)
        self.assertEqual(self.n_audit("chief.summary", "ok"), before + 1, "one row per cadet read")
        un = chief.platoon_summary(self.path, self.boss, None, now=NOW)
        self.assertEqual([r["name"] for r in un], ["Nun"])
        empty = ops.create_platoon(self.path, "E")
        self.assertEqual(chief.platoon_summary(self.path, self.boss, empty, now=NOW), [])
        self.assertEqual(self.n_audit("chief.summary", "ok"), before + 3)
        with self.assertRaises(NotFoundError):
            chief.platoon_summary(self.path, self.boss, 9999, now=NOW)
        for actor in (self.ca, self.ins, self.adm, self.dead, 9999):
            with self.assertRaises(AuthError):
                chief.platoon_summary(self.path, actor, self.pa, now=NOW)

    def test_chief_platoon_summary_skips_deactivated_and_staff_accounts(self):
        ops.deactivate_user(self.path, self.ca)
        self.assertEqual(chief.platoon_summary(self.path, self.boss, self.pa, now=NOW), [])

    def test_chief_cadet_read_carries_accommodations_and_the_disclosure_counts_the_new_read(self):
        got = chief.get_cadet_results(self.path, self.boss, self.ca, now=NOW)
        self.assertEqual(len(got["accommodations"]), 2)
        self.assertEqual(got["cadet"]["gender"], "M")
        chief.platoon_summary(self.path, self.boss, self.pa, now=NOW)
        with db.reader(self.path) as c:
            reads = audit.reads_of_user(c, self.ca, now=NOW)
        self.assertEqual(sum(r["reads"] for r in reads), 2, "chief.cadet and chief.summary both count as a read of the cadet")

    def test_the_chief_overview_is_unchanged_and_leaks_nothing_about_accommodations(self):
        ov = chief.overview(self.path, self.boss, lambda *a: "pass", now=NOW)
        self.assertNotIn("accommodation", repr(ov))


class ErasureAndRetention(Base):
    def setUp(self):
        super().setUp()
        self.add(self.ca, self.ca, note="סודי")
        self.add(self.boss, self.cb, kind="exemption")
        self.by_ins = self.add(self.ins, self.ca, scope="both")
        for uid in (self.ca, self.cb):
            repo.add_result(self.path, uid, test_date="2026-10-01", pushups=20)

    def count(self):
        with db.reader(self.path) as c:
            return c.execute("SELECT count(*) FROM accommodations").fetchone()[0]

    def test_erasing_an_account_deletes_its_accommodations_and_only_its_own(self):
        repo.delete_user(self.path, self.ca)
        self.assertEqual(self.rows(self.ca), [])
        self.assertEqual(self.count(), 1)

    def test_a_staff_account_that_recorded_one_can_be_removed_and_the_record_stays_with_no_author(self):
        with db.transaction(self.path) as c:
            c.execute("DELETE FROM users WHERE id=?", (self.ins,))
        row = [r for r in self.rows(self.ca) if r[0] == self.by_ins][0]
        self.assertEqual((row[2], row[3]), ("instructor", None))

    def test_retention_deletes_accommodations_with_the_identity_and_keeps_pseudonymised_results(self):
        retention.set_course_end(self.path, self.pa, "2026-01-31")
        out = retention.run(self.path, today=dt.date(2026, 11, 3), now=NOW)
        self.assertEqual(out["deidentified"], 1)
        self.assertEqual(self.rows(self.ca), [])
        self.assertEqual(self.count(), 1, "the other platoon's record is untouched")
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM results WHERE pseudonym IN (SELECT pseudonym FROM pseudonyms WHERE deidentified_at IS NOT NULL)").fetchone()[0], 1)
        self.assertNotIn("סודי".encode(), open(self.path, "rb").read(), "the note was scrubbed from the file")

    def test_the_export_carries_the_own_records_and_nobody_elses(self):
        data = repo.export_user_data(self.path, self.ca)
        self.assertEqual([a["note"] for a in data["accommodations"]], [GOOD["note"], "סודי"])
        self.assertEqual({a["kind"] for a in data["accommodations"]}, {"relief"}, "the other cadet's exemption is not in it")
        self.assertNotIn("created_by", data["accommodations"][0])


class OperatorTools(Base):
    def test_operator_adds_lists_and_deletes(self):
        rid = ops.add_accommodation(self.path, self.ca, today=TODAY, now=NOW, **GOOD)
        self.assertEqual(self.rows(self.ca)[0][2:4], ("admin", None))
        self.assertEqual([(r["id"], r["cadet_name"], r["platoon"]) for r in ops.list_accommodations(self.path)], [(rid, "Alef", "A")])
        with self.assertRaises(NotFoundError):
            ops.add_accommodation(self.path, self.ins, today=TODAY, **GOOD)       # only cadets have accommodations
        self.assertTrue(ops.delete_accommodation(self.path, rid, now=NOW))
        self.assertFalse(ops.delete_accommodation(self.path, rid, now=NOW))


if __name__ == "__main__":
    unittest.main()


class OperatorScript(Base):
    def run_cli(self, *argv):
        import contextlib
        import io
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("acc_cli", Path(__file__).resolve().parent.parent / "scripts" / "accommodations.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(["--db", self.path, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_add_list_delete_and_the_refusals(self):
        today = dt.date.today()
        start, end = today.isoformat(), (today + dt.timedelta(days=30)).isoformat()
        code, out, _ = self.run_cli("add", "ca@x.com", "--kind", "exemption", "--scope", "both", "--from", start, "--to", end, "--note", "קל")
        self.assertEqual(code, 0, out)
        code, out, _ = self.run_cli("list")
        self.assertIn("Alef", out)
        self.assertIn("exemption", out)
        self.assertEqual(self.run_cli("add", "nobody@x.com", "--kind", "relief", "--scope", "run", "--from", start, "--to", end)[0], 1)
        self.assertEqual(self.run_cli("add", "i@x.com", "--kind", "relief", "--scope", "run", "--from", start, "--to", end)[0], 1,
                         "only cadets have accommodations")
        self.assertEqual(self.run_cli("add", "ca@x.com", "--kind", "relief", "--scope", "run", "--from", end, "--to", start)[0], 3)
        self.assertEqual(self.run_cli("add", "ca@x.com", "--kind", "relief", "--scope", "run", "--from", start, "--to", end, "--note", "<b>")[0], 3)
        self.assertEqual(self.run_cli("delete", "1")[0], 0)
        self.assertEqual(self.run_cli("delete", "1")[0], 1)
