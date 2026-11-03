"""TEMPORARY demo switch, for showing the app (owner / class demo). To remove it later: delete this file, its test
(tests/test_demo_switch.py), the four ``demo_*`` keys in messages_he.py, the ``demo_banner`` rules at the end of
static/app.css, the README section, and the three ``demo_switch`` lines in app.py.

What it does. When the operator sets ``BAROR_DEMO_ENABLED=1`` (default: off, and then nothing here has any effect), the
sign-in page shows a "מצב הדגמה" toggle. ON: this browser session works against a SEPARATE demo database (fake people,
one shared demo password, staff screens enabled) that is created and filled on first use. OFF: the real app and the
real database. The two never mix:

* the demo database is another file (``DEMO_DB_PATH``, default ``demo.db`` next to the real database);
* the switch refuses to exist when both paths are the same file, so the real data can never be reached in demo mode;
* a demo sign-in token is resolved only against the demo database and a real one only against the real database;
* the setting is only a flag (session state and a ``baror_demo=1`` cookie): it carries no identity and grants nothing.

Uses the standard library and ``demo/demo_seed.py`` (not shipped in the Docker image: without that file the switch does
not appear)."""
from __future__ import annotations

import dataclasses
import importlib.util
import os
from pathlib import Path

import streamlit as st

from baror import startup
from baror.config import Config
from baror.messages_he import t

ENV_ENABLE = "BAROR_DEMO_ENABLED"
ENV_DB = "DEMO_DB_PATH"
COOKIE = "baror_demo"
MODE_KEY = "demo_mode"            # plain session flag (not a widget key, and no leading underscore: it survives sign-out)
TOGGLE_KEY = "demo_toggle"
_SEED = Path(__file__).resolve().parent.parent.parent / "demo" / "demo_seed.py"


def _load_seed():
    spec = importlib.util.spec_from_file_location("baror_demo_seed", _SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def demo_db_path(real: Config) -> Path:
    return Path(os.environ.get(ENV_DB) or (Path(real.db_path).parent / "demo.db"))


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def available(real: Config) -> bool:
    """Operator opted in, the seed script is present, and the demo database is not the real one."""
    return (os.environ.get(ENV_ENABLE) == "1" and _SEED.is_file()
            and not _same_file(demo_db_path(real), Path(real.db_path)))


def is_on(real: Config) -> bool:
    if not available(real):
        return False
    if MODE_KEY not in st.session_state:                     # a fresh browser session: the cookie remembers the choice
        st.session_state[MODE_KEY] = st.context.cookies.get(COOKIE) == "1"
    return bool(st.session_state[MODE_KEY])


@st.cache_resource
def _ensure_demo_db(demo_path: str, real_path: str, cfg: Config) -> bool:
    """Once per process (and path): create and fill the demo database if it does not exist, then run the normal
    start-up checks on it (which upgrade an older demo file). Never called with the real path."""
    if _same_file(Path(demo_path), Path(real_path)):
        raise RuntimeError("the demo database must not be the real database")
    if not Path(demo_path).exists():
        _load_seed().main(demo_path)
    startup.run_startup_checks(dataclasses.replace(cfg, db_path=Path(demo_path), cohort_views=True))
    return True


def effective(real: Config) -> Config:
    """The configuration for this run: the real one, or the same settings pointed at the demo database."""
    if not is_on(real):
        return real
    demo_path = demo_db_path(real)
    _ensure_demo_db(str(demo_path), str(real.db_path), real)
    return dataclasses.replace(real, db_path=demo_path, cohort_views=True)


def _changed() -> None:
    st.session_state[MODE_KEY] = bool(st.session_state.get(TOGGLE_KEY))


def render_switch(real: Config) -> None:
    """On the sign-in page (below the forms). Static scripts only: nothing typed by a user is ever embedded."""
    if not available(real):
        return
    on = is_on(real)
    st.toggle(t("demo_switch_label"), value=on, key=TOGGLE_KEY, on_change=_changed, help=t("demo_switch_help"))
    if on:
        st.caption(t("demo_accounts_intro"))
        st.caption(t("demo_accounts_list"))
        st.code(_load_seed().PASSWORD, language=None)
    # the choice survives a reload: a flag cookie, written in the same run (no st.rerun here, or the script is lost)
    if on:
        st.html(f"<script>document.cookie='{COOKIE}=1; Max-Age=31536000; Path=/; SameSite=Strict'"
                "+(location.protocol==='https:'?'; Secure':'');</script>", unsafe_allow_javascript=True)
    else:
        st.html(f"<script>document.cookie='{COOKIE}=; Max-Age=0; Path=/';</script>", unsafe_allow_javascript=True)


def banner(real: Config) -> None:
    """A visible reminder on every signed-in screen while the demo is on."""
    if is_on(real):
        with st.container(key="demo_banner"):
            st.caption(t("demo_banner"))
