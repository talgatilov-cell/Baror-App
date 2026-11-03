# Operator runbook

For the person who runs the server. You need shell access to the host. Every command below assumes Docker Compose from
the repository folder; replace `docker compose exec baror` by nothing if you run the scripts directly on a machine that has
the dependencies, and always pass `--db /data/baror.db` (or the path of your database). Commands marked **(audited)**
write a row to the audit log with no actor (operator).

## 0. Where things are

| What | Where |
|---|---|
| Database (SQLite, WAL) | volume `baror_data`, file `/data/baror.db` (plus `-wal` and `-shm` while running) |
| Backups | `/data/backups/baror-YYYYMMDD.db` (mode 0600) |
| Settings and secrets | `./baror.env` next to `docker-compose.yml`, never committed |
| Logs | `docker compose logs baror` (json-file, 5 x 10 MB) |
| Health | `docker compose ps` shows `healthy` or `unhealthy`; `docker compose exec baror python scripts/healthcheck.py` |

## 1. First start and the first admin

1. `docker compose up -d --build`. The container creates the database before the server starts; `docker compose logs baror`
   starts with `database ready: schema vN`.
2. A person registers through the app only if their e-mail is on the allowlist (section 2). So: add your own address,
   register in the browser, then make that account admin:

       docker compose exec baror python scripts/admin_users.py --db /data/baror.db set-role you@example.com admin   # (audited)

3. Keep `BAROR_COHORT_VIEWS` at `0` until the owner has approved what cadets are told about instructor and chief access.

## 2. Add cadets (the allowlist)

Registration is closed to anyone not on the list. The refusal message is the same for every reason, so a person cannot
find out whether an address is listed.

    python scripts/allowlist.py --db /data/baror.db add dana@example.com --platoon "Platoon A"
    python scripts/allowlist.py --db /data/baror.db import people.csv --dry-run          # then again without --dry-run
    python scripts/allowlist.py --db /data/baror.db list --status pending

CSV: UTF-8, header `email[,role][,platoon]`, platoon is a name (create platoons first with
`admin_users.py platoon-add NAME`, or `import --create-platoons`). Re-running does nothing the second time. Exit code 1
means a row was rejected: read the line numbers and reason codes it prints.

**Known weakness:** there is no e-mail verification. Anyone who knows a listed address can register it before its owner.
When a cadet reports that their address "is already taken", look at `admin_users.py list`, deactivate the account that is
not theirs, and tell the owner (there is no delete command yet).

## 3. Roles, platoons, instructors, chief

    admin_users.py --db DB platoon-add "Platoon A"                    # (audited)
    admin_users.py --db DB assign dana@example.com "Platoon A"        # '-' instead of a name clears        (audited)
    admin_users.py --db DB set-role ins@example.com instructor        # (audited)
    admin_users.py --db DB grant ins@example.com "Platoon A"          # instructor may READ that platoon  (audited)
    admin_users.py --db DB revoke ins@example.com "Platoon A"
    admin_users.py --db DB set-role boss@example.com chief            # chief or deputy chief: all platoons, aggregates (audited)
    admin_users.py --db DB list

Instructors see their granted platoons only; the chief sees aggregates (groups under 5 are hidden) and may open a named
cadet, which is audited. Changing a role takes effect on the user's next click. The screens only appear when
`BAROR_COHORT_VIEWS=1`.

## 4. Reset a password / locked-out cadet

There is **no reset command yet**. Procedure (uses the application's own functions; the temporary password must be at least
8 characters; it will be asked for without echo):

    docker compose exec baror python -c "
    import getpass
    from baror.auth import passwords
    from baror.storage import repo
    db = '/data/baror.db'
    rec = repo.get_login_record(db, 'dana@example.com')
    repo.set_password_hash(db, rec['id'], passwords.hash_password(getpass.getpass('temporary password: ')), must_change_pw=True)
    "

This revokes all of the account's sessions, clears the failed-attempt counter and forces a new password at the next sign-in.
Hand the temporary password over in person, not in a group chat.

A cadet who typed the password wrongly many times is only slowed down (2 s after the fifth failure, doubling, at most 5
minutes) and the delay ends by itself. Resetting the password also clears it.

## 5. Deactivate an account

    admin_users.py --db DB deactivate dana@example.com      # revokes every session; sign-in is refused   (audited)
    admin_users.py --db DB reactivate dana@example.com

Removing an address from the allowlist never touches an existing account.

## 6. Backup and restore

See README, "Backups and restore". Daily from the host's scheduler; copy off the machine. To restore: stop the app, restore,
start. After a restore the previous file is `baror.db.before-restore`: it holds everybody's identity link, so delete it once
you no longer need it.

## 7. Retention (6 months after the course)

1. Set each platoon's course end once: `admin_users.py --db DB course-end "Platoon A" 2026-12-31`. It prints the deadline and
   warns if it is already past. **Setting a date in the past makes the next job run de-identify that platoon for good.**
2. Daily: `scripts/retention.py --db DB --backup-dir /data/backups`. First, after any date change:
   `--dry-run` (prints how many cadets are due and writes nothing).
3. If the job exits with an error about the write-ahead log or a reader, the deletion already happened and the cleanup is
   owed: run it again; it retries the cleanup even when nothing is due.

**The job cannot reach:** copies you made outside the data volume (host snapshots, off-host backups, disk images),
`/data/baror.db.before-restore`, `users.json` / `users.json.premigration` from the migration, anything pasted into chats or
e-mail. Cadets with no platoon, or in a platoon without a course end, are never deleted automatically (the report counts them
as `unscheduled`). The audit log is not touched by the job; its own purge age is 365 days and has not been confirmed by the owner.

## 8. Incidents

| Symptom | What to do |
|---|---|
| Container `unhealthy` | `docker compose exec baror python scripts/healthcheck.py` prints one line saying which check failed: the web server (look at `docker compose logs baror`) or the database (file missing, corrupt or locked). |
| Container restarts at once, log says `ERROR E-STORAGE ... unable to open database file` | `/data` is not writable by uid 10001. A named volume is correct by default; a bind mount must be `chown 10001:10001`. |
| Log says `ERROR E-CORRUPT ... file is not a database` | The database file is damaged. The app refuses to start and has not changed the file. Stop, keep a copy of the damaged file, restore the newest good backup (README). |
| Cadets are signed out after a server restart | Expected only if the browser lost the cookie; sessions live in the database and survive restarts. Check that the site is served over HTTPS (the cookie is `Secure` there). |
| Nobody can sign in for a while, log shows `proxy_untrusted` | A reverse proxy is in front but `TRUSTED_PROXIES` is empty, so all cadets share one address and the shared limit was used up. Set `TRUSTED_PROXIES` to the proxy address and restart. |
| Blank white page on a mistyped URL | The reverse proxy must redirect paths with two or more segments to `/` (README, "Reverse proxy"). |
| A cadet says someone else registered their address | Section 2, "Known weakness". |
| Suspected leaked session cookie | `deactivate` then `reactivate` the account (revokes all sessions), and reset the password (section 4). |

## 9. Aggregated reports (weekly and monthly) and their e-mail

Schema v8 adds the tables `reports`, `report_deliveries` and `report_settings`. **Back up first** (`scripts/backup.py`) before the first start on a real database.

**What is made, and when (Israel time, weekend = Friday and Saturday).**
- *Weekly*, every Thursday: Friday up to and including that Thursday (periods touch, no gaps).
- *Monthly*, on the last day of the month when it is Sunday to Thursday; when the last day is a Friday or Saturday, on the Thursday before it. Period: the 1st up to that day; the PDF says "data up to <date>". A Thursday that is also the month end makes both.
- **Known gap (owner's rule, flagged):** results dated on the Friday/Saturday after a month's generation date are in no monthly report (the next one starts on the 1st). They are in the next weekly report. If the owner wants them in the next monthly report, change `monthly_period` in `baror/domain/reports.py` (start = day after the previous report's end).
- Content: aggregates only, no names; groups under 5 cadets are not shown; each cadet's latest result in the period per test; change vs the previous period only when both groups have at least 5. Known and accepted: a reader can subtract "cadets" and "tested" of a shown platoon to get the number who did not test. Reports are snapshots: late entries are not added afterwards.

**Run it daily** (cron on the host, or a systemd timer). It exits 0 when nothing is due; running it twice is harmless (a stored report is never made again):

    15 6 * * *  cd /srv/baror && docker compose exec -T baror python scripts/generate_reports.py --db /data/baror.db >> /var/log/baror-reports.log 2>&1

- Missed days: `scripts/generate_reports.py --db DB --since 2026-10-29` makes whatever was due on every day since then (up to 400 days) and mails nothing that is older than the switch or than 14 days. `--date YYYY-MM-DD` runs as if it were that day. `--dry-run` writes and sends nothing and prints what is due and the switch state.
- To make a report again (for example after correcting results): `sqlite3 DB "DELETE FROM reports WHERE period_type='weekly' AND period_start='2026-10-30'"`, then run the script with `--date` of that Thursday.
- Exit codes: 0 ok (also "mail not configured" or "auto-send off") | 1 error (database, schema older than v8) | 2 reports stored, switch on, mail settings invalid.
- The chief sees and downloads the reports on the chief screen, section "דוחות" (each download is audited).

**E-mail (OFF by default).** The chief switches "שליחה אוטומטית בדואר אלקטרוני" on the chief screen. Check or change the state:

    sqlite3 /data/baror.db "SELECT mail_enabled, changed_at, changed_by FROM report_settings"   # 0 = off

Prefer the screen (it is audited). An operator can switch it off in an emergency: `UPDATE report_settings SET mail_enabled=0`. A fresh database, a migration and `scripts/backup.py --restore` all leave it OFF. Turning it on sends only reports generated after that moment (never the backlog); unsent or failed ones are retried for 14 days; one mail per report and active chief-role account (chief and deputy chief). Environment variables, for BOTH the job and the app container (the app only uses them to show the "not configured" warning), never committed:

    BAROR_SMTP_HOST  BAROR_SMTP_PORT (587 starttls / 465 ssl)  BAROR_SMTP_MODE (starttls|ssl)
    BAROR_SMTP_USER  BAROR_SMTP_PASSWORD  BAROR_SMTP_FROM

TLS with certificate verification is required (no plain mode); a remote host needs a login. Logs hold counts and short codes only (`smtp_auth`, `tls`, `network`, `timeout`, `smtp_refused`, `smtp_error`), also in `report_deliveries.last_error`. Do not run two copies of the job at once. E-mail is not end-to-end encrypted: see the proposed paragraph in `docs/privacy-notice-he.md`.
