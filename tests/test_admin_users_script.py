"""scripts/admin_users.py (operator CLI, v9): roles with titles and platoons, admin cap, set-title, release-email, grant,
list with warnings. `revoke` no longer exists."""
import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

from baror.storage import db, repo
from tests.base import DbCase

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("admin_users_script", ROOT / "scripts" / "admin_users.py")
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


class Cli(DbCase):
    def setUp(self):
        super().setUp()
        with db.transaction(self.path) as c:
            self.pa = int(c.execute("INSERT INTO platoons(name) VALUES('A')").lastrowid)
            self.pb = int(c.execute("INSERT INTO platoons(name) VALUES('B')").lastrowid)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = script.main(["--db", self.path, *argv])
        return code, out.getvalue(), err.getvalue()

    def acct(self, email, role="cadet"):
        return self.user(email, role=role, name="Name " + email[0].upper())

    def info(self, email):
        return repo.get_user(self.path, repo.get_login_record(self.path, email)["id"])

    def test_set_role_instructor_needs_title_and_platoon(self):
        self.acct("ins@example.com")
        self.assertEqual(self.run_cli("set-role", "ins@example.com", "instructor")[0], 1)
        self.assertEqual(self.run_cli("set-role", "ins@example.com", "instructor", "--title", "mm")[0], 1)
        self.assertEqual(self.run_cli("set-role", "ins@example.com", "instructor", "--platoon", "A")[0], 1)
        self.assertEqual(self.info("ins@example.com")["role"], "cadet")
        self.assertEqual(self.run_cli("set-role", "ins@example.com", "instructor", "--title", "mm", "--platoon", "A")[0], 0)
        u = self.info("ins@example.com")
        self.assertEqual((u["role"], u["title"], u["platoon_id"]), ("instructor", "mm", self.pa))

    def test_a_platoon_has_one_mm_and_five_cmdrs(self):
        for i in range(7):
            self.acct(f"i{i}@example.com")
        self.assertEqual(self.run_cli("set-role", "i0@example.com", "instructor", "--title", "mm", "--platoon", "A")[0], 0)
        code, _, err = self.run_cli("set-role", "i1@example.com", "instructor", "--title", "mm", "--platoon", "A")
        self.assertEqual(code, 1)
        self.assertIn("refused", err)
        for i in range(1, 6):
            self.assertEqual(self.run_cli("set-role", f"i{i}@example.com", "instructor", "--title", "cmdr", "--platoon", "A")[0], 0)
        self.assertEqual(self.run_cli("set-role", "i6@example.com", "instructor", "--title", "cmdr", "--platoon", "A")[0], 1)
        self.assertEqual(self.run_cli("set-role", "i6@example.com", "instructor", "--title", "cmdr", "--platoon", "B")[0], 0)

    def test_chiefs_need_a_title_and_there_are_at_most_two(self):
        for e in ("c1", "c2", "c3"):
            self.acct(f"{e}@example.com")
        self.assertEqual(self.run_cli("set-role", "c1@example.com", "chief")[0], 1)
        self.assertEqual(self.run_cli("set-role", "c1@example.com", "chief", "--title", "mp")[0], 0)
        self.assertEqual(self.run_cli("set-role", "c2@example.com", "chief", "--title", "mp")[0], 1, "titles are unique")
        self.assertEqual(self.run_cli("set-role", "c2@example.com", "chief", "--title", "smp")[0], 0)
        self.assertEqual(self.run_cli("set-role", "c3@example.com", "chief", "--title", "smp")[0], 1)

    def test_at_most_two_admins_and_the_last_one_stays(self):
        for e in ("a1", "a2", "a3"):
            self.acct(f"{e}@example.com")
        self.assertEqual(self.run_cli("set-role", "a1@example.com", "admin")[0], 0)
        self.assertEqual(self.run_cli("set-role", "a2@example.com", "admin")[0], 0)
        code, _, err = self.run_cli("set-role", "a3@example.com", "admin")
        self.assertEqual(code, 1)
        self.assertIn("refused", err)
        self.assertEqual(self.info("a3@example.com")["role"], "cadet")
        self.assertEqual(self.run_cli("set-role", "a1@example.com", "cadet")[0], 0)
        self.assertEqual(self.run_cli("set-role", "a2@example.com", "cadet")[0], 1, "the last admin cannot be demoted")

    def test_set_title_changes_only_the_title(self):
        self.acct("ins@example.com")
        self.acct("boss@example.com")
        self.run_cli("set-role", "ins@example.com", "instructor", "--title", "cmdr", "--platoon", "A")
        self.run_cli("set-role", "boss@example.com", "chief", "--title", "mp")
        self.assertEqual(self.run_cli("set-title", "ins@example.com", "mm")[0], 0)
        self.assertEqual(self.info("ins@example.com")["title"], "mm")
        self.assertEqual(self.run_cli("set-title", "ins@example.com", "smp")[0], 1, "a chief title does not fit an instructor")
        self.assertEqual(self.run_cli("set-title", "boss@example.com", "smp")[0], 0)
        self.assertEqual(self.run_cli("set-title", "nobody@example.com", "mm")[0], 1)

    def test_grant_moves_the_instructors_single_platoon(self):
        self.acct("ins@example.com")
        self.run_cli("set-role", "ins@example.com", "instructor", "--title", "cmdr", "--platoon", "A")
        self.assertEqual(self.run_cli("grant", "ins@example.com", "B")[0], 0)
        self.assertEqual(self.info("ins@example.com")["platoon_id"], self.pb)
        with db.reader(self.path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM instructor_platoons").fetchone()[0], 1)
        self.assertEqual(self.run_cli("grant", "ins@example.com", "Nope")[0], 1)

    def test_revoke_no_longer_exists(self):
        code, _, _ = self.run_cli("revoke", "ins@example.com", "A")
        self.assertEqual(code, 2)

    def test_assign_puts_a_cadet_in_a_platoon(self):
        self.acct("cad@example.com")
        self.assertEqual(self.run_cli("assign", "cad@example.com", "A")[0], 0)
        self.assertEqual(self.info("cad@example.com")["platoon_id"], self.pa)
        self.assertEqual(self.run_cli("assign", "cad@example.com", "-")[0], 0)
        self.assertIsNone(self.info("cad@example.com")["platoon_id"])

    def test_release_email_is_admin_attributed_cadet_only(self):
        self.acct("adm@example.com")
        self.run_cli("set-role", "adm@example.com", "admin")
        self.acct("cad@example.com")
        self.acct("ins@example.com")
        self.run_cli("set-role", "ins@example.com", "instructor", "--title", "cmdr", "--platoon", "A")
        self.assertEqual(self.run_cli("release-email", "cad@example.com", "--as", "adm@example.com")[0], 0)
        self.assertIsNone(repo.get_login_record(self.path, "cad@example.com"), "the address is free again")
        self.assertEqual(self.run_cli("release-email", "ins@example.com", "--as", "adm@example.com")[0], 1, "cadets only")
        self.acct("other@example.com")
        self.assertEqual(self.run_cli("release-email", "other@example.com", "--as", "ins@example.com")[0], 1,
                         "only an admin can be named with --as")
        self.assertEqual(self.run_cli("release-email", "other@example.com")[0], 2, "--as is required")

    def test_list_shows_titles_and_warns_about_structure(self):
        for i in range(8):
            self.acct(f"i{i}@example.com", role="instructor")        # legacy: instructors without a title or a platoon row
        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 8)
        self.assertIn("WARNING", err)
        self.assertIn("title", err)
        self.run_cli("set-role", "i0@example.com", "cadet")
        self.acct("x@example.com")
        self.run_cli("set-role", "x@example.com", "chief", "--title", "mp")
        _, out, _ = self.run_cli("list")
        line = [l for l in out.splitlines() if "x@example.com" in l][0]
        self.assertIn("chief", line)
        self.assertIn("mp", line)

    def test_warnings_for_counts_platoons_over_the_limits(self):
        rows = [dict(id=i, email=f"{i}@e", role="instructor", title="cmdr", active=1, platoon="A") for i in range(6)]
        rows += [dict(id=10 + i, email=f"{i}@f", role="chief", title=None, active=1, platoon=None) for i in range(3)]
        rows += [dict(id=20, email="gone@e", role="chief", title="mp", active=0, platoon=None)]
        text = " | ".join(script.warnings_for(rows))
        self.assertIn("platoon A", text)
        self.assertIn("3 active chief", text)
        self.assertNotIn("gone", text)
        self.assertEqual(script.warnings_for([]), [])

    def test_usage_errors(self):
        self.assertEqual(self.run_cli("set-role", "a@example.com", "emperor")[0], 2)
        self.assertEqual(self.run_cli("set-role", "a@example.com", "chief", "--title", "king")[0], 2)
        self.assertEqual(self.run_cli("set-role", "ghost@example.com", "cadet")[0], 1)


if __name__ == "__main__":
    unittest.main()
