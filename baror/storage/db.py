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


# Schema v3 (ADR-0002, pseudonymized retention). Results hang off a random pseudonym, not off a user row, so that
# deleting the identity row in ``users`` (name, e-mail, password hash, pseudonym) leaves de-identified results behind.
#   pseudonyms        parent of results; holds NO personal data. gender/is_km/platoon are copied here only when a
#                     cadet is de-identified (the columns are NULL while the account is live).
#   users.pseudonym   THE identity link (the only table that connects a person to a pseudonym).
#   results.pseudonym FK to pseudonyms, not to users. Erasure by the user deletes the pseudonyms row, which cascades
#                     to the users row, the sessions and the results.
#   platoons.course_end  start of the retention clock (set by the operator).
# Rebuilds ``users`` and ``results`` with foreign_keys=OFF (see SCHEMA_V2 for the procedure). Pseudonyms for existing
# users come from SQLite's randomblob (seeded from the operating system); later ones from ``secrets``.
SCHEMA_V3 = """
CREATE TABLE pseudonyms(
 pseudonym TEXT PRIMARY KEY CHECK(length(pseudonym)=32 AND pseudonym NOT GLOB '*[^0-9a-f]*'),
 gender TEXT CHECK(gender IS NULL OR gender IN ('M','F')),
 is_km INTEGER CHECK(is_km IS NULL OR is_km IN (0,1)),
 platoon_id INTEGER REFERENCES platoons(id) ON DELETE SET NULL,
 deidentified_at TEXT,
 CHECK((deidentified_at IS NULL) = (gender IS NULL)));
CREATE TABLE _pmap(user_id INTEGER PRIMARY KEY, pseudonym TEXT NOT NULL UNIQUE);
INSERT INTO _pmap(user_id,pseudonym) SELECT id, lower(hex(randomblob(16))) FROM users;
INSERT INTO pseudonyms(pseudonym) SELECT pseudonym FROM _pmap;
CREATE TABLE users_v3(
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
 platoon_id INTEGER REFERENCES platoons(id) ON DELETE SET NULL,
 pseudonym TEXT NOT NULL UNIQUE REFERENCES pseudonyms(pseudonym) ON DELETE CASCADE);
INSERT INTO users_v3(id,email,name,gender,is_km,pw_hash,must_change_pw,role,failed_logins,locked_until,created_at,active,platoon_id,pseudonym)
 SELECT u.id,u.email,u.name,u.gender,u.is_km,u.pw_hash,u.must_change_pw,u.role,u.failed_logins,u.locked_until,u.created_at,u.active,u.platoon_id,m.pseudonym
 FROM users u JOIN _pmap m ON m.user_id=u.id;
CREATE TABLE results_v3(
 id INTEGER PRIMARY KEY,
 pseudonym TEXT NOT NULL REFERENCES pseudonyms(pseudonym) ON DELETE CASCADE,
 test_date TEXT NOT NULL CHECK(date(test_date) IS NOT NULL AND date(test_date)=test_date),
 run_tenths INTEGER CHECK(run_tenths IS NULL OR run_tenths BETWEEN 1 AND 72000),
 pushups INTEGER CHECK(pushups IS NULL OR pushups BETWEEN 0 AND 300),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 CHECK(run_tenths IS NOT NULL OR pushups IS NOT NULL));
INSERT INTO results_v3(id,pseudonym,test_date,run_tenths,pushups,created_at)
 SELECT r.id,m.pseudonym,r.test_date,r.run_tenths,r.pushups,r.created_at FROM results r JOIN _pmap m ON m.user_id=r.user_id;
DROP TABLE results;
DROP TABLE users;
ALTER TABLE users_v3 RENAME TO users;
ALTER TABLE results_v3 RENAME TO results;
DROP TABLE _pmap;
CREATE INDEX idx_users_platoon ON users(platoon_id);
CREATE INDEX idx_results_pseudonym_date ON results(pseudonym,test_date);
ALTER TABLE platoons ADD COLUMN course_end TEXT CHECK(course_end IS NULL OR (date(course_end) IS NOT NULL AND date(course_end)=course_end))
"""

# Schema v4 (ADR-0001): the `chief` role (chief and deputy chief). Only the role CHECK of ``users`` changes, and SQLite
# cannot alter a CHECK, so ``users`` is rebuilt once more with foreign_keys=OFF (procedure: see SCHEMA_V2).
SCHEMA_V4 = """
CREATE TABLE users_v4(
 id INTEGER PRIMARY KEY,
 email TEXT NOT NULL UNIQUE CHECK(email=lower(trim(email)) AND instr(email,'@')>1),
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 80),
 gender TEXT NOT NULL CHECK(gender IN ('M','F')),
 is_km INTEGER NOT NULL DEFAULT 0 CHECK(is_km IN (0,1)),
 pw_hash TEXT NOT NULL CHECK(length(pw_hash) > 0),
 must_change_pw INTEGER NOT NULL DEFAULT 0 CHECK(must_change_pw IN (0,1)),
 role TEXT NOT NULL DEFAULT 'cadet' CHECK(role IN ('cadet','instructor','admin','chief')),
 failed_logins INTEGER NOT NULL DEFAULT 0,
 locked_until TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 platoon_id INTEGER REFERENCES platoons(id) ON DELETE SET NULL,
 pseudonym TEXT NOT NULL UNIQUE REFERENCES pseudonyms(pseudonym) ON DELETE CASCADE);
INSERT INTO users_v4(id,email,name,gender,is_km,pw_hash,must_change_pw,role,failed_logins,locked_until,created_at,active,platoon_id,pseudonym)
 SELECT id,email,name,gender,is_km,pw_hash,must_change_pw,role,failed_logins,locked_until,created_at,active,platoon_id,pseudonym FROM users;
DROP TABLE users;
ALTER TABLE users_v4 RENAME TO users;
CREATE INDEX idx_users_platoon ON users(platoon_id)
"""

# Schema v5: a date CHECK that holds on EVERY SQLite build. Found by running the suite inside the production image
# (Debian bookworm, SQLite 3.40.1): there ``date('2026-02-31')`` returns '2026-02-31' unchanged, so the v1..v4 check
# ``date(x)=x`` accepted impossible dates (newer builds normalise them to '2026-03-03'). ``date(julianday(x))`` is
# normalised on both, so ``date(julianday(x))=x`` refuses 2026-02-31, 2026-02-30, 2026-04-31 and 2026-02-29 everywhere.
# Rebuilds ``results`` and ``platoons`` (the column ``course_end``), foreign keys off; a row that already violates the
# stricter check aborts the whole migration (the app and the JSON migration never stored one) instead of being dropped.
SCHEMA_V5 = """
CREATE TABLE results_v5(
 id INTEGER PRIMARY KEY,
 pseudonym TEXT NOT NULL REFERENCES pseudonyms(pseudonym) ON DELETE CASCADE,
 test_date TEXT NOT NULL CHECK(date(test_date) IS NOT NULL AND date(julianday(test_date))=test_date),
 run_tenths INTEGER CHECK(run_tenths IS NULL OR run_tenths BETWEEN 1 AND 72000),
 pushups INTEGER CHECK(pushups IS NULL OR pushups BETWEEN 0 AND 300),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 CHECK(run_tenths IS NOT NULL OR pushups IS NOT NULL));
INSERT INTO results_v5(id,pseudonym,test_date,run_tenths,pushups,created_at)
 SELECT id,pseudonym,test_date,run_tenths,pushups,created_at FROM results;
DROP TABLE results;
ALTER TABLE results_v5 RENAME TO results;
CREATE INDEX idx_results_pseudonym_date ON results(pseudonym,test_date);
CREATE TABLE platoons_v5(
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL UNIQUE CHECK(name=trim(name) AND length(name) BETWEEN 1 AND 60),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 course_end TEXT CHECK(course_end IS NULL OR (date(course_end) IS NOT NULL AND date(julianday(course_end))=course_end)));
INSERT INTO platoons_v5(id,name,created_at,course_end) SELECT id,name,created_at,course_end FROM platoons;
DROP TABLE platoons;
ALTER TABLE platoons_v5 RENAME TO platoons
"""

# Schema v6: accommodations (הקלות ופטורים), owner decision of 2026-11-03. One new table, nothing is rebuilt.
# The table hangs off the USER (not the results pseudonym): a record describes a person and may be health related, so
# it is deleted together with the account (erasure) and by the retention job (de-identification), while the results
# stay under their pseudonym. ``source`` says who recorded it (a cadet cannot edit or delete it afterwards: that is
# enforced in baror.storage.accommodations; the CHECKs here are the backstop for the values). Dates use the same
# build-independent check as v5; the 3-year limit uses SQLite's date arithmetic. (No semicolons inside the script:
# ``_split`` cuts on them.)
SCHEMA_V6 = """
CREATE TABLE accommodations(
 id INTEGER PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('relief','exemption')),
 scope TEXT NOT NULL CHECK(scope IN ('run','pushups','both')),
 start_date TEXT NOT NULL CHECK(date(start_date) IS NOT NULL AND date(julianday(start_date))=start_date),
 end_date TEXT NOT NULL CHECK(date(end_date) IS NOT NULL AND date(julianday(end_date))=end_date),
 note TEXT CHECK(note IS NULL OR length(note) BETWEEN 1 AND 100),
 source TEXT NOT NULL CHECK(source IN ('cadet','instructor','chief','admin')),
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 delete_requested_at TEXT,
 delete_requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 CHECK(end_date >= start_date),
 CHECK(end_date <= date(start_date,'+3 years')),
 CHECK(delete_requested_by IS NULL OR delete_requested_at IS NOT NULL));
CREATE INDEX idx_accommodations_user ON accommodations(user_id);
CREATE INDEX idx_accommodations_pending ON accommodations(delete_requested_at) WHERE delete_requested_at IS NOT NULL
"""

# Schema v7: the approval step of accommodations (owner request of 2026-11-03). A record entered by a cadet starts
# 'pending' and counts only once the chief has 'approved' it; 'rejected' keeps the row (cadet and staff still see it,
# labelled) but it is never in effect. Records of instructors, admins and the chief are 'approved' from the start.
# Three columns are ADDED (nothing is rebuilt; ``DEFAULT 'approved'`` keeps an operator insert that names no approval
# approved). Existing rows: source 'cadet' -> 'pending', all others stay 'approved'. The CHECKs on the two decision
# columns keep the trio consistent (a pending row has no decision; a decider always has a time).
SCHEMA_V7 = """
ALTER TABLE accommodations ADD COLUMN approval TEXT NOT NULL DEFAULT 'approved' CHECK(approval IN ('pending','approved','rejected'));
ALTER TABLE accommodations ADD COLUMN decided_at TEXT CHECK(decided_at IS NULL OR approval<>'pending');
ALTER TABLE accommodations ADD COLUMN decided_by INTEGER REFERENCES users(id) ON DELETE SET NULL CHECK(decided_by IS NULL OR decided_at IS NOT NULL);
UPDATE accommodations SET approval='pending' WHERE source='cadet';
CREATE INDEX idx_accommodations_approval ON accommodations(created_at) WHERE approval='pending'
"""

# Schema v8: the stored aggregated reports (owner request of 2026-11-03: weekly report every Thursday, monthly report at
# the end of the month; see baror.domain.reports) and their e-mail delivery bookkeeping. Two new tables, nothing is
# rebuilt. ``reports.pdf`` holds the finished PDF: aggregated figures only, NO names, groups under 5 hidden, so it is
# not personal data and it stays when cadets are de-identified; it is part of the ordinary database backup, which is why
# it lives here and not in a file (no new volume). At most one report per type and period start (a second generation is
# a no-op). ``report_deliveries`` has one row per report and RECIPIENT ACCOUNT (never an address: it is read from
# ``users`` when sending and the row follows the account if it is deleted): sent_at when the mail went out, ``last_error``
# a short code (lower-case letters, digits and underscore, never exception text) after a failed attempt.
# ``report_settings`` is ONE row: the switch for automatic e-mailing, OFF by default (mail_enabled=0 after the migration);
# the chief flips it on the screen, which records who and when (changed_at is also the cut-off: only reports generated
# at or after the moment it was switched ON are mailed, never the older backlog). scripts/backup.py --restore switches it
# off again.
SCHEMA_V8 = """
CREATE TABLE reports(
 id INTEGER PRIMARY KEY,
 period_type TEXT NOT NULL CHECK(period_type IN ('weekly','monthly')),
 period_start TEXT NOT NULL CHECK(date(period_start) IS NOT NULL AND date(julianday(period_start))=period_start),
 period_end TEXT NOT NULL CHECK(date(period_end) IS NOT NULL AND date(julianday(period_end))=period_end),
 generated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 pdf BLOB NOT NULL CHECK(length(pdf) > 0),
 CHECK(period_end >= period_start),
 UNIQUE(period_type, period_start));
CREATE INDEX idx_reports_end ON reports(period_type, period_end);
CREATE TABLE report_deliveries(
 report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 sent_at TEXT,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 last_error TEXT CHECK(last_error IS NULL OR (length(last_error) BETWEEN 1 AND 40 AND last_error NOT GLOB '*[^a-z_0-9]*')),
 PRIMARY KEY(report_id, user_id));
CREATE INDEX idx_report_deliveries_unsent ON report_deliveries(report_id) WHERE sent_at IS NULL;
CREATE TABLE report_settings(
 id INTEGER PRIMARY KEY CHECK(id=1),
 mail_enabled INTEGER NOT NULL DEFAULT 0 CHECK(mail_enabled IN (0,1)),
 changed_at TEXT,
 changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL);
INSERT INTO report_settings(id) VALUES(1)
"""

# Schema v9: open registration and the company structure (owner decisions of 2026-11-04). ``users`` is NOT rebuilt.
#   users.title            מ"פ/סמ"פ (chief: mp, smp) or מ"מ/מ"כ (instructor: mm, cmdr); NULL for everyone else and for a
#                          legacy chief/instructor (shown as "title not set"). Unique among ACTIVE chiefs.
#   platoons.join_epoch    bumping it revokes every signed join link of that platoon.
#   signup_events          one row per sign-up, no personal data: the database-wide hourly sign-up cap. It survives the
#                          deletion of an account (unlike a count over users) and is pruned on every sign-up.
#   cadet_change_requests  what an instructor may ASK for: add a cadet who sits in another platoon (add_conflict), edit
#                          (name/gender/km/platoon only), remove from the platoon (delete), and, for a מ"מ, nominate a
#                          registered cadet as מ"כ of the own platoon (nominate_cmdr). A chief or admin decides.
#   allowed_emails         DROPPED: there is no registration gate any more; roles are granted to existing accounts.
#   instructor_platoons    ONE row per instructor (PRIMARY KEY instructor_id). Rows of a user who is not an instructor
#                          and extra platoons are dropped, each one with an audit row ``migrate.v9_grant_dropped``
#                          (written BEFORE the table is dropped). The kept platoon is the one with the earliest granted_at
#                          (tie: lowest platoon id). ``is_mm`` mirrors ``users.title='mm' AND users.active=1`` and is what
#                          makes "one מ"מ per platoon" a DATABASE rule (partial unique index); the storage layer keeps it
#                          in step in the same transaction. A legacy instructor has title NULL and is_mm 0, and counts
#                          toward the מ"כ limit until a title is set.
# Existing users keep role and platoon; platoons already over the limits are left alone (limits apply to new grants).
# Nothing here is rebuilt that other tables reference, so foreign keys stay ON. (No semicolons inside the strings:
# ``_split`` cuts on them.)
SCHEMA_V9 = """
ALTER TABLE users ADD COLUMN title TEXT CHECK(title IS NULL OR (role='chief' AND title IN ('mp','smp')) OR (role='instructor' AND title IN ('mm','cmdr')));
CREATE UNIQUE INDEX uq_users_chief_title ON users(title) WHERE role='chief' AND active=1 AND title IS NOT NULL;
ALTER TABLE platoons ADD COLUMN join_epoch INTEGER NOT NULL DEFAULT 0 CHECK(join_epoch>=0);
CREATE TABLE signup_events(
 id INTEGER PRIMARY KEY,
 ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
CREATE INDEX idx_signup_events_ts ON signup_events(ts);
CREATE TABLE cadet_change_requests(
 id INTEGER PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN ('add_conflict','edit','delete','nominate_cmdr')),
 requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 requested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 target_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 platoon_id INTEGER REFERENCES platoons(id) ON DELETE SET NULL,
 new_name TEXT CHECK(new_name IS NULL OR length(trim(new_name)) BETWEEN 1 AND 80),
 new_gender TEXT CHECK(new_gender IS NULL OR new_gender IN ('M','F')),
 new_is_km INTEGER CHECK(new_is_km IS NULL OR new_is_km IN (0,1)),
 new_platoon_id INTEGER REFERENCES platoons(id) ON DELETE SET NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
 decided_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 decided_at TEXT,
 CHECK(kind<>'edit' OR new_name IS NOT NULL OR new_gender IS NOT NULL OR new_is_km IS NOT NULL OR new_platoon_id IS NOT NULL),
 CHECK(kind='edit' OR (new_name IS NULL AND new_gender IS NULL AND new_is_km IS NULL AND new_platoon_id IS NULL)),
 CHECK(kind NOT IN ('add_conflict','nominate_cmdr') OR platoon_id IS NOT NULL),
 CHECK((status='pending')=(decided_at IS NULL)),
 CHECK(decided_by IS NULL OR decided_at IS NOT NULL));
CREATE INDEX idx_ccr_pending ON cadet_change_requests(requested_at) WHERE status='pending';
CREATE UNIQUE INDEX uq_ccr_open ON cadet_change_requests(kind,target_user_id,requested_by) WHERE status='pending';
DROP TABLE allowed_emails;
INSERT INTO audit_log(actor_id,action,target_user_id,platoon_id)
 SELECT NULL,'migrate.v9_grant_dropped',ip.instructor_id,ip.platoon_id FROM instructor_platoons ip
 JOIN users u ON u.id=ip.instructor_id
 WHERE NOT (u.role='instructor' AND ip.platoon_id=(SELECT x.platoon_id FROM instructor_platoons x
  WHERE x.instructor_id=ip.instructor_id ORDER BY x.granted_at, x.platoon_id LIMIT 1));
CREATE TABLE instructor_platoons_v9(
 instructor_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 platoon_id INTEGER NOT NULL REFERENCES platoons(id) ON DELETE CASCADE,
 is_mm INTEGER NOT NULL DEFAULT 0 CHECK(is_mm IN (0,1)),
 granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 granted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
INSERT INTO instructor_platoons_v9(instructor_id,platoon_id,granted_by,granted_at)
 SELECT ip.instructor_id,ip.platoon_id,ip.granted_by,ip.granted_at FROM instructor_platoons ip
 JOIN users u ON u.id=ip.instructor_id
 WHERE u.role='instructor' AND ip.platoon_id=(SELECT x.platoon_id FROM instructor_platoons x
  WHERE x.instructor_id=ip.instructor_id ORDER BY x.granted_at, x.platoon_id LIMIT 1);
DROP TABLE instructor_platoons;
ALTER TABLE instructor_platoons_v9 RENAME TO instructor_platoons;
CREATE INDEX idx_instructor_platoons_platoon ON instructor_platoons(platoon_id);
CREATE UNIQUE INDEX uq_one_mm_per_platoon ON instructor_platoons(platoon_id) WHERE is_mm=1
"""

# user_version -> script that upgrades from (version-1). Append only; never edit a released entry.
MIGRATIONS: dict[int, str] = {1: SCHEMA_V1, 2: SCHEMA_V2, 3: SCHEMA_V3, 4: SCHEMA_V4, 5: SCHEMA_V5, 6: SCHEMA_V6,
                              7: SCHEMA_V7, 8: SCHEMA_V8, 9: SCHEMA_V9}
LATEST_VERSION = max(MIGRATIONS)
# Versions whose script rebuilds a table that other tables reference: run with foreign_keys=OFF + foreign_key_check.
_FK_OFF_VERSIONS = frozenset({2, 3, 4, 5})


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


def connect(db_path: str | os.PathLike, *, timeout_ms: int = BUSY_TIMEOUT_MS,
            secure_delete: bool = False) -> sqlite3.Connection:
    """Open a configured connection. Caller closes it (use ``transaction``/``reader`` instead where possible).

    ``secure_delete``: overwrite deleted content with zeros instead of leaving it in free pages. Only the retention
    job asks for it (it costs write time and ordinary deletes do not need it).
    """
    try:
        conn = sqlite3.connect(os.fspath(db_path), timeout=timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")  # per-connection, off by default
        conn.execute(f"PRAGMA busy_timeout={int(timeout_ms)}")
        if secure_delete:
            conn.execute("PRAGMA secure_delete=ON")
        return conn
    except sqlite3.Error as exc:
        raise _translate(exc, "connect") from exc


@contextlib.contextmanager
def transaction(db_path, *, timeout_ms: int = BUSY_TIMEOUT_MS, secure_delete: bool = False) -> Iterator[sqlite3.Connection]:
    """Write transaction: BEGIN IMMEDIATE ... COMMIT, ROLLBACK on any exception, connection always closed."""
    conn = connect(db_path, timeout_ms=timeout_ms, secure_delete=secure_delete)
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


def init_db(db_path: str | os.PathLike, *, target_version: int | None = None) -> int:
    """Create or upgrade the database. Idempotent. Returns the schema version.

    ``target_version`` (default: the latest) stops early; it exists so tests can exercise one migration step at a time.

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
        stop = LATEST_VERSION if target_version is None else target_version
        if not 1 <= stop <= LATEST_VERSION:
            raise StorageError(detail=f"unknown target schema version {target_version!r}")
        for version in range(current + 1, stop + 1):
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
            before = _row_counts(conn, version)
            for stmt in _split(MIGRATIONS[version]):
                conn.execute(stmt)
            if before is not None and _row_counts(conn, version) != before:   # never drop rows silently
                raise StorageError(detail=f"migration v{version} aborted: row counts changed {before} -> "
                                          f"{_row_counts(conn, version)}")
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


def _row_counts(conn: sqlite3.Connection, version: int):
    """Rows that a rebuilding migration must carry over unchanged (v3 joins results to users: an orphan would vanish)."""
    if version not in (3, 4, 5, 9):
        return None
    tables = ("users", "results", "sessions") + (("platoons",) if version in (5, 9) else ())
    return tuple(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables)


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
