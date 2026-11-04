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
2. Registration is open (section 2), so: register yourself in the browser like any cadet, then make that account admin with
   the operator script. There are **exactly two admins** (the cap is enforced; a third `set-role ... admin` is refused).
   Do the same for the second admin, who registers first on their own device:

       docker compose exec baror python scripts/admin_users.py --db /data/baror.db set-role you@example.com admin   # (audited)

   Admins can only be created this way, never from the app and never from an environment variable. A third admin is
   refused (`refused: ADMIN_LIMIT`, exit code 1). *(Verified 2026-11-04 on a scratch copy of the demo database.)*

3. Keep `BAROR_COHORT_VIEWS` at `0` until the owner has approved what cadets are told about instructor and chief access.
4. Set the v9 environment variables in `baror.env` (section 2.1). In particular decide on `BAROR_PUBLIC_URL` and, if join
   links are wanted, `BAROR_JOIN_LINK_SECRET`.

## 2. Open registration (there is no allowlist any more)

Since schema v9 **anyone can register**; every new account is a **cadet** and picks a platoon in the form ("not known yet" is
allowed). The `allowed_emails` table and its operator script no longer exist. Staff roles are never pre-listed or
self-claimed: an admin or a chief grants a role later, to an account that already exists (section 3). The refusal message
is the same for every reason.

### 2.1 Environment variables (put them in `baror.env`)

| Variable | Default | Meaning |
|---|---|---|
| `BAROR_REGISTRATION_OPEN` | `1` | Kill switch. `0` closes self-registration (the form says so; sign-in is not affected). Read at start-up: **restart the container** after changing it. Only `0` or `1` are accepted: any other value stops the start-up with a readable message. Use it during an attack or when the course is full. |
| `BAROR_SIGNUP_CAP_PER_HOUR` | `120` | Hard cap on new accounts per hour for the whole app, 1 to 1000. A missing, non-numeric or out-of-range value falls back to 120 (a typo never switches the cap off). Counted in the database table `signup_events` (survives account deletion). **When it is reached, real cadets are also refused until the hour passes.** |
| `BAROR_JOIN_LINK_SECRET` | unset | Signing key for join links, 32 to 512 characters without spaces, random. **Unset = join links are off.** A secret that is too short or has spaces stops the start-up. Treat it like a password: never commit it, never reuse it. Changing it invalidates every issued link. |
| `BAROR_PUBLIC_URL` | unset | Optional public address of the app, **https only**, host name only (for example `https://baror.example.org`: no path, query, user name or trailing path). It is used **only to display a complete join link**; unset = the screen shows just the `?join=...` part, which the user appends to the address. An invalid value stops the start-up. |

Rate limits that stay in force and are not configurable: honeypot field, signed one-time form token and minimum fill time
(`BAROR_BOT_MIN_SECONDS`), **60 registration attempts per hour per IP** (was 100), **600 attempts per hour for the whole app**
(checked before the password hash is computed), failed-login back-off. If many cadets share one public address (a base
Wi-Fi), the per-IP limit can refuse real cadets: raise `TRUSTED_PROXIES` correctly first (section 8) before suspecting the limit.
The numbers are proposals and still need the owner's confirmation (`docs/SESSION_NEXT_STEPS.md`, section 8.7).

### 2.2 Join links

A join link only **pre-selects a platoon** in the registration form and grants nothing. Instructors create links for their own
platoon from the app, an admin or a chief for any platoon. Validity is 14 days by default, 30 at most. To cancel every link of
one platoon at once, an admin or a chief uses "rotate links" for that platoon (this raises the platoon's `join_epoch`). Changing
`BAROR_JOIN_LINK_SECRET` cancels all links of all platoons. Links are not secret documents: assume anybody who sees one can
open the form, which is why the sign-up cap and the rate limits exist.

### 2.3 Known weakness: no e-mail verification

There is no SMTP, so nothing proves that a person owns the address they typed. Anyone can register somebody else's address
before its owner does (squatting). Consequences and what to do:

- The real owner sees that the address is taken. The account is a cadet account with no special power. The fix is
  `release-email` (below): the address is replaced by a meaningless one, the account is deactivated and signed out, its results stay (unlinked from the address), and the real owner can register.
- The risk that matters is a squatted address that later gets a staff role. That is why **no role is granted without an out-of-band identity check** (section 3.1).
- Small groups: with open registration someone can pad a platoon with fake accounts so that a group of fewer than 5 real cadets
  reaches 5 and is shown. Staff can move or remove suspicious accounts from the platoon; watch the account table for new
  sign-ups you do not recognise.

    admin_users.py --db DB release-email dana@example.com --as admin@example.com    # (audited)

`--as` names the **active admin** the release is attributed to in the audit log (required: the release is an admin action).
Only a cadet's address can be released (`refused: only a cadet e-mail can be released` for staff) and the named account must
be an active admin (`refused: people.release: role not allowed` otherwise). *(Verified 2026-11-04 on a scratch database.)*

## 3. Roles, platoons, instructors, chief

Structure: one company. **Chief** role (max 2 active): titles `mp` (מ"פ) and `smp` (סמ"פ), unique. **Instructor** role: per
platoon exactly one `mm` (מ"מ) plus up to 5 `cmdr` (מ"כ), each instructor in exactly one platoon; מ"מ and מ"כ have the same
permissions. **Admin**: exactly 2, operator script only. Cadets: unlimited. Roles and titles are granted from the app
(by an admin or a chief) except `admin`, which only this script can create.

    admin_users.py --db DB platoon-add "Platoon A"                                  # (audited)
    admin_users.py --db DB assign dana@example.com "Platoon A"                      # '-' instead of a name clears    (audited)
    admin_users.py --db DB set-role you@example.com admin                           # max 2; last admin cannot be removed
    admin_users.py --db DB set-role ins@example.com instructor --title mm --platoon "Platoon A"    # (audited)
    admin_users.py --db DB set-role boss@example.com chief --title mp               # mp or smp, max 2 chiefs (audited)
    admin_users.py --db DB set-title ins@example.com cmdr                           # change the title only (audited)
    admin_users.py --db DB grant ins@example.com "Platoon A"                        # moves the instructor to that platoon (their ONE platoon)
    admin_users.py --db DB list                                                     # id, role, title, status, platoon, e-mail; warnings on stderr

There is **no `revoke`** any more: an instructor always has exactly one platoon. Move them with `grant`, or make them a
cadet with `set-role EMAIL cadet`. `list` prints a `WARNING` line for legacy structure that exceeds the new limits (more than 2
admins or chiefs, two מ"מ, more than 5 מ"כים in a platoon, instructors without a platoon or without a title). The same limits
apply to the script as to the app (one מ"מ per platoon, 5 מ"כים, 2 chiefs, 2 admins). Refusals print `refused: <code>` and exit 1
(`ADMIN_LIMIT`, `MM_TAKEN`, `CMDR_LIMIT`, `CHIEF_LIMIT`, `TITLE_TAKEN`; a missing `--platoon` for an instructor prints
`an instructor needs an existing platoon`). Instructors see their one platoon; the chief sees aggregates (groups
under 5 are hidden) and may open a named cadet, which is audited. **Admins do not see cohort data at all**: they manage
permissions and cadet records (name, gender, כ.מ, platoon), never results. Changing a role takes effect on the user's next click.
The screens only appear when `BAROR_COHORT_VIEWS=1`.

*(The `--title`, `--platoon`, `set-title` forms, the admin cap, the chief and מ"מ limits, `grant` and `release-email` were run
on a scratch copy of the demo database on 2026-11-04 and behave as written. The script's `--help` output is the reference.)*

### 3.1 Granting a staff role (procedure for the person who grants)

A role is granted to an account that has already registered. Because the address is not verified (section 2.3), the identity
of the account holder is checked **outside the system**:

1. Have the person present (in person or on a video call you recognise them on), and ask them to register in the app on
   their own device if they have not.
2. Open the accounts table in the app (admin or chief), search by e-mail or name, select the account.
3. Ask the person to sign in on their own device, open More, then Profile, and **read out the name and the registration
   time ("מועד ההרשמה") shown there**. Compare with what the grant dialog shows (name, e-mail, registration time; both use the
   same format and the course's time zone, for example `04/11/2026 11:30`). If they do not match, do not grant: someone else
   registered that address. Use `release-email` (admin) and have the person register again.
4. Tick "הזהות אומתה מחוץ למערכת" and confirm. If the account changed in the meantime the grant is refused (`ACCOUNT_CHANGED`);
   start again from step 2.
5. מ"כים are nominated by the מ"מ of the platoon from the app (only a registered, active cadet account can be nominated);
   an admin or a chief approves with the same identity check (steps 3 and 4).

Who may grant what: `chief` role, admin only. `instructor` role, admin or chief. Demotion of an instructor to cadet, admin or
chief; of a chief, admin only. Nobody changes their own role, and nobody can grant `admin` from the app.

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

A deactivated account keeps its e-mail address (the address stays taken). To give an address back to its real owner use
`release-email` (section 2.3, cadet accounts only). A reactivation is refused if the account holds a chief title that
another active chief now has.

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
| A cadet says someone else registered their address | Section 2.3: check the account, then `release-email` (admin). |
| Real cadets are refused at sign-up with a "try later" style message | The hourly sign-up cap (`BAROR_SIGNUP_CAP_PER_HOUR`) or the app-wide limit was reached: wait for the hour, check the account table for a flood of fake sign-ups, and close registration with `BAROR_REGISTRATION_OPEN=0` if it is an attack. |
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

## 10. Upgrading from v8 to v9 (open registration, one platoon per instructor)

**Read this before the first start of the v9 code on a real database. The migration is one way.**

1. **Back up first**, and keep the file: `scripts/backup.py --db /data/baror.db --dir /data/backups`. Restoring is the only way back (README, "Backups and restore").
2. **List the instructors per platoon before upgrading**, so you know who will lose what (for example from `admin_users.py list`, or ask the owner). v9 allows one platoon per instructor:
   - an instructor with several platoon grants **keeps the earliest grant** (ties: the lowest platoon id); the others are dropped;
   - **admins lose all platoon grants** (admins no longer see cohort data);
   - before dropping, the migration writes one audit row `migrate.v9_grant_dropped` (no actor; user id and platoon id) per dropped grant, so the list can be recovered from the audit log;
   - `allowed_emails` is dropped. Unused allowlist entries and pre-assigned platoons for people who have not yet registered are lost: re-collect them as join links (section 2.2) or tell people to pick the platoon in the form;
   - existing chiefs and instructors get no title ("תואר לא הוגדר") until you set one (`set-title`, or in the app). Platoons that already exceed the new limits are left alone; limits apply to new grants.
3. After the first start check `docker compose logs baror` for `database ready: schema v9`.
4. Set the new environment variables (section 2.1), then create the two admins (section 1).
5. **Not yet verified:** the new `ADD COLUMN ... CHECK` statements were tested only on the developer machine's SQLite; the image uses SQLite 3.40.1 (no Docker on the dev machine). Run the migration on a **copy** of the database inside the image before the real upgrade.

## 11. Open items for the owner (v9)

Details in `docs/SESSION_NEXT_STEPS.md`, section 8.7. Short list: confirm 60 per hour per IP, 600 per hour app-wide, 120 sign-ups
per hour and the 14-day join-link validity (max 30); decide whether a duplicate e-mail may be revealed at sign-up (it is today,
through the "taken" hint); decide whether chiefs may also deactivate fake accounts (today only admin, through `release-email`);
confirm that a chief may search all registered e-mails; keep `BAROR_JOIN_LINK_SECRET` only on the server; admins only by the operator script.
