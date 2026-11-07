# syntax=docker/dockerfile:1
#
# Base image: tag AND digest. The tag says what we intend, the digest makes the build reproducible.
# Dependabot (package-ecosystem "docker", see .github/dependabot.yml) opens a pull request that bumps tag and
# digest together; read it, run the tests, then merge. To refresh by hand:
#     docker pull python:3.12-slim-bookworm && docker image inspect python:3.12-slim-bookworm   # see RepoDigests
# Digest below resolved on 2026-11-03.
FROM python:3.15-slim-bookworm@sha256:4e03260adeeebafccd084f3584e6b84823aba5651cfb53ef8a078ed441ddf9cd AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.lock .
# Hash-checked, wheels only, no dependency resolution: exactly the packages of the lock file (tzdata included).
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --only-binary=:all: --no-deps -r requirements.lock

FROM python:3.15-slim-bookworm@sha256:4e03260adeeebafccd084f3584e6b84823aba5651cfb53ef8a078ed441ddf9cd AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    DB_PATH=/data/baror.db \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
# HOME=/tmp: the root filesystem is read-only in compose and /tmp is a tmpfs there. /data is the only persistent,
# writable place (SQLite WAL creates -wal and -shm next to the database, so the directory itself must be writable).
RUN groupadd --gid 10001 baror \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin baror \
 && mkdir -p /data /app \
 && chown 10001:10001 /data
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
# The REAL layout (static/ holds the stylesheet, the logo and the self-hosted font). scripts/healthcheck.py (T8) is what the HEALTHCHECK below runs.
COPY --chown=10001:10001 app.py logo.png image_bea22f.jpg ./
COPY --chown=10001:10001 baror/ ./baror/
COPY --chown=10001:10001 static/ ./static/
COPY --chown=10001:10001 scripts/ ./scripts/
COPY --chown=10001:10001 .streamlit/ ./.streamlit/
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=8s --start-period=30s --retries=3 \
  CMD ["python", "/app/scripts/healthcheck.py"]
# init_db.py first: the database exists (schema current) before the first health check, and an unusable /data stops the
# container with one readable line. `exec` makes Streamlit PID 1's replacement so it receives the stop signal.
# umask 077: the database, its -wal/-shm files and anything else the app creates are readable by uid 10001 only (they hold
# password hashes and personal data); SQLite would otherwise create them 0644.
CMD ["sh", "-c", "umask 077 && python scripts/init_db.py && exec streamlit run app.py"]
