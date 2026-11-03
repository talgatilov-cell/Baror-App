"""Signed-in state of one browser session. Identity is the server-side session token only (never a widget value);
the user, their role and ``active`` are re-read from the database on EVERY run, so a logout, a deactivation, a
demotion or a deleted account takes effect on the next interaction.

The token lives in ``st.session_state["_auth_token"]`` (not a widget key) and, for "stay signed in after reload", in a
cookie. The cookie is set by a static script (bearer token, not HttpOnly: see baror.auth.sessions for the limits).
Do NOT call ``st.rerun()`` in the run that sets the cookie: the script would never reach the browser."""
from __future__ import annotations

import streamlit as st

from baror.auth import sessions

COOKIE = "baror_session"
TOKEN_KEY = "_auth_token"          # must never equal a widget key


def set_cookie(token: str, days: int) -> None:
    if not sessions.TOKEN_RE.fullmatch(token) or not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 90:
        raise ValueError("bad cookie arguments")           # nothing but a validated token and a number is ever embedded
    st.html(f"<script>document.cookie='{COOKIE}={token}; Max-Age={days * 86400}; Path=/; SameSite=Strict'"
            "+(location.protocol==='https:'?'; Secure':'');</script>", unsafe_allow_javascript=True)


def clear_cookie() -> None:
    st.html(f"<script>document.cookie='{COOKIE}=; Max-Age=0; Path=/';</script>", unsafe_allow_javascript=True)


def sign_out_view(message_key: str) -> None:
    """After ``end``: clear the cookie in THIS run, show the message and stop the script (never ``st.rerun``)."""
    from baror.messages_he import t
    clear_cookie()
    st.text(t(message_key))
    st.stop()


def current_user(db_path, session_days: int):
    """The signed-in user for this run, or None. Falls back to the cookie on a fresh session (reload, restart)."""
    token = st.session_state.get(TOKEN_KEY)
    if token is None:
        token = st.context.cookies.get(COOKIE)
    user = sessions.resolve(db_path, token, slide_days=session_days)
    if user is None:
        st.session_state.pop(TOKEN_KEY, None)
        return None
    st.session_state[TOKEN_KEY] = token
    return user


def start(db_path, user_id: int, session_days: int) -> str:
    """Issue a session for a user who has just proved who they are; remember it in this browser session."""
    token = sessions.issue(db_path, user_id, session_days)
    st.session_state[TOKEN_KEY] = token
    return token


def end(db_path) -> None:
    """Revoke this session's token and forget every non-widget key we hold (all of ours start with an underscore).
    Widget keys are left alone: Streamlit drops the state of widgets that are not rendered in the next run, and
    deleting the key of a widget that was already rendered in THIS run breaks the run."""
    sessions.revoke(db_path, st.session_state.get(TOKEN_KEY))
    for key in [k for k in st.session_state.keys() if isinstance(k, str) and k.startswith("_")]:
        del st.session_state[key]
