"""Profile: who is signed in, the notice about the instructor view, export of the data, sign-out, account deletion.

* The name is user-controlled text: ``st.text`` only (no Markdown interpretation).
* Export is the cadet's OWN data as a PDF (``repo.export_user_data`` -> ``baror.export_pdf``: account without the
  password hash, results, accommodations, and who read them). Nothing is cached.
* Deleting the account asks for the password again (``service.login`` verifies it with the usual backoff), then
  ``repo.delete_user`` removes the account, sessions and results. Sign-out and deletion clear the cookie in the SAME run
  and do not rerun (a rerun would drop the script that clears it)."""
from __future__ import annotations

import datetime as dt

import streamlit as st

from baror.auth import service
from baror.config import Config
from baror import export_pdf
from baror.errors import BarorError
from baror.logging_setup import log_event
from baror.messages_he import render_exception, t
from baror.storage import repo
from baror.ui import session
from baror.ui.text import esc  # noqa: F401  (kept for symmetry with the other screens)


def export_bytes(cfg: Config, user: dict) -> bytes:
    """The cadet's own data as a PDF (read at render time; nothing is cached)."""
    data = repo.export_user_data(cfg.db_path, user["id"])
    return export_pdf.build_pdf(data, today=dt.datetime.now(cfg.tz).date())


def delete_account(cfg: Config, user: dict, password: str) -> None:
    """Re-check the password (same backoff and generic error as sign-in), then erase account, sessions and results.
    Raises BarorError when the password is refused; nothing is deleted then."""
    service.login(cfg.db_path, user["email"], password)
    log_event("account_deleted", user_id=user["id"])
    repo.delete_user(cfg.db_path, user["id"])


@st.dialog(t("delete_account_title"))
def _delete_account_dialog(cfg: Config, user: dict) -> None:
    st.write(t("delete_account_body"))
    password = st.text_input(t("field_password"), type="password", key="del_acct_pw", autocomplete="current-password")
    st.caption(t("delete_account_password"))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        confirm = st.button(t("btn_delete_account_confirm"), type="primary", width="stretch", icon=":material/delete_forever:",
                            key="del_acct_ok")
    with c2:
        if st.button(t("btn_keep_account"), width="stretch", key="del_acct_keep"):
            st.rerun()
    if not confirm:
        return
    try:
        delete_account(cfg, user, password)
    except BarorError as exc:
        st.error(render_exception(exc))
        return
    session.end(cfg.db_path)
    session.sign_out_view("account_deleted")


def render(cfg: Config, user: dict) -> None:
    st.subheader(t("profile_title"))
    st.text(user["name"])
    if cfg.cohort_views and user["role"] == "cadet":
        with st.container(key="cadet_notice"):
            st.write(t("cadet_view_notice"))
    st.download_button(t("btn_export"), data=export_bytes(cfg, user), file_name="baror-data.pdf",
                       mime="application/pdf", icon=":material/picture_as_pdf:", width="stretch", key="export")
    st.caption(t("export_body"))
    st.divider()                                              # the irreversible action is set apart from the rest
    if st.button(t("btn_delete_account"), icon=":material/delete_forever:", width="stretch", key="delete_account"):
        _delete_account_dialog(cfg, user)
