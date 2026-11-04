# Deploy checklist: first image of the Baror App

**Date:** 2026-11-03 | **Checked by:** task T9 (Docker host: Docker 29.9.2, Compose 5.6.1, Linux x86_64) | **Deployer:** to be filled in

Legend: **[x]** done and verified with the result shown; **[ ]** not done; **[?]** could not be verified here, with the reason.
Nothing below is assumed.

## Pre-deploy

- [x] Build from a clean checkout: `docker build` succeeded. First build **68 s** (pulling the base image, hash-checked install of
  the 35 locked packages); rebuild with a warm cache 2 s. Image **905 MB** on disk (200 MB compressed content).
- [x] Base image pinned by tag **and** digest (`python:3.12-slim-bookworm@sha256:e697c818…d337`, resolved 2026-11-03). SQLite in it: 3.40.1.
- [x] Dependencies hash-checked: `pip install --require-hashes --only-binary=:all: --no-deps -r requirements.lock` ran inside the build.
  The lock was also regenerated from `requirements.txt` with `tools/make_lock.py`: same 35 `name==version` pairs.
- [x] Unit tests, host Python 3.12 (SQLite 3.45.1): **all green**, 4 skipped (the root-only ones). Exit code checked, not only a filtered summary.
- [x] Unit tests inside the image of the final tree (repository mounted read-only, as root, Python 3.12, SQLite 3.40.1): **604 tests OK, exit 0, 7 skipped** (3 tests that need a git checkout, 3 that are for a non-root user, and the git-ignore check). The 4 root-only tests ran and passed.
- [x] **Found by the run above and fixed in schema v5 (task T1b):** SQLite 3.40.1 does not calendar-validate `date()` (`date('2026-02-31')` returns `2026-02-31`; 3.45.1 returns `2026-03-03`), so the old `CHECK(date(x)=x)` accepted impossible dates in the image. The check is now `date(julianday(x)) = x`; the new migration tests pass inside the image, i.e. on 3.40.1.
- [x] The 4 tests that skip without root (`tests.test_concurrency.VolumePermissions`) **pass as root inside the container** (4 of 4).
- [x] No secrets or data in the image: `find /app -name '*.db' -o -name 'users.json*' -o -name '.env*' -o -name '*.md' -o -name tests` found nothing.
  Files in `/app`: `app.py`, `logo.png`, `image_bea22f.jpg`, `baror/` (with `domain/thresholds.json`), `scripts/`, `.streamlit/`.
- [x] Time zone data present: `ZoneInfo('Asia/Jerusalem')` works in the image (tzdata comes from the lock file).
- [x] Database migrations tested: schema v1 to v4 with data kept, concurrent upgrades, rollback on failure (tests `test_migration_v2/v3/v4`).
- [ ] Owner confirms the GitHub Actions commit SHAs listed in `docker-publish.yml`, then the tags are replaced by SHAs (not done on purpose).
- [ ] Owner decides what cadets are told about instructor and chief access; until then `BAROR_COHORT_VIEWS` stays `0`.
- [ ] Privacy officer reviews `docs/privacy-notice-he.md`; export and delete screens do not exist yet (the notice must not be shown before).
- [x] `scripts/healthcheck.py` (task T8) is merged: the final image reports `healthy` (docker health status and `/_stcore/health` 200, run read-only as uid 10001 on a named volume).
- [x] Database files are created `0600` (the start command sets `umask 077`; before, SQLite created them `0644`). Verified in the final image.
  (Verified here with the research pack's version of that file, which was **not** committed with T9.)
- [?] Code review by a person: automated review only (no high or critical findings, see the commit message).

## Container checks (all run against the built image, uid 10001, `--read-only --tmpfs /tmp`)

- [x] Runs as uid 10001 (`id` prints `uid=10001(baror)`).
- [x] Fresh named volume: the container is **healthy with no visitor** (database created by `scripts/init_db.py` before the server starts;
  log line `database ready: schema v4`). `/_stcore/health` answers `ok` (HTTP 200).
  *Found and fixed during this task:* before `init_db.py` existed the database was created only when the first browser opened the
  site, so a fresh deployment was `unhealthy` ("db missing") until then.
- [x] `/data/baror.db` is owned by `baror` (10001).
- [x] The sign-in page renders in a real browser (headless Chrome) from the container; no error in the logs.
- [x] Healthcheck command inside the container: healthy = exit 0; corrupt database file = `UNHEALTHY db: file is not a database`, exit 1;
  missing database = `UNHEALTHY db: db missing`, exit 1; nothing listening on the port = `UNHEALTHY http: ... Connection refused`, exit 1.
- [x] Bind mount the container user cannot write to: `ERROR E-STORAGE: connect: unable to open database file`, exit 1, one line, no traceback.
- [x] Corrupt database file in the volume: `ERROR E-CORRUPT: init_db: file is not a database`, exit 1; the file is byte-identical afterwards (same md5).
- [x] Named volume works (data directory writable by 10001, WAL files created next to the database).
- [x] `docker compose config` is valid; `docker compose up -d --build` (unique project name) starts a container that turns `healthy`.
  Verified on the running compose container: read-only root (`touch /app/x` fails), `/data` and `/tmp` writable, `CapDrop=[ALL]`,
  `no-new-privileges:true`, memory 512 MiB, 1 CPU, 256 pids, user 10001, port published only on `127.0.0.1:8501`, `HOME=/tmp`.
- [?] With `restart: unless-stopped`, a corrupt or unwritable data directory should make Docker restart the container repeatedly with the
  one-line error in the log each time (nothing is overwritten). Expected from the restart policy; the loop itself was not observed.
- [?] HTTPS, HSTS and the redirect of unknown multi-segment URLs: belong to the reverse proxy, which does not exist in this environment.
- [?] Real phones (iPhone Safari cookie lifetime, keyboard, autofill): not testable here.
- [?] Power loss and full disk on the volume: not tested.
- [?] Publishing to ghcr.io and the GitHub Actions run: cannot run here (no GitHub). The workflow is covered by text checks only.
- [?] CVE scan of the 35 packages and of the base image: not run.
- [?] `Streamlit` logs a warning at start: static file serving is enabled in `.streamlit/config.toml` but there is no `/app/static`
  folder yet (harmless; the design phase adds the folder or the setting is turned off).

## Deploy

- [ ] **Schema v9 (in progress, 2026-11-04): back up first** (`scripts/backup.py`), then follow runbook section 10 (v8 to v9: one platoon per instructor, `allowed_emails` dropped, grants dropped and audited as `migrate.v9_grant_dropped`). List the instructors per platoon beforehand.
- [ ] **Still open: run the v9 migration on a copy of the database inside the Docker image (SQLite 3.40.1).** The `ADD COLUMN ... CHECK` statements were only tested with the developer machine's SQLite; `docker compose logs baror` must start with `database ready: schema v9`.
- [ ] Create `baror.env` on the host if any setting differs from the defaults (see README, environment variables). New in v9: `BAROR_SIGNUP_CAP_PER_HOUR` (default 120), `BAROR_REGISTRATION_OPEN` (default 1), `BAROR_JOIN_LINK_SECRET` (32+ characters, unset = join links off), `BAROR_PUBLIC_URL`.
- [ ] `docker compose up -d --build`; wait for `healthy` in `docker compose ps`.
- [ ] Create the **two admins** (runbook, section 1): each registers in the app like a cadet, then `admin_users.py set-role EMAIL admin` (cap 2, operator script only). There is no allowlist any more; registration is open, so decide before opening the address whether `BAROR_REGISTRATION_OPEN` should start at `0`.
- [ ] Grant the staff roles afterwards from the app, with the out-of-band identity check (runbook, section 3.1). Set titles (מ"פ, סמ"פ, מ"מ, מ"כ).
- [ ] Owner confirms the registration limits (60/h per IP, 600/h app-wide, 120 sign-ups/h) and ADR-0004 (status Proposed).
- [ ] Put the HTTPS reverse proxy in front; confirm the cookie gets `Secure` and sign-in survives a page reload.
- [ ] Schedule: daily `scripts/backup.py` and `scripts/retention.py` from the host; copy backups off the machine.
- [ ] Set `course-end` for every platoon (`admin_users.py course-end`), then `scripts/retention.py --dry-run` once.

## Post-deploy

- [ ] Sign in as a test account, enter a result, sign out, reload (expect the sign-in page).
- [ ] Check `docker compose logs baror` for errors for 15 minutes.
- [ ] Delete any leftover `users.json`, `users.json.premigration` and `baror.db.before-restore` after verifying the data.
- [ ] Announce to cadets only after the privacy notice (including the proposed open-registration paragraph) is approved and the export/delete screens exist.

## Rollback

- Keep the previous image tag (`ghcr.io/<owner>/<repo>:<sha>`); roll back with `docker compose` pointing at that tag.
- A schema upgrade is one way: restore the pre-upgrade backup (README, "Backups and restore") together with the previous image.
  Take a backup immediately before every deploy that changes `LATEST_VERSION` in `baror/storage/db.py`.
- Roll back when: the container stays `unhealthy` for more than 3 minutes after start, sign-in fails for everybody, or the log shows
  `E-CORRUPT` or `E-STORAGE`.
