"""Roles, titles and structural limits of the course (schema v9). Pure Python; no I/O, no streamlit.

One company (פלוגה): exactly one מ"פ (``mp``) and one סמ"פ (``smp``), both with the role ``chief`` (at most
``MAX_CHIEFS`` active chiefs, titles unique). Every platoon has at most one מ"מ (``mm``) and up to
``MAX_CMDRS_PER_PLATOON`` מ"כים (``cmdr``), both with the role ``instructor``; an instructor belongs to exactly one
platoon. Admins (``MAX_ADMINS``) are created only by the operator script.

The storage layer (``baror.storage.people``) enforces every rule inside the write transaction; ``may_change`` here is
only the hint the screens use to decide which choices to offer.
"""
from __future__ import annotations

MAX_ADMINS = 2
MAX_CHIEFS = 2
MAX_CMDRS_PER_PLATOON = 5

ROLES = ("cadet", "instructor", "chief", "admin")
CHIEF_TITLES = ("mp", "smp")
INSTRUCTOR_TITLES = ("mm", "cmdr")
TITLES_BY_ROLE = {"chief": CHIEF_TITLES, "instructor": INSTRUCTOR_TITLES}
ALL_TITLES = CHIEF_TITLES + INSTRUCTOR_TITLES

# Who may move an account from one role to another. Nobody can grant ``admin`` from the app, and nobody can touch an
# admin row. An instructor <-> cadet change is open to the chief; everything that involves the chief role is admin only.
_ADMIN_MOVES = frozenset({("cadet", "instructor"), ("cadet", "chief"), ("instructor", "cadet"), ("instructor", "chief"),
                          ("chief", "cadet"), ("chief", "instructor")})
_CHIEF_MOVES = frozenset({("cadet", "instructor"), ("instructor", "cadet")})


def title_allowed(role: str, title: str | None) -> bool:
    """True when ``title`` may go with ``role`` (None goes with every role: a legacy account has no title)."""
    return title is None or title in TITLES_BY_ROLE.get(role, ())


def may_change(actor_role: str, from_role: str, to_role: str) -> bool:
    """UI hint: may an account with ``actor_role`` change a ``from_role`` account to ``to_role``? (Self-change and the
    per-case limits are checked in storage.)"""
    if actor_role == "admin":
        return (from_role, to_role) in _ADMIN_MOVES
    if actor_role == "chief":
        return (from_role, to_role) in _CHIEF_MOVES
    return False
