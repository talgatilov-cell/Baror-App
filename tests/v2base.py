from baror.storage import admin, db, ops, repo
from tests.base import DbCase


class V2Case(DbCase):
    """DbCase plus helpers for roles, platoons and grants (trusted ops path builds the fixtures)."""

    def platoon(self, name):
        return ops.create_platoon(self.path, name)

    def cadet(self, email, platoon=None, name=None, **kw):
        uid = self.user(email, name=name or email.split("@")[0].title(), **kw)
        if platoon is not None:
            ops.set_user_platoon(self.path, uid, platoon)
        return uid

    def staff(self, email, role="instructor", platoons=(), name=None):
        uid = self.user(email, name=name or email.split("@")[0].title(), role=role)
        for p in platoons:
            ops.grant_platoon(self.path, uid, p)
        return uid

    def audit_rows(self, **where):
        sql = "SELECT actor_id,action,target_user_id,platoon_id,outcome FROM audit_log"
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
        with db.reader(self.path) as c:
            return [tuple(r) for r in c.execute(sql + " ORDER BY id", tuple(where.values()))]
