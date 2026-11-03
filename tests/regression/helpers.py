"""Shared helpers: run app.py under AppTest with an isolated USERS_FILE.

BAROR_APP_DIR selects which copy is tested (default: the untouched scratch copy
of the original; use ../fixed to run the same tests against the patched app).
"""
import json, os, shutil, tempfile, warnings, logging
from pathlib import Path
from streamlit.testing.v1 import AppTest

logging.disable(logging.CRITICAL)           # silence streamlit bare-mode warnings
warnings.filterwarnings("ignore")

BUGS = Path(__file__).resolve().parent.parent
APP_DIR = Path(os.environ.get("BAROR_APP_DIR", BUGS)).resolve()
os.chdir(APP_DIR)                            # app opens logo.png / image CWD-relative

def make_users_file(content=None, raw=None, name="users.json"):
    d = tempfile.mkdtemp(prefix="baror_")
    p = os.path.join(d, name)
    if raw is not None:
        with open(p, "wb") as f: f.write(raw)
    elif content is not None:
        with open(p, "w", encoding="utf-8") as f: json.dump(content, f, ensure_ascii=False)
    os.environ["USERS_FILE"] = p
    return p

def user(name="דנה", gender="נקבה", password="pw", is_km=False, history=None, **extra):
    u = {"name": name, "gender": gender, "password": password, "is_km": is_km, "history": history if history is not None else []}
    u.update(extra)
    return u

def new_app(app_dir=None, timeout=60):
    d = Path(app_dir) if app_dir else APP_DIR
    os.chdir(d)
    return AppTest.from_file(str(d / "app.py"), default_timeout=timeout)

def btn(at, label):
    return [b for b in at.button if b.label == label][0]

def login(at, email, pw):
    at.run()
    at.text_input(key="login_email").set_value(email)
    at.text_input(key="login_pass").set_value(pw)
    btn(at, "היכנס").click()
    at.run()
    return at

def register(at, name, email, gender, pw, km=False):
    at.run()
    at.text_input(key="reg_name").set_value(name)
    at.text_input(key="reg_email").set_value(email)
    at.selectbox(key="reg_gender").select(gender)
    at.checkbox(key="reg_km").set_value(km)
    at.text_input(key="reg_pass").set_value(pw)
    btn(at, "הרשם").click()
    at.run()
    return at

def submit_result(at, run_time, pushups=0, date=None):
    ti = [t for t in at.text_input if t.label.startswith("זמן")][0]
    ti.set_value(run_time)
    at.number_input[0].set_value(pushups)
    if date is not None:
        at.date_input[0].set_value(date)
    btn(at, "שמור תוצאות 💾").click()
    at.run()
    return at

def read(p):
    with open(p, encoding="utf-8") as f: return json.load(f)

def texts(at):
    out = []
    for kind in ("markdown", "success", "error", "warning", "info", "title", "subheader"):
        out += [getattr(e, "value", "") for e in at.get(kind)] if kind != "markdown" else [e.value for e in at.markdown]
    return out

def exc(at):
    return [e.value for e in at.exception]

def chart_points(at):
    """number of x-points in each plotly chart"""
    return [len(json.loads(c.proto.spec)["data"][0]["x"]) for c in at.get("plotly_chart")]

def app_copy(drop=()):
    """Copy the app dir to a temp dir (optionally removing files)."""
    d = Path(tempfile.mkdtemp(prefix="baror_app_"))
    for f in ("app.py", "logo.png", "image_bea22f.jpg"):
        if f not in drop:
            shutil.copy(APP_DIR / f, d / f)
    return d
