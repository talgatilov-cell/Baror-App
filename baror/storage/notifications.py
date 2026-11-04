"""Counts for the top-bar bell (chief and admin). Stdlib only; never imports streamlit.

``counts`` returns numbers only, so a success is not audited (the bar asks on every screen update). A caller who may
not use it gets ``AuthError`` and, if the account exists, a ``denied`` audit row (the shared actor gate of
``baror.storage.people``). The chief sees all three kinds, an admin only the change requests.
"""
from __future__ import annotations

from typing import Any

from baror.storage import people

__all__ = ["counts"]


def counts(db_path, actor_id, *, now=None) -> dict[str, Any]:
    """{"accommodation_requests", "deletion_requests", "change_requests", "total"}: pending accommodations awaiting the
    chief's approval, pending accommodation deletion requests, pending cadet change requests (add_conflict / edit / delete /
    nominate_cmdr). Admin: only ``change_requests`` (the others are 0)."""
    def body(c, a):
        change = c.execute("SELECT count(*) FROM cadet_change_requests r JOIN users t ON t.id=r.target_user_id "
                           "WHERE r.status='pending' AND (? OR t.role<>'admin')",
                           (1 if a["role"] == "admin" else 0,)).fetchone()[0]
        accom = deletion = 0
        if a["role"] == "chief":
            # same rows as accommodations.pending_requests / pending_deletions (a cadet's record only)
            accom = c.execute("SELECT count(*) FROM accommodations a JOIN users u ON u.id=a.user_id "
                              "WHERE a.approval='pending' AND u.role='cadet'").fetchone()[0]
            deletion = c.execute("SELECT count(*) FROM accommodations a JOIN users u ON u.id=a.user_id "
                                 "WHERE a.delete_requested_at IS NOT NULL AND u.role='cadet'").fetchone()[0]
        return {"accommodation_requests": accom, "deletion_requests": deletion, "change_requests": change,
                "total": accom + deletion + change}
    return people._run(db_path, actor_id, "notifications.counts", ("chief", "admin"), body, now=now)
