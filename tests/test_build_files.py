"""T9: Dockerfile, compose, workflow, dockerignore, docs and scripts/init_db.py. Static checks on the text (no YAML library
exists in the project); the real build and container checks are in docs/deploy-checklist.md."""
import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from baror import config

ROOT = Path(__file__).resolve().parent.parent


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def code_lines(text: str):
    """Lines without comments (a '#' starts a comment in Dockerfile, YAML and .dockerignore alike)."""
    return [l.split(" #")[0] for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


class Dockerfile(unittest.TestCase):
    def setUp(self):
        self.text = read("Dockerfile")
        self.lines = code_lines(self.text)

    def test_base_images_are_pinned_by_tag_and_digest_and_never_latest(self):
        froms = [l for l in self.lines if l.startswith("FROM ")]
        self.assertEqual(len(froms), 2)
        for l in froms:
            self.assertRegex(l, r"^FROM python:3\.12-slim-bookworm@sha256:[0-9a-f]{64} AS \w+$")
            self.assertNotIn(":latest", l)
        self.assertEqual(len({re.search(r"sha256:\w+", l).group(0) for l in froms}), 1, "one digest for both stages")

    def test_copy_sources_exist_and_no_wildcard_copy(self):
        for l in self.lines:
            if not l.startswith("COPY ") or "--from=" in l:
                continue
            parts = [p for p in l.split()[1:] if not p.startswith("--")]
            for src in parts[:-1]:
                self.assertNotIn(src, (".", "./", "*"), "copy an explicit list, never the whole context")
                self.assertTrue((ROOT / src.rstrip("/")).exists(), f"COPY source missing: {src}")

    def test_everything_the_app_needs_at_run_time_is_copied(self):
        copied = " ".join(l for l in self.lines if l.startswith("COPY "))
        for needed in ("app.py", "logo.png", "image_bea22f.jpg", "baror/", "scripts/", ".streamlit/", "requirements.lock"):
            self.assertIn(needed, copied, needed)
        self.assertTrue((ROOT / "baror" / "domain" / "thresholds.json").exists(), "shipped inside baror/")
        self.assertNotIn("static/", copied, "there is no static/ directory yet")

    def test_hash_checked_install_only(self):
        run = " ".join(self.lines)
        self.assertIn("pip install --require-hashes --only-binary=:all: --no-deps -r requirements.lock", run)
        self.assertNotIn("upgrade pip", run)

    def test_runs_as_uid_10001_with_a_healthcheck_and_initialises_the_database_first(self):
        self.assertIn("USER 10001:10001", self.lines)
        self.assertTrue([l for l in self.lines if l.startswith("HEALTHCHECK")])
        self.assertIn("scripts/healthcheck.py", " ".join(self.lines))
        cmd = [l for l in self.lines if l.startswith("CMD")][-1]
        self.assertIn("scripts/init_db.py", cmd)
        self.assertLess(cmd.index("init_db.py"), cmd.index("streamlit run app.py"))
        self.assertIn("exec streamlit", cmd)
        self.assertTrue((ROOT / "scripts" / "init_db.py").exists())

    def test_writable_home_and_db_path_and_no_secrets_in_env(self):
        env = " ".join(l for l in self.lines if l.startswith(("ENV", "    ")))
        self.assertIn("HOME=/tmp", env)
        self.assertIn("DB_PATH=/data/baror.db", env)
        for word in ("PASSWORD", "SECRET", "TOKEN", "KEY="):
            self.assertNotIn(word, env.upper().replace("PYTHONUNBUFFERED", ""))

    @unittest.skipUnless((ROOT / "scripts" / "healthcheck.py").exists(),
                         "scripts/healthcheck.py is provided by task T8; the HEALTHCHECK needs it")
    def test_the_healthcheck_script_exists(self):
        self.assertTrue((ROOT / "scripts" / "healthcheck.py").exists())

    def test_the_start_command_sets_a_private_umask_before_anything_creates_the_database(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cmd = [l for l in text.splitlines() if l.startswith("CMD ")][0]
        self.assertIn("umask 077", cmd)
        self.assertLess(cmd.index("umask 077"), cmd.index("init_db.py"))


class DockerIgnore(unittest.TestCase):
    def test_data_secrets_and_noise_are_excluded_but_the_images_are_not(self):
        lines = set(code_lines(read(".dockerignore")))
        for pattern in (".git", "tests", "docs", ".env", "*.env", "baror.env", "users.json", "*.db", "*.db-wal", "*.db-shm",
                        "__pycache__", ".venv", "backups", "data"):
            self.assertIn(pattern, lines, pattern)
        self.assertNotIn("*.jpg", lines, "the theme needs image_bea22f.jpg")
        self.assertNotIn("*.png", lines)


class Compose(unittest.TestCase):
    def setUp(self):
        self.text = read("docker-compose.yml")
        self.code = "\n".join(code_lines(self.text))

    def test_hardening(self):
        for needle in ("read_only: true", "cap_drop: [ALL]", "no-new-privileges:true", "mem_limit:", "cpus:", "pids_limit:",
                       "tmpfs:", "driver: json-file", "max-size:", "max-file:", "baror_data:/data"):
            self.assertIn(needle, self.code, needle)

    def test_published_on_loopback_only(self):
        ports = re.findall(r'-\s*"([^"]*:\d+:\d+)"', self.code)
        self.assertEqual(ports, ["127.0.0.1:8501:8501"])
        self.assertNotIn("0.0.0.0", self.code)

    def test_env_file_is_optional_and_no_secret_is_in_the_file(self):
        self.assertIn("path: ./baror.env", self.code)
        self.assertIn("required: false", self.code)
        env_block = self.code.split("environment:")[1].split("read_only")[0]
        self.assertEqual(re.findall(r"^\s+([A-Z_]+):", env_block, re.M), ["DB_PATH"])

    def test_forwarded_allow_ips_is_never_set_in_the_compose_file(self):
        self.assertNotIn("FORWARDED_ALLOW_IPS", self.code)           # not as a setting
        self.assertIn("NEVER set FORWARDED_ALLOW_IPS", self.text)    # but the warning is there
        self.assertIn("TRUSTED_PROXIES", self.text)


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = read(".github/workflows/docker-publish.yml")
        self.code = "\n".join(code_lines(self.text))

    def test_no_pull_request_trigger_and_default_permissions_are_empty(self):
        self.assertNotIn("pull_request", self.code)
        self.assertIn("permissions: {}", self.code)
        on = self.code.split("jobs:")[0]
        for trigger in ("push:", "workflow_dispatch:", "branches: [main]", 'tags: ["v*"]'):
            self.assertIn(trigger, on)

    def test_publish_needs_the_test_job_and_the_tests_run_with_the_locked_install(self):
        test_job, publish_job = self.code.split("  publish:")
        self.assertIn("python -m unittest discover -s tests -t .", test_job)
        self.assertIn("--require-hashes", test_job)
        self.assertIn("needs: test", publish_job)
        self.assertNotIn("packages: write", test_job)
        self.assertIn("packages: write", publish_job)
        self.assertNotIn("secrets.", test_job)

    def test_publish_only_from_main_or_a_version_tag(self):
        self.assertIn("github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')", self.code)

    def test_actions_are_pinned_to_a_tag_or_a_sha_never_to_a_branch(self):
        uses = re.findall(r"uses:\s*(\S+)", self.code)
        self.assertEqual(len(uses), 6)
        for u in uses:
            self.assertRegex(u, r"^[\w./-]+@(v\d+|[0-9a-f]{40})$", u)    # not main/master/latest
        if any(re.search(r"@[0-9a-f]{40}$", u) for u in uses):
            return                                                       # the owner has pinned by SHA: nothing left to confirm
        # still on tags: the candidate SHAs must be marked as unverified and waiting for the owner
        self.assertIn("OWNER TO CONFIRM", self.text)
        self.assertIn("NOT verified", self.text)

    def test_checkout_does_not_keep_the_token(self):
        self.assertEqual(self.code.count("persist-credentials: false"), self.code.count("actions/checkout@"))


class Dependabot(unittest.TestCase):
    def test_three_ecosystems_weekly(self):
        text = "\n".join(code_lines(read(".github/dependabot.yml")))
        for eco in ("pip", "docker", "github-actions"):
            self.assertIn(f"package-ecosystem: {eco}", text)
        self.assertEqual(text.count("interval: weekly"), 3)


class Docs(unittest.TestCase):
    def test_readme_mentions_every_environment_variable_the_app_reads(self):
        readme = read("README.md")
        for name in config.ENV_KEYS:
            self.assertIn(f"`{name}`", readme, name)
        self.assertIn("FORWARDED_ALLOW_IPS", readme)
        for script in ("backup.py", "retention.py", "allowlist.py", "admin_users.py", "migrate_json_to_sqlite.py", "init_db.py",
                       "make_lock.py", "--dry-run", "users.json.premigration", "HSTS"):
            self.assertIn(script, readme, script)

    def test_every_script_the_docs_name_exists(self):
        for doc in ("README.md", "docs/runbook.md"):
            for name in set(re.findall(r"scripts/(\w+\.py)", read(doc))):
                if name == "healthcheck.py":
                    continue          # written by task T8 (the Dockerfile test above skips visibly until it is merged)
                self.assertTrue((ROOT / "scripts" / name).exists(), f"{doc} names scripts/{name}")
        self.assertTrue((ROOT / "tools" / "make_lock.py").exists())

    def test_every_operator_subcommand_in_the_runbook_is_a_real_one(self):
        real = {"list", "set-role", "deactivate", "reactivate", "platoon-add", "assign", "grant", "revoke", "course-end"}
        for cmd in re.findall(r"admin_users\.py(?: --db \S+)? ([a-z-]+)", read("docs/runbook.md") + read("README.md")):
            self.assertIn(cmd, real, cmd)
        for cmd in re.findall(r"allowlist\.py(?: --db \S+)? ([a-z]+)", read("docs/runbook.md") + read("README.md")):
            self.assertIn(cmd, {"add", "remove", "list", "import"}, cmd)

    def test_privacy_notice_is_a_marked_draft_with_the_owner_placeholder_and_says_nothing_about_staff_access(self):
        text = read("docs/privacy-notice-he.md")
        note, notice = text.split("\n---\n", 1)
        self.assertIn("טיוטה", text.splitlines()[0])
        self.assertIn("לא נבנו", note)                                          # export/delete screens do not exist yet
        self.assertIn("[ממתין לאישור בעל הפרויקט והממונה על הפרטיות]", notice)
        for word in ("מדריכ", "מפקד", "סגן"):
            self.assertNotIn(word, notice, "what instructors and the chief see is an owner decision")
        self.assertNotRegex(notice, r"[א-ת]\/[א-ת]", "no slash gender forms")
        self.assertNotRegex(notice, "[\U0001F000-\U0001FAFF]")


class ToolsAreStdlib(unittest.TestCase):
    def test_tools_import_only_the_standard_library(self):
        for f in (ROOT / "tools").glob("*.py"):
            names = set()
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module.split(".")[0])
            self.assertEqual(names - set(sys.stdlib_module_names), set(), f.name)


class InitDb(unittest.TestCase):
    def run_script(self, db_path, **env):
        e = {**os.environ, "DB_PATH": str(db_path), **env}
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "init_db.py")], capture_output=True, text=True, env=e, cwd=ROOT)

    def test_creates_the_database_idempotently_at_the_latest_schema(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "baror.db"
            first, second = self.run_script(p), self.run_script(p)
            self.assertEqual((first.returncode, second.returncode), (0, 0), first.stderr + second.stderr)
            from baror.storage import db
            self.assertIn(f"schema v{db.LATEST_VERSION}", first.stdout)
            self.assertEqual(db.user_version(p), db.LATEST_VERSION)

    def test_a_file_that_is_not_a_database_stops_with_one_line_and_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "baror.db"
            p.write_bytes(b"this is not sqlite" * 50)
            before = p.read_bytes()
            r = self.run_script(p)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
            self.assertIn("E-CORRUPT", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(p.read_bytes(), before)

    def test_missing_directory_and_bad_settings_are_one_line_errors(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_script(Path(d) / "no" / "such" / "dir" / "baror.db")
            self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (1, 1), r.stderr)
            r = self.run_script(Path(d) / "x.db", SESSION_DAYS="banana")
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("Traceback", r.stderr)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write anywhere")
    def test_a_directory_the_user_cannot_write_to_is_one_line_error(self):
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, 0o555)
            try:
                r = self.run_script(Path(d) / "baror.db")
            finally:
                os.chmod(d, 0o755)
            self.assertEqual(r.returncode, 1)
            self.assertIn("E-STORAGE", r.stderr)
            self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
