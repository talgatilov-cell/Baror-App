import os
import tempfile
import unittest

from baror.auth import service
from baror.storage import db, repo


class DbCase(unittest.TestCase):
    """Fresh database in a temp dir per test."""

    def setUp(self):
        service.reset_unknown_email_backoff()   # module-level state must not leak between tests
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, "baror.db")
        db.init_db(self.path)

    def user(self, email="a@example.com", **kw):
        args = dict(email=email, name="Test", gender="M", is_km=False, pw_hash="x$hash")
        args.update(kw)
        return repo.create_user(self.path, **args)
