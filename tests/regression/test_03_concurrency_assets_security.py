"""Concurrency, missing-asset, markdown-injection and negative-result characterisation tests."""
import ast, json, os, re, tempfile, threading, unittest
from unittest import mock
from helpers import *
import json as _json


def _wrap_dump(hook):
    """Replace json.dump with a wrapper calling hook(real_dump, obj, fp, **kw)."""
    real = _json.dump
    def wrapper(obj, fp, **kw):
        return hook(real, obj, fp, **kw)
    return mock.patch("json.dump", wrapper)


class SaveAtomicity(unittest.TestCase):
    def test_failed_save_must_not_destroy_existing_data(self):        # BR-02
        original = {"a@b.c": user("דנה", history=[{"date": "2026-01-01", "run_seconds": 900, "pushups": 20}])}
        p = make_users_file(original)
        at = new_app(); login(at, "a@b.c", "pw")
        def boom(real, obj, fp, **kw):
            fp.write('{"a@b.c": {"name": "')       # partial write ...
            fp.flush()
            raise OSError(28, "No space left on device")              # ... then the disk fills up
        with _wrap_dump(boom):
            submit_result(at, "15:00", 20)
        try:
            data = read(p)
        except Exception as e:
            self.fail(f"users.json destroyed by failed save: {type(e).__name__}: {e}; size={os.path.getsize(p)}")
        # (compare history/name only: the fixed app upgrades the plaintext password on login)
        self.assertEqual(data["a@b.c"]["history"], original["a@b.c"]["history"])
        self.assertEqual(data["a@b.c"]["name"], "דנה")

    def test_reader_during_write_window(self):                          # BR-03a
        """While session A is between open(..,'w') and the end of json.dump, session B's page load
        sees a truncated file and crashes."""
        p = make_users_file({"a@b.c": user(), "o@o.o": user("אחר")})
        a = new_app(); login(a, "a@b.c", "pw")
        seen = {}
        def hook(real, obj, fp, **kw):
            b = new_app(); b.run()                 # another visitor loads the app NOW
            seen["b_exceptions"] = exc(b)
            os.chdir(APP_DIR)
            return real(obj, fp, **kw)
        with _wrap_dump(hook):
            submit_result(a, "15:00", 20)
        self.assertEqual(seen["b_exceptions"], [], "concurrent visitor must not crash")

    def _race(self, prepared, clickers):
        """prepared: two AppTest objects already loaded & filled in (not yet submitted).
        clickers: two callables that click the submit button.  json.load is wrapped so that BOTH
        sessions finish reading users.json before either of them writes -> deterministic lost update."""
        barrier = threading.Barrier(2, timeout=3)
        real_load = _json.load
        def load_then_wait(fp, **kw):
            data = real_load(fp, **kw)
            try: barrier.wait()
            except threading.BrokenBarrierError: pass
            return data
        out = {}
        def run(tag, at, click):
            try:
                click(at); at.run(); out[tag] = len(at.success)
            except Exception as e:
                out[tag] = repr(e)
        with mock.patch("json.load", load_then_wait):
            ts = [threading.Thread(target=run, args=(t, at, c)) for t, at, c in zip("AB", prepared, clickers)]
            [t.start() for t in ts]; [t.join(60) for t in ts]
        return out

    def _prep_reg(self, name, email, pw):
        at = new_app(); at.run()
        at.text_input(key="reg_name").set_value(name); at.text_input(key="reg_email").set_value(email)
        at.selectbox(key="reg_gender").select("זכר"); at.text_input(key="reg_pass").set_value(pw)
        return at

    def test_concurrent_registrations_lose_a_user(self):               # BR-03b
        p = make_users_file({})
        A = self._prep_reg("A", "a@a.a", "pa"); B = self._prep_reg("B", "b@b.b", "pb")
        click = lambda at: btn(at, "הרשם").click()
        r = self._race([A, B], [click, click])
        self.assertEqual(r, {"A": 1, "B": 1}, r)                        # both were told "registered successfully"
        self.assertEqual(sorted(read(p)), ["a@a.a", "b@b.b"], "one acknowledged registration was silently lost")

    def test_same_email_registration_race_overwrites_account(self):    # BR-03c  (account takeover)
        p = make_users_file({})
        A = self._prep_reg("Alice", "victim@x.y", "alice-pw"); B = self._prep_reg("Mallory", "victim@x.y", "mallory-pw")
        click = lambda at: btn(at, "הרשם").click()
        r = self._race([A, B], [click, click])
        stored = read(p)["victim@x.y"]
        self.assertEqual(r.get("A", 0) + r.get("B", 0), 1,
                         f"both sessions were told they registered the same e-mail: {r}; stored owner={stored['name']}")

    def test_concurrent_saves_lose_a_record(self):                     # BR-03d
        p = make_users_file({"a@b.c": user(), "b@b.c": user("ב")})
        def prep(email):
            at = new_app(); login(at, email, "pw")
            [t for t in at.text_input if t.label.startswith("זמן")][0].set_value("15:00")
            at.number_input[0].set_value(20); return at
        A, B = prep("a@b.c"), prep("b@b.c")
        click = lambda at: btn(at, "שמור תוצאות 💾").click()
        r = self._race([A, B], [click, click])
        d = read(p)
        self.assertEqual((len(d["a@b.c"]["history"]), len(d["b@b.c"]["history"])), (1, 1), r)


class MissingAssets(unittest.TestCase):
    def test_missing_background_image_no_base64_None(self):            # BR-12a
        make_users_file({})
        d = app_copy(drop=("image_bea22f.jpg",))
        at = new_app(d); at.run()
        css = at.markdown[0].value
        self.assertEqual(exc(at), [])
        self.assertNotIn("base64,None", css)
    def test_missing_logo_does_not_crash_dashboard(self):              # BR-12b
        make_users_file({"a@b.c": user()})
        d = app_copy(drop=("logo.png",))
        at = new_app(d); login(at, "a@b.c", "pw")
        self.assertEqual(exc(at), [])
    def test_works_when_cwd_is_not_app_dir(self):                      # BR-12c
        make_users_file({"a@b.c": user()})
        at = new_app(); at.run()
        os.chdir(tempfile.mkdtemp())               # `streamlit run /app/app.py` launched from elsewhere
        at.run()
        css = at.markdown[0].value
        self.assertNotIn("base64,None", css, "background silently missing when CWD != app dir")
        os.chdir(APP_DIR)

    def test_bare_except_is_gone(self):                                # BR-13
        tree = ast.parse((APP_DIR / "app.py").read_text(encoding="utf-8"))
        bare = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.type is None]
        self.assertEqual(bare, [], f"bare except at lines {bare}")


class MarkdownInjection(unittest.TestCase):
    PAYLOAD = "![t](https://attacker.example/pixel.png)[click](https://attacker.example/phish)**"   # BR-19
    def test_name_is_escaped_in_title_and_sidebar(self):
        make_users_file({"a@b.c": user(self.PAYLOAD)})
        at = new_app(); login(at, "a@b.c", "pw")
        title = at.title[0].value
        side = [m.value for m in at.sidebar.markdown if "מחובר" in m.value][0]
        self.assertNotIn("![t](", title); self.assertNotIn("![t](", side)

    def test_html_sinks_interpolate_only_trusted_values(self):         # negative result: passes
        src = (APP_DIR / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and any(k.arg == "unsafe_allow_html" for k in n.keywords):
                for a in n.args:
                    for j in ast.walk(a):
                        if isinstance(j, ast.FormattedValue):
                            names |= {x.id for x in ast.walk(j.value) if isinstance(x, ast.Name)}
        self.assertLessEqual(names, {"bg_base64", "chart_title"}, names)

    def test_odd_emails_roundtrip_safely(self):                        # negative result: passes on the original
        p = make_users_file({})
        for e in ['a"b@x.y', "a\\b@x.y", "a\nb@x.y", "__proto__", "ünï@x.y", "😀@x.y", "x" * 5000]:
            at = new_app(); register(at, "n", e, "זכר", "pw")
            self.assertEqual(exc(at), [], e[:20])
        keys = list(read(p))
        self.assertIn('a"b@x.y', keys)
        for k in keys:                                  # every stored key can log in again (JSON round-trip is lossless)
            at = new_app(); login(at, k, "pw")
            self.assertTrue(at.session_state["logged_in"], repr(k[:20]))


class Dependencies(unittest.TestCase):
    def test_requirements_are_pinned(self):                            # BR-24
        lines = [l.strip() for l in (APP_DIR / "requirements.txt").read_text().splitlines() if l.strip()]
        self.assertTrue(all("==" in l or ">=" in l for l in lines), lines)
    def test_no_deprecated_use_container_width(self):
        src = (APP_DIR / "app.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("use_container_width"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
