"""SQLite connection handling, schema and migrations. Stdlib only; never imports streamlit.

Design rules (see research_notes/storage_sqlite.md):
* One short-lived connection per operation. Connections are never shared between threads
  (Streamlit runs every session in its own thread).
* Autocommit mode (isolation_level=None) + explicit ``BEGIN IMMEDIATE`` for writes, so the write lock is
  taken up-front and a busy database surfaces as a timeout instead of a mid-transaction SQLITE_BUSY.
* sqlite3 errors are translated into the domain hierarchy in ``baror.errors``.
* Corrupt files are NEVER overwritten or recreated.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Iterator

from baror.errors import ConflictError, CorruptedDataError, StorageError, ValidationError

BUSY_TIMEOUT_MS = 5000

# Schema v1. Differences from IMPLEMENTATION_BRIEF.md T1.2 (each VERIFIED in tests/test_db.py):
#   * test_date CHECK also requires a real calendar date (the brief's GLOB accepted '2026-99-99' and '2026-02-31').
#   * pushups upper bound stays 300 here; the UI limit (100 today) is a product decision, not a storage rule.
SCHEMA_V1 = """
CREATE TABLE users(
 id INTEGER PRIMARY KEY,
 email TEXT NOT NULL UNIQUE CHECK(email=lower(trim(email)) AND instr(email,'@')>1),
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 80),
 gender TEXT NOT NULL CHECK(gender IN ('M','F')),
 is_km INTEGER NOT NULL DEFAULT 0 CHECK(is_km IN (0,1)),
 pw_hash TEXT NOT NULL CHECK(length(pw_hash) > 0),
 must_change_pw INTEGER NOT NULL DEFAULT 0 CHECK(must_change_pw IN (0,1)),
 role TEXT NOT NULL DEFAULT 'cadet' CHECK(role IN ('cadet','admin')),
 failed_logins INTEGER NOT NULL DEFAULT 0,
 locked_until TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
CREATE TABLE results(
 id INTEGER PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 test_date TEXT NOT NULL CHECK(date(test_date) IS NOT NULL AND date(test_date)=test_date),
 run_tenths INTEGER CHECK(run_tenths IS NULL OR run_tenths BETWEEN 1 AND 72000),
 pushups INTEGER CHECK(pushups IS NULL OR pushups BETWEEN 0 AND 300),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 CHECK(run_tenths IS NOT NULL OR pushups IS NOT NULL));
CREATE INDEX idx_results_user_date ON results(user_id,test_date);
CREATE TABLE sessions(
 token_hash TEXT PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at TEXT NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0);
CREATE INDEX idx_sessions_user ON sessions(user_id);
"""

# Schema v2 (owner decisions of 2026-11-02: e-mail allowlist, instructor role, platoons, read-only cohort view).
# Applied to an existing v1 database IN PLACE with data intact. ``users`` must be rebuilt because SQLite cannot ALTER a
# CHECK constraint (v1 allows role IN ('cadet','admin') only). The rebuild follows the documented 12-step procedure
# (https://www.sqlite.org/lang_altertable.html#otheralter): create new table, copy, DROP old, RENAME new -> old.
# NOT the other way round (renaming the old table away would drag the FKs of ``sessions``/``results`` along with it).
# ``init_db`` runs this script with foreign_keys=OFF (cannot be changed inside a transaction; with it ON the
# DROP TABLE would cascade-delete every session and result) and runs PRAGMA foreign_key_check before COMMIT.
SCHEMA_V2 = """
CREATE TABLE platoons(
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL UNIQUE CHECK(name=trim(name) AND length(name) BETWEEN 1 AND 60),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
CREATE TABLE users_v2(
 id INTEGER PRIMARY KEY,
 email TEXT NOT NULL UNIQUE CHECK(email=lower(trim(email)) AND instr(email,'@')>1),
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 80),
 gender TEXT NOT NULL CHECK(gender IN ('M','F')),
 is_km INTEGER NOT NULL DEFAULT 0 CHECK(is_km IN (0,1)),
 pw_hash TEXT NOT NULL CHECK(length(pw_hash) > 0),
 must_change_pw INTEGER NOT NULL DEFAULT 0 CHECK(must_change_pw IN (0,1)),
 role TEXT NOT NULL DEFAULT 'cadet' CHECK(role IN ('cadet','instructor','admin')),
 failed_logins INTEGER NOT NULL DEFAULT 0,
 locked_until TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 platoon_id INTEGER REFERENCES platoons(id) ON DELETE SET NULL);
INSERT INTO users_v2(id,email,name,gender,is_km,pw_hash,must_change_pw,role,failed_logins,locked_until,created_at)
 SELECT id,email,name,gender,is_km,pw_hash,must_change_pw,role,failed_logins,locked_until,created_at FROM users;
DROP TABLE users;
ALTER TABLE users_v2 RENAME TO users;
CREATE INDEX idx_users_platoon ON users(platoon_id);
CREATE TABLE instructor_platoons(
 instructor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 platoon_id INTEGER NOT NULL REFERENCES platoons(id) ON DELETE CASCADE,
 granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 granted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 PRIMARY KEY(instructor_id, platoon_id)) WITHOUT ROWID;
CREATE INDEX idx_instructor_platoons_platoon ON instructor_platoons(platoon_id);
CREATE TABLE allowed_emails(
 email TEXT PRIMARY KEY CHECK(email=lower(trim(email)) AND instr(email,'@')>1 AND length(email)<=254),
 role TEXT NOT NULL DEFAULT 'cadet' CHECK(role IN ('cadet','instructor')),
 platoon_id INTEGER REFERENCES platoons(id) ON DELETE SET NULL,
 added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 source TEXT NOT NULL DEFAULT 'cli' CHECK(source IN ('cli','import','ui')),
 added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 used_at TEXT,
 CHECK(role='cadet' OR platoon_id IS NULL),
 CHECK(used_by IS NULL OR used_at IS NOT NULL));
CREATE TABLE audit_log(
 id INTEGER PRIMARY KEY,
 ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 action TEXT NOT NULL CHECK(length(action) BETWEEN 1 AND 40),
 target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 platoon_id INTEGER REFERENCES platoons(id) ON DELETE SET NULL,
 outcome TEXT NOT NULL DEFAULT 'ok' CHECK(outcome IN ('ok','denied')));
CREATE INDEX idx_audit_ts ON audit_log(ts);
CREATE INDEX idx_audit_target ON audit_log(target_user_id, ts);
CREATE INDEX idx_audit_actor ON audit_log(actor_id, ts)
"""

# user_version -> script that upgrades from (version-1). Append only; never edit a released entry.
MIGRATIONS: dict[int, str] = {1: SCHEMA_V1, 2: SCHEMA_V2}
LATEST_VERSION = max(MIGRATIONS)
# Versions whose script rebuilds a table that other tables reference: run with foreign_keys=OFF + foreign_key_check.
_FK_OFF_VERSIONS = frozenset({2})


def _translate(exc: sqlite3.Error, what: str) -> Exception:
    """Map a sqlite3 error to the domain hierarchy. The original is chained by the caller (``from exc``)."""
    msg = str(exc).lower()
    if isinstance(exc, sqlite3.IntegrityError):
        if "unique" in msg:
            return ConflictError(detail=f"{what}: {exc}")
        return ValidationError(detail=f"{what}: {exc}")
    if isinstance(exc, sqlite3.DatabaseError) and not isinstance(exc, sqlite3.OperationalError):
        # "file is not a database", "database disk image is malformed" (DatabaseError / its subclass)
        return CorruptedDataError(detail=f"{what}: {exc}")
    if isinstance(exc, sqlite3.OperationalError):
        if "malformed" in msg or "not a database" in msg:
            return CorruptedDataError(detail=f"{what}: {exc}")
        return StorageError(detail=f"{what}: {exc}")  # locked, unable to open, readonly, disk I/O, full
    return StorageError(detail=f"{what}: {exc}")


def connect(db_path: str | os.PathLike, *, timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Open a configured connection. Caller closes it (use ``transaction``/``reader`` instead where possible)."""
    try:
        conn = sqlite3.connect(os.fspath(db_path), timeout=timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")  # per-connection, off by default
        conn.execute(f"PRAGMA busy_timeout={int(timeout_ms)}")
        return conn
    except sqlite3.Error as exc:
        raise _translate(exc, "connect") from exc


@contextlib.contextmanager
def transaction(db_path, *, timeout_ms: int = BUSY_TIMEOUT_MS) -> Iterator[sqlite3.Connection]:
    """Write transaction: BEGIN IMMEDIATE ... COMMIT, ROLLBACK on any exception, connection always closed."""
    conn = connect(db_path, timeout_ms=timeout_ms)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as exc:
        raise _translate(exc, "transaction") from exc
    finally:
        conn.close()


@contextlib.contextmanager
def reader(db_path, *, timeout_ms: int = BUSY_TIMEOUT_MS) -> Iterator[sqlite3.Connection]:
    """Read-only use (a consistent snapshot per statement). Connection always closed."""
    conn = connect(db_path, timeout_ms=timeout_ms)
    try:
        yield conn
    except sqlite3.Error as exc:
        raise _translate(exc, "read") from exc
    finally:
        conn.close()


def user_version(db_path) -> int:
    with reader(db_path) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def init_db(db_path: str | os.PathLike) -> int:
    """Create or upgrade the database. Idempotent. Returns the schema version.

    * Missing file: created (parent directory must already exist and be writable; we do not mkdir).
    * Existing non-database file: raises CorruptedDataError and leaves the file byte-for-byte untouched.
    * Newer-than-known schema: raises StorageError (refuse to run an old app against a new database).
    """
    path = Path(db_path)
    if not path.parent.is_dir():
        raise StorageError(detail=f"data directory {path.parent} does not exist")
    conn = connect(path)
    try:
        # journal_mode=WAL is persistent in the file; it needs write access to the directory (-wal/-shm files).
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            raise StorageError(detail=f"could not enable WAL (got {mode!r}); is the filesystem a network mount?")
        # FULL = every commit is fsynced. Cost is irrelevant at this scale; durability is not.
        conn.execute("PRAGMA synchronous=FULL")
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current > LATEST_VERSION:
            raise StorageError(detail=f"database schema v{current} is newer than this app (v{LATEST_VERSION})")
        for version in range(current + 1, LATEST_VERSION + 1):
            _apply_migration(conn, version)
        return conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        raise _translate(exc, "init_db") from exc
    finally:
        conn.close()


def _apply_migration(conn: sqlite3.Connection, version: int) -> None:
    """Apply one migration inside BEGIN IMMEDIATE; all-or-nothing together with the user_version bump."""
    fk_off = version in _FK_OFF_VERSIONS
    if fk_off:
        # Step 1 of the 12-step procedure. It is a NO-OP inside a transaction, so it must come before BEGIN.
        conn.execute("PRAGMA foreign_keys=OFF")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise StorageError(detail="could not disable foreign keys for the table rebuild")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # re-check inside the lock: another process may have migrated while we waited
            if conn.execute("PRAGMA user_version").fetchone()[0] >= version:
                conn.execute("ROLLBACK")
                return
            for stmt in _split(MIGRATIONS[version]):
                conn.execute(stmt)
            if fk_off:  # step 10: refuse to commit a rebuild that left dangling references
                bad = conn.execute("PRAGMA foreign_key_check").fetchall()
                if bad:
                    raise StorageError(detail=f"migration v{version} aborted: {len(bad)} foreign key violation(s), "
                                              f"first in table {bad[0][0]!r}")
            conn.execute(f"PRAGMA user_version={version}")  # transactional in SQLite
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        if fk_off:  # step 12
            conn.execute("PRAGMA foreign_keys=ON")


def _split(script: str) -> list[str]:
    # Our scripts contain no triggers/semicolons inside strings, so a plain split is safe and keeps the
    # whole migration inside one explicit transaction (executescript would COMMIT implicitly).
    return [s.strip() for s in script.split(";") if s.strip()]


def integrity_check(db_path) -> list[str]:
    """Returns [] when healthy, otherwise the messages reported by SQLite (plus FK violations)."""
    problems: list[str] = []
    with reader(db_path) as c:
        rows = [r[0] for r in c.execute("PRAGMA integrity_check").fetchall()]
        if rows != ["ok"]:
            problems.extend(rows)
        fk = c.execute("PRAGMA foreign_key_check").fetchall()
        problems.extend(f"foreign key violation: table={r[0]} rowid={r[1]}" for r in fk)
    return problems
