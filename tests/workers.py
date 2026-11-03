"""Subprocess entry points for the multi-process / crash tests. Run as: python -m tests.workers <mode> <db> [...]"""
import os
import sys
import time

from baror.errors import BarorError
from baror.storage import db, repo


def insert_results(path, user_id, n):
    for i in range(int(n)):
        repo.add_result(path, int(user_id), test_date="2026-01-01", pushups=i % 100)


def register_same(path, email):
    try:
        repo.create_user(path, email=email, name="n", gender="M", is_km=False, pw_hash="h")
        print("OK")
    except BarorError as e:
        print(type(e).__name__)


def open_tx_and_hang(path, user_id):
    """Insert many rows inside a transaction that is never committed, announce READY, then sleep."""
    c = db.connect(path)
    c.execute("BEGIN IMMEDIATE")
    for i in range(2000):
        c.execute("INSERT INTO results(user_id,test_date,pushups) VALUES(?,?,?)", (int(user_id), "2026-02-02", i % 100))
    print("READY", flush=True)
    time.sleep(60)


def commit_batches_forever(path, user_id, batch):
    """Each loop iteration is ONE transaction inserting ``batch`` rows. Killed from outside."""
    batch = int(batch)
    print("READY", flush=True)
    while True:
        with db.transaction(path) as c:
            for i in range(batch):
                c.execute("INSERT INTO results(user_id,test_date,pushups) VALUES(?,?,?)", (int(user_id), "2026-03-03", i))


def as_uid_init(path, uid):
    os.setgid(int(uid)); os.setuid(int(uid))
    from baror.errors import BarorError as BE
    try:
        db.init_db(path)
        print("INIT_OK")
    except BE as e:
        print("ERR", type(e).__name__)


def as_uid_read(path, uid):
    os.setgid(int(uid)); os.setuid(int(uid))
    from baror.errors import BarorError as BE
    try:
        with db.reader(path) as c:
            n = c.execute("SELECT count(*) FROM users").fetchone()[0]
        print("READ_OK", n)
    except BE as e:
        print("ERR", type(e).__name__)


def register_allowlisted(path, email):
    """Allowlist registration race: prints OK or the exception class name (RegistrationDeniedError for the losers)."""
    try:
        repo.register_with_allowlist(path, email=email, name="n", gender="M", is_km=False, pw_hash="h")
        print("OK")
    except BarorError as e:
        print(type(e).__name__)


def init_only(path):
    try:
        print("VERSION", db.init_db(path))
    except BarorError as e:
        print("ERR", type(e).__name__)


if __name__ == "__main__":
    fn = globals()[sys.argv[1]]
    fn(*sys.argv[2:])
