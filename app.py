"""Baror App entry point: start-up checks, sign-in / registration, then the signed-in shell.

Identity comes only from the server-side session (cookie token resolved against the database on every run)."""
from __future__ import annotations

import streamlit as st

from baror import boundary, config, startup
from baror.messages_he import t
from baror.ui import auth_views, session, shell, theme


@st.cache_resource
def _startup(env_key: tuple):
    """Process-wide, no user data: configuration and the database schema (created or upgraded once per environment)."""
    cfg = config.from_env({k: v for k, v in env_key if v is not None})
    startup.run_startup_checks(cfg)        # data dir writable, tzdata, integrity BEFORE any migration, schema current
    return cfg


def _sign_in_page(cfg):
    with st.container(key="signin"):          # the theme keys its dark sign-in look on this container (.st-key-signin)
        st.title(t("app_title"))
        st.write(t("app_tagline"))
        tab_login, tab_register = st.tabs([t("login_title"), t("register_title")])
        with tab_login:
            user = auth_views.login_form(cfg)
        with tab_register:
            new_user = auth_views.register_form(cfg)
    return user or new_user


def main() -> None:
    theme.apply()
    cfg = _startup(config.env_key())
    user = session.current_user(cfg.db_path, cfg.session_days)
    if user is None:
        slot = st.empty()
        with slot.container():
            user = _sign_in_page(cfg)
        if user is None:
            return
        slot.empty()                                          # remove the forms in the same run ...
        session.set_cookie(st.session_state[session.TOKEN_KEY], cfg.session_days)   # ... and NO st.rerun()
    if user["must_change_pw"]:
        slot = st.empty()
        with slot.container():
            changed = auth_views.password_change_form(cfg, user)
        if not changed:
            return
        slot.empty()
        session.set_cookie(st.session_state[session.TOKEN_KEY], cfg.session_days)
        user = session.current_user(cfg.db_path, cfg.session_days)
    shell.render(cfg, user)


_icon = theme.ROOT / "logo.png"
st.set_page_config(page_title=t("page_title"), page_icon=str(_icon) if _icon.exists() else None, layout="centered")
boundary.run_app(main)            # expected errors -> Hebrew message; bugs -> generic message + incident id, never a traceback
