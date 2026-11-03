"""Sign-in, registration and password-change forms. Each returns the signed-in user dict after success (a session has
then been started and the caller sets the cookie), else None.

* One generic message for every sign-in failure and ONE for every registration refusal (no allowlist oracle).
* Field problems that do not depend on the allowlist (password length, mismatch, missing gender) are reported per field.
* Entered values stay in the form on error (Streamlit keeps widget state).
* Do not open a ``st.form`` from inside another form; the caller renders these inside ``st.empty().container()``."""
from __future__ import annotations

import streamlit as st

from baror.auth import passwords, service
from baror.config import Config
from baror.errors import BarorError
from baror.messages_he import render_exception, t
from baror.ui import session

GENDERS = {"gender_male": "M", "gender_female": "F"}


def _show(exc: BarorError) -> None:
    st.error(render_exception(exc))


def login_form(cfg: Config):
    with st.form("login"):
        email = st.text_input(t("field_email"), key="login_email", autocomplete="username")
        password = st.text_input(t("field_password"), type="password", key="login_pass", autocomplete="current-password")
        submitted = st.form_submit_button(t("btn_login"), type="primary")
    if not submitted:
        return None
    try:
        user = service.login(cfg.db_path, email, password)
    except BarorError as exc:
        _show(exc)
        return None
    session.start(cfg.db_path, user["id"], cfg.session_days)
    return user


def register_form(cfg: Config):
    st.caption(t("register_intro"))
    with st.form("register"):
        name = st.text_input(t("field_name"), key="reg_name", max_chars=80)
        email = st.text_input(t("field_email"), key="reg_email", autocomplete="username")
        gender_label = st.selectbox(t("field_gender"), [t(k) for k in GENDERS], index=None,
                                    placeholder=t("choose_placeholder"), key="reg_gender")
        is_km = st.checkbox(t("field_km"), key="reg_km")
        password = st.text_input(t("field_password"), type="password", key="reg_pass", autocomplete="new-password",
                                 help=t("password_rule"))
        confirm = st.text_input(t("field_password_confirm"), type="password", key="reg_pass2", autocomplete="new-password")
        submitted = st.form_submit_button(t("btn_register"), type="primary")
    if not submitted:
        return None
    gender = next((code for key, code in GENDERS.items() if t(key) == gender_label), None)
    problems = []
    if gender is None:
        problems.append("INVALID_GENDER")
    try:
        passwords.check_policy(password)
    except BarorError as exc:
        problems.append(exc.message_key)
    if problems:
        for key in problems:
            st.error(render_exception(BarorError(key)))
        return None
    if password != confirm:
        st.error(t("password_confirm_mismatch"))
        return None
    try:
        user = service.register(cfg.db_path, email=email, name=name, gender=gender, is_km=is_km, password=password)
    except BarorError as exc:
        _show(exc)         # RegistrationDeniedError: the same text whatever the reason
        return None
    session.start(cfg.db_path, user["id"], cfg.session_days)
    return user


def password_change_form(cfg: Config, user: dict):
    st.subheader(t("pw_change_title"))
    st.write(t("pw_change_body"))
    with st.form("change_password"):
        current = st.text_input(t("field_password_current"), type="password", key="pw_current", autocomplete="current-password")
        new = st.text_input(t("field_password_new"), type="password", key="pw_new", autocomplete="new-password",
                            help=t("password_rule"))
        confirm = st.text_input(t("field_password_confirm"), type="password", key="pw_new2", autocomplete="new-password")
        submitted = st.form_submit_button(t("btn_change_password"), type="primary")
    if not submitted:
        return None
    if new != confirm:
        st.error(t("password_confirm_mismatch"))
        return None
    try:
        service.change_password(cfg.db_path, user["id"], current, new)   # revokes every session of this user
    except BarorError as exc:
        _show(exc)
        return None
    session.start(cfg.db_path, user["id"], cfg.session_days)             # a fresh session replaces the revoked one
    return True
