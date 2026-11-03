"""Sign-in, registration and password-change forms. Each returns the signed-in user dict after success (a session has
then been started and the caller sets the cookie), else None.

* One generic message for every sign-in failure and ONE for every registration refusal (no allowlist oracle).
* Field problems that do not depend on the allowlist (password length, mismatch, missing gender) are reported per field.
* Entered values stay in the form on error (Streamlit keeps widget state).
* Do not open a ``st.form`` from inside another form; the caller renders these inside ``st.empty().container()``."""
from __future__ import annotations

import logging
import math

import streamlit as st

from baror.auth import passwords, service
from baror.config import Config
from baror.errors import AuthError, BarorError, RateLimitedError
from baror.logging_setup import log_event
from baror.messages_he import render_exception, t
from baror.ui import guards, session

GENDERS = {"gender_male": "M", "gender_female": "F"}


def _show(exc: BarorError) -> None:
    st.error(render_exception(exc))


def _honeypot(form: str):
    """A field no human sees (moved off-screen by CSS, keyed container) but a script that fills every input will."""
    with st.container(key=f"hp_{form}"):
        return st.text_input(t("honeypot_label"), key=f"hp_{form}_value", autocomplete="off")


def _blocked(wait: float) -> None:
    """The address limit speaks with the SAME words as the per-account lock-out, so it says nothing extra."""
    _show(RateLimitedError(params={"seconds": max(1, math.ceil(wait))}))


def login_form(cfg: Config):
    guards.issue_token(cfg, "login")
    with st.form("login"):
        email = st.text_input(t("field_email"), key="login_email", autocomplete="username")
        password = st.text_input(t("field_password"), type="password", key="login_pass", autocomplete="current-password")
        honeypot = _honeypot("login")
        submitted = st.form_submit_button(t("btn_login"), type="primary", width="stretch", icon=":material/login:")
    if not submitted:
        return None
    # Order matters: everything cheap runs BEFORE the password hash (scrypt), so a flood costs almost nothing.
    ip, limiter = guards.client_ip(cfg), guards.limiter()
    wait = limiter.check_login(ip, email)
    if wait > 0:
        log_event("login_blocked_address", level=logging.WARNING)
        _blocked(wait)
        return None
    if not guards.check_and_renew(cfg, "login", honeypot):
        limiter.record_login_failure(ip)                 # a script hammering the form is slowed like a guesser
        log_event("bot_refused", level=logging.WARNING, form="login")
        st.error(t("bot_check_failed"))
        return None
    try:
        user = service.login(cfg.db_path, email, password)
    except RateLimitedError as exc:                      # per-account backoff: the attempt was refused, not guessed
        log_event("login_rate_limited", level=logging.WARNING, email=email)
        _show(exc)
        return None
    except AuthError as exc:
        limiter.record_login_failure(ip, email)
        log_event("login_failed", level=logging.WARNING, email=email)
        _show(exc)
        return None
    except BarorError as exc:
        _show(exc)
        return None
    limiter.record_login_success(ip, email)
    session.start(cfg.db_path, user["id"], cfg.session_days)
    log_event("login_ok", user_id=user["id"])
    return user


def register_form(cfg: Config):
    guards.issue_token(cfg, "register")
    st.caption(t("register_intro"))
    if cfg.cohort_views:
        with st.container(key="cadet_notice"):
            st.write(t("cadet_view_notice"))                                  # told BEFORE the account exists
    with st.form("register"):
        name = st.text_input(t("field_name"), key="reg_name", max_chars=80)
        email = st.text_input(t("field_email"), key="reg_email", autocomplete="username")
        gender_label = st.selectbox(t("field_gender"), [t(k) for k in GENDERS], index=None,
                                    placeholder=t("choose_placeholder"), key="reg_gender")
        is_km = st.checkbox(t("field_km"), key="reg_km")
        password = st.text_input(t("field_password"), type="password", key="reg_pass", autocomplete="new-password")
        st.caption(t("password_rule"))                       # the rule is visible BEFORE typing (not a hidden tooltip)
        confirm = st.text_input(t("field_password_confirm"), type="password", key="reg_pass2", autocomplete="new-password")
        honeypot = _honeypot("register")
        submitted = st.form_submit_button(t("btn_register"), type="primary", width="stretch", icon=":material/person_add:")
    if not submitted:
        return None
    ip, limiter = guards.client_ip(cfg), guards.limiter()
    wait = limiter.check_register(ip)
    if wait > 0:
        log_event("register_blocked_address", level=logging.WARNING)
        _blocked(wait)
        return None
    if not guards.check_and_renew(cfg, "register", honeypot):
        limiter.record_register_attempt(ip)
        log_event("bot_refused", level=logging.WARNING, form="register")
        st.error(t("bot_check_failed"))
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
    limiter.record_register_attempt(ip)                  # every attempt that reaches the password hash counts
    try:
        user = service.register(cfg.db_path, email=email, name=name, gender=gender, is_km=is_km, password=password)
    except BarorError as exc:
        # the user sees ONE text; the operator's log keeps the reason (its detail never contains the e-mail)
        log_event("register_refused", level=logging.INFO, email=email, reason=getattr(exc, "detail", None))
        _show(exc)
        return None
    session.start(cfg.db_path, user["id"], cfg.session_days)
    log_event("register_ok", user_id=user["id"])
    return user


def password_change_form(cfg: Config, user: dict):
    st.subheader(t("pw_change_title"))
    st.write(t("pw_change_body"))
    with st.form("change_password"):
        current = st.text_input(t("field_password_current"), type="password", key="pw_current", autocomplete="current-password")
        new = st.text_input(t("field_password_new"), type="password", key="pw_new", autocomplete="new-password")
        st.caption(t("password_rule"))
        confirm = st.text_input(t("field_password_confirm"), type="password", key="pw_new2", autocomplete="new-password")
        submitted = st.form_submit_button(t("btn_change_password"), type="primary", width="stretch")
    if not submitted:
        return None
    if new != confirm:
        st.error(t("password_confirm_mismatch"))
        return None
    try:
        service.change_password(cfg.db_path, user["id"], current, new)   # revokes every session of this user
    except BarorError as exc:
        log_event("password_change_refused", level=logging.WARNING, user_id=user["id"], reason=exc.message_key)
        _show(exc)
        return None
    session.start(cfg.db_path, user["id"], cfg.session_days)             # a fresh session replaces the revoked one
    log_event("password_changed", user_id=user["id"])
    return True
