# Baror App

A Hebrew (right-to-left), phone-first Streamlit app for officer-course cadets: log 3 km run (or 6 km walk) times and
push-ups, see progress, read tips. Instructors (own platoon) and the chief / deputy chief (all platoons, aggregates)
have read-only screens that are **off by default**. Data lives in one SQLite file. Runtime packages: `streamlit`,
`pandas`, `plotly` only; tests use `unittest` and `streamlit.testing.v1.AppTest` only.

Operators: see [docs/runbook.md](docs/runbook.md). Decisions: [docs/adr/](docs/adr/). Release check:
[docs/deploy-checklist.md](docs/deploy-checklist.md). Draft privacy notice for cadets (Hebrew, not approved):
[docs/privacy-notice-he.md](docs/privacy-notice-he.md).

## Run locally

    python -m venv .venv && . .venv/bin/activate
    pip install --require-hashes --only-binary=:all: --no-deps -r requirements.lock
    DB_PATH=./data/baror.db python scripts/init_db.py      # creates the folder's database (the folder must exist)
    DB_PATH=./data/baror.db streamlit run app.py

## Run the tests

    python -m unittest discover -s tests -t .

Four tests need root (they drop to uid 10001 to check volume permissions) and skip otherwise; the Docker section shows
how to run them. `tests/regression/` is an opt-in suite for the old JSON-based app and is not part of the command above.

## Run with Docker

    docker compose up -d --build        # http://127.0.0.1:8501, loopback only
    docker compose logs -f baror
    docker compose down                 # the named volume `baror_data` keeps the database

Needs Docker Compose 2.24 or newer (the optional `baror.env`). The image runs as uid 10001 with a read-only root filesystem; `/data` (volume) and `/tmp` (tmpfs) are the only writable
places. Before the server starts, `scripts/init_db.py` creates or upgrades the database and stops the container with a
one-line error if `/data` is not writable or the file is not a database (it never overwrites it). Settings and secrets
go in `./baror.env` (optional, never committed), one `KEY=value` per line.

Run the whole test suite, including the four root-only tests, inside the image (the repository is mounted read-only):

    docker build -t baror:test .
    docker run --rm --user 0 --tmpfs /tmp -v "$PWD":/src:ro -w /src baror:test python -m unittest discover -s tests -t .

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `DB_PATH` | `/data/baror.db` | SQLite file. Its directory must exist and be writable by the app user. |
| `SESSION_DAYS` | `14` | Lifetime of the "stay signed in" cookie, 1 to 90. |
| `TZ_NAME` | `Asia/Jerusalem` | Time zone for "today" (the date a result is entered). Needs the `tzdata` package (in the lock file). |
| `TRUSTED_PROXIES` | empty | Comma-separated **exact IP addresses** of the reverse proxies whose `X-Forwarded-For` header may be believed (no `*`, no ranges, no names). Empty means the header is ignored. **Set it when a proxy is in front** (the Docker bridge gateway for a host proxy): otherwise every cadet shares the proxy's address, one outsider can use up the shared sign-in limit for everybody, and the log shows a `proxy_untrusted` warning. |
| `FORWARDED_ALLOW_IPS` | unset | Streamlit/uvicorn's own setting. Leave it unset. The app **refuses to start** if it contains `*`. |
| `BAROR_BOT_MIN_SECONDS` | `2` | Minimum seconds between opening the sign-in or registration form and submitting it (bot check), 0 to 60. |
| `LOG_LEVEL`, `LOG_FORMAT`, `LOG_SALT` | `INFO`, `json`, random per start | Log level, `json` or `text`, and the secret used to turn e-mail addresses into short tokens in the log. Set `LOG_SALT` to a fixed secret, or the tokens change at every restart. |
| `BAROR_COHORT_VIEWS` | `0` | `1` shows the instructor and chief screens. Keep `0` until the owner has approved what cadets are told about those views. |

Invalid values stop the start-up with a readable message. `FORWARDED_ALLOW_IPS` must **never** be `*`: with it a client can
claim any address. Address limits (generous, a whole platoon may share one public address): 60 failed sign-ins per 10
minutes and 100 registration attempts per hour per address, 8 failures per (address, account) per 10 minutes.

## Backups and restore

    python scripts/backup.py --db /data/baror.db --dir /data/backups --keep 14

Uses SQLite's online backup, so it is safe while the app runs. Writes `baror-YYYYMMDD.db` (mode 0600; a second run on
the same day writes `-HHMMSS`), verifies the copy, keeps the newest `--keep` files. In Docker:

    docker compose exec baror python scripts/backup.py --db /data/baror.db --dir /data/backups

Schedule it from the host (cron or a systemd timer) once a day. Copy backups off the machine yourself. They hold
password hashes and performance data.

**Restore (stop the app first):**

    docker compose stop baror
    docker compose run --rm --no-deps baror python scripts/backup.py --restore /data/backups/baror-20261102.db --db /data/baror.db
    docker compose start baror

The current database is kept as `baror.db.before-restore` (it still holds the identity link of everybody: delete it when you
no longer need it). A backup that fails its integrity check is refused.

## Retention (privacy)

Six calendar months after a platoon's course ends, the job de-identifies its cadets: name, e-mail, password hash and the
link to their results are deleted; the results stay under a random pseudonym. Set the course end once per platoon:

    python scripts/admin_users.py --db /data/baror.db course-end "Platoon A" 2026-12-31

Run daily from the host's scheduler, always `--dry-run` first after changing a date (de-identification cannot be undone):

    docker compose exec baror python scripts/retention.py --db /data/baror.db --backup-dir /data/backups --dry-run
    docker compose exec baror python scripts/retention.py --db /data/baror.db --backup-dir /data/backups

It also deletes backups older than six months and every backup that still holds a deleted link, then writes a fresh one.
Output is counts only. **Not reachable by the job:** copies outside the data volume (host snapshots, off-host backups),
`baror.db.before-restore`, `users.json.premigration`, and the audit log (its own 365-day purge is a number the owner has
not confirmed). Cadets without a platoon or whose platoon has no course end are never deleted automatically.
See [ADR-0002](docs/adr/0002-pseudonymized-retention.md).

## Operator commands

All take `--db /data/baror.db` (or the `BAROR_DB` variable). In Docker prefix them with `docker compose exec baror`.

| Script | Purpose |
|---|---|
| `scripts/allowlist.py add EMAIL [--role cadet\|instructor] [--platoon NAME]`, `remove`, `list [--status pending\|used]`, `import FILE.csv [--dry-run] [--strict] [--create-platoons] [--delimiter ';']` | Who may register. CSV header `email[,role][,platoon]`. Removing an entry never deletes an account. |
| `scripts/admin_users.py list`, `set-role EMAIL cadet\|instructor\|admin\|chief`, `deactivate EMAIL`, `reactivate EMAIL`, `platoon-add NAME`, `assign EMAIL PLATOON\|-`, `grant EMAIL PLATOON`, `revoke EMAIL PLATOON`, `course-end PLATOON YYYY-MM-DD\|-` | Roles, platoons, instructor grants, retention clock. Every change is audited. The first admin is made with `set-role`. |
| `scripts/backup.py`, `scripts/retention.py`, `scripts/init_db.py` | See above. |
| `scripts/migrate_json_to_sqlite.py --json users.json --db /data/baror.db [--rejects-dir DIR]` | One-time import from the old JSON app. |

## Migrating from the old `users.json`

    python scripts/migrate_json_to_sqlite.py --json /path/users.json --db /data/baror.db

Idempotent, never modifies the original. Passwords are hashed and every migrated account must change its password at the
next sign-in. Exit code 2 means some records were rejected: read `migration_rejects.json` (mode 0600, passwords redacted).
It leaves `users.json.premigration`, a read-only copy **with the old plaintext passwords**: delete it, and the original
`users.json`, once the import is verified.

## Dependencies

`requirements.txt` holds the three direct pins; `requirements.lock` holds every package with its hash and is what the
image and CI install (`--require-hashes`). Dependabot opens update pull requests for `requirements.txt`, the Docker base
image (tag and digest) and the GitHub Actions, weekly. It does **not** update the lock file. After merging a pip update,
regenerate it on the image platform and check it installs:

    pip install --dry-run --ignore-installed --quiet --report linux-x86_64.json --only-binary=:all: \
        --platform manylinux_2_28_x86_64 --platform manylinux_2_27_x86_64 --platform manylinux_2_24_x86_64 \
        --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --python-version 3.12 \
        --target /tmp/lock-target -r requirements.txt
    python tools/make_lock.py requirements.lock requirements.txt linux-x86_64.json      # stdlib only; pass more reports to add targets
    pip install --require-hashes --only-binary=:all: --no-deps -r requirements.lock      # in a clean venv, must succeed

Do not use `--platform manylinux2014_x86_64` alone: pandas 3 has no wheel for it, so pip finds no matching version.
Check the result with `docker build .` before merging. Do not add a package without asking the owner.

## Reverse proxy

The app speaks plain HTTP on `127.0.0.1:8501`. Put an HTTPS proxy in front (the session cookie is a bearer token and is
`Secure` only over HTTPS) with HSTS. Streamlit shows a blank page for an unknown URL with two or more segments
(`/nope/deeper`): make the proxy redirect everything that is not `/` or Streamlit's own paths (`/_stcore/`, `/app/`) to `/`.
Pass the real client address in `X-Forwarded-For`; the per-IP limit task will say which proxy addresses to trust.

## CI

`.github/workflows/docker-publish.yml`: tests first (hash-checked install, `unittest`), then the image is built and pushed
to ghcr.io only from `main` or a `v*` tag. No `pull_request` trigger. The actions are still on version tags: the owner must
confirm the candidate commit SHAs listed in the workflow, then pin them.
