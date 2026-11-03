"""T0: repository hygiene. Static checks, no Streamlit needed."""
import re
import shutil
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HAVE_GIT = shutil.which("git") is not None and (ROOT / ".git").exists()


def _lock_pins():
    pins = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+) \\$", line)
        if m:
            pins[m.group(1).lower()] = m.group(2)
    return pins


class GitignoreTests(unittest.TestCase):
    def test_gitignore_is_plain_utf8_lf(self):
        raw = (ROOT / ".gitignore").read_bytes()
        self.assertFalse(raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")), "BOM")
        self.assertNotIn(b"\x00", raw, "NUL bytes: file is UTF-16")
        self.assertNotIn(b"\r", raw, "CRLF line endings")
        raw.decode("utf-8")

    @unittest.skipUnless(HAVE_GIT, "needs a git checkout")
    def test_secrets_and_data_are_ignored(self):
        for name in ("users.json", ".env", "baror.env", "data/baror.db", "x.db", "x.db-wal",
                     "x.db-shm", "backups/baror-20260101.db", ".idea/misc.xml",
                     "users.json.premigration", "migration_rejects.json"):
            r = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT)
            self.assertEqual(r.returncode, 0, f"{name} is not ignored")

    @unittest.skipUnless(HAVE_GIT, "needs a git checkout")
    def test_idea_is_not_tracked(self):
        out = subprocess.run(["git", "ls-files", ".idea"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "")

    @unittest.skipUnless(HAVE_GIT, "needs a git checkout")
    def test_no_data_files_tracked(self):
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split("\n")
        bad = [f for f in out if re.search(r"(\.db(-wal|-shm)?|users\.json.*|\.env)$", f)]
        self.assertEqual(bad, [])


class DependencyTests(unittest.TestCase):
    def test_requirements_are_exact_and_only_three(self):
        lines = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
        names = sorted(re.match(r"^([a-z]+)==\d", l).group(1) for l in lines)
        self.assertEqual(names, ["pandas", "plotly", "streamlit"])

    def test_requirements_match_the_lock(self):
        pins = _lock_pins()
        for line in (ROOT / "requirements.txt").read_text().splitlines():
            if "==" in line:
                name, ver = line.strip().split("==")
                self.assertEqual(pins.get(name), ver, name)

    def test_every_lock_entry_has_a_hash(self):
        text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        entries = re.findall(r"^[A-Za-z0-9_.\-]+==\S+ \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n?)+)", text, re.M)
        self.assertEqual(len(entries), len(_lock_pins()))
        self.assertGreaterEqual(len(entries), 30)


class StreamlitConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))

    def test_error_details_hidden(self):
        self.assertEqual(self.cfg["client"]["showErrorDetails"], "none")

    def test_server_options(self):
        self.assertTrue(self.cfg["server"]["enableXsrfProtection"])
        self.assertTrue(self.cfg["server"]["enableStaticServing"])
        self.assertEqual(self.cfg["client"]["toolbarMode"], "minimal")


if __name__ == "__main__":
    unittest.main()
