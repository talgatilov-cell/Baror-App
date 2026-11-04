"""baror.domain.roles: the constants of the company structure and the UI hint ``may_change``."""
import unittest

from baror.domain import roles as R


class Constants(unittest.TestCase):
    def test_structure_limits(self):
        self.assertEqual((R.MAX_ADMINS, R.MAX_CHIEFS, R.MAX_CMDRS_PER_PLATOON), (2, 2, 5))

    def test_titles_belong_to_one_role_each(self):
        self.assertEqual(R.CHIEF_TITLES, ("mp", "smp"))
        self.assertEqual(R.INSTRUCTOR_TITLES, ("mm", "cmdr"))
        self.assertEqual(set(R.ALL_TITLES), {"mp", "smp", "mm", "cmdr"})
        self.assertEqual(R.TITLES_BY_ROLE, {"chief": ("mp", "smp"), "instructor": ("mm", "cmdr")})

    def test_title_allowed(self):
        self.assertTrue(R.title_allowed("chief", "mp"))
        self.assertTrue(R.title_allowed("instructor", "cmdr"))
        self.assertTrue(R.title_allowed("cadet", None), "no title is fine for everybody (a legacy account)")
        self.assertTrue(R.title_allowed("instructor", None))
        for role, title in (("chief", "mm"), ("instructor", "mp"), ("cadet", "cmdr"), ("admin", "mp"), ("instructor", "boss")):
            self.assertFalse(R.title_allowed(role, title), (role, title))


class MayChange(unittest.TestCase):
    ROLES = ("cadet", "instructor", "chief", "admin")

    def test_the_full_matrix(self):
        allowed = {
            "admin": {("cadet", "instructor"), ("cadet", "chief"), ("instructor", "cadet"), ("instructor", "chief"),
                      ("chief", "cadet"), ("chief", "instructor")},
            "chief": {("cadet", "instructor"), ("instructor", "cadet")},
            "instructor": set(),
            "cadet": set(),
        }
        for actor in self.ROLES:
            for src in self.ROLES:
                for dst in self.ROLES:
                    with self.subTest(actor=actor, src=src, dst=dst):
                        self.assertEqual(R.may_change(actor, src, dst), (src, dst) in allowed[actor])

    def test_nobody_can_grant_admin_or_touch_an_admin(self):
        for actor in self.ROLES:
            for other in self.ROLES:
                self.assertFalse(R.may_change(actor, other, "admin"))
                self.assertFalse(R.may_change(actor, "admin", other))

    def test_chief_role_is_admin_only(self):
        self.assertTrue(R.may_change("admin", "cadet", "chief"))
        for actor in ("chief", "instructor", "cadet"):
            for src in ("cadet", "instructor"):
                self.assertFalse(R.may_change(actor, src, "chief"))
            self.assertFalse(R.may_change(actor, "chief", "cadet"))
            self.assertFalse(R.may_change(actor, "chief", "instructor"))

    def test_a_change_to_the_same_role_is_not_a_change(self):
        for actor in self.ROLES:
            for role in self.ROLES:
                self.assertFalse(R.may_change(actor, role, role))

    def test_unknown_values_are_refused(self):
        self.assertFalse(R.may_change("root", "cadet", "instructor"))
        self.assertFalse(R.may_change("admin", "cadet", "commander"))
        self.assertFalse(R.may_change(None, "cadet", "instructor"))


if __name__ == "__main__":
    unittest.main()
