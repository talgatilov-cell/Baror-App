"""OPERATOR-ONLY entry points: the same admin operations WITHOUT an actor check.

Trust model: whoever can run ``scripts/*.py`` against the database file already has full read/write access to the
file, so authenticating them inside the app would add nothing. The web app MUST NOT import this module;
tests/test_admin.py::test_ops_is_not_imported_by_app_code fails if any module under ``baror/`` other than this one
imports it. This is also the only way to create the FIRST admin (bootstrap).
"""
from __future__ import annotations

from baror.storage import admin, db


def _op(db_path, core, *args, **kw):
    with db.transaction(db_path) as c:
        return core(c, None, *args, **kw)


def deactivate_user(db_path, user_id):
    return _op(db_path, admin._do_deactivate, user_id)


def reactivate_user(db_path, user_id):
    return _op(db_path, admin._do_reactivate, user_id)


def set_role(db_path, user_id, role):
    return _op(db_path, admin._do_set_role, user_id, role)


def create_platoon(db_path, name):
    return _op(db_path, admin._do_create_platoon, name)


def set_user_platoon(db_path, user_id, platoon_id):
    return _op(db_path, admin._do_set_user_platoon, user_id, platoon_id)


def grant_platoon(db_path, instructor_id, platoon_id):
    return _op(db_path, admin._do_grant, instructor_id, platoon_id)


def revoke_platoon(db_path, instructor_id, platoon_id):
    return _op(db_path, admin._do_revoke, instructor_id, platoon_id)


def list_accounts(db_path):
    return _op(db_path, admin._do_list_accounts)
