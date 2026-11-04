"""The accounts panel: who holds which role, title and platoon, and the grant of a role. Used on the admin screen and on
the chief page (``render(cfg, user)``); storage limits the chief (instructor grants only, no admin rows, no e-mail release).

Flow: a search form -> one table (st.dataframe, single-row selection; a drop-down below it does the same job for people
who cannot use the grid) -> a panel for the chosen account -> a dialog before anything is written.

* The search is read ONCE per search (and once when the panel first opens) and kept under a ``_`` key; a rerun is not a
  second audited read. Any change drops it (``cadet_admin.invalidate``).
* A grant to instructor or chief goes through a dialog that shows the person's name, e-mail and registration time, says
  that anyone can register under any e-mail, and requires the box "הזהות אומתה מחוץ למערכת". The name and the time that were
  shown are sent as ``expected_name`` / ``expected_created_at``: storage refuses with ACCOUNT_CHANGED when the account no
  longer matches. ``identity_block`` is the reusable part (the nomination approval uses the same).
* Admin rows are read-only here (they are changed by the operator script only). Nobody changes their own role.

Safety: names and e-mails are typed by people: ``st.text``, a table cell or a widget value only, never Markdown or HTML.
The registration time is shown in the course's time zone by ``registered_text`` (the profile uses the same function, so
the two can be compared)."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from baror.config import Config
from baror.domain import roles
from baror.errors import BarorError
from baror.logging_setup import log_event
from baror.messages_he import render_exception, t
from baror.storage import people
from baror.ui import cadet_admin
from baror.ui.components import iso

PAGE = 50
_ROWS = "_acct_rows"                  # {"q", "role", "res", "gen"}  (the one audited search read)
_GEN = "_acct_gen"                    # grows with every read: new table / drop-down widgets, so no stale selection
_PREV = "_acct_prev"                  # (gen, table choice, drop-down choice, chosen id) of the previous run


# ------------------------------------------------------------------------------------------------------- formatting
def registered_text(created_at: str | None, tz) -> str:
    """'2026-11-04T09:30:12Z' (UTC) -> '04/11/2026 11:30' in the course's time zone. Unreadable input is returned as is."""
    if not created_at:
        return ""
    try:
        when = dt.datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).astimezone(tz)
    except ValueError:
        return str(created_at)
    return when.strftime("%d/%m/%Y %H:%M")


def role_text(role: str) -> str:
    return t("role_" + role) if role in roles.ROLES else str(role)


def title_text(role: str, title: str | None) -> str:
    if role not in roles.TITLES_BY_ROLE:
        return ""
    return t("title_" + title) if title in roles.ALL_TITLES else t("title_unset")


# --------------------------------------------------------------------------------------------------------- reading
def _load(cfg: Config, user: dict, query: str, role: str | None) -> dict | None:
    try:
        res = people.search_accounts(cfg.db_path, user["id"], query=query, role=role, limit=PAGE)
    except BarorError as exc:
        st.error(render_exception(exc), icon=":material/error:")
        return None
    gen = st.session_state.get(_GEN, 0) + 1
    st.session_state[_GEN] = gen
    held = {"q": query, "role": role, "res": res, "gen": gen}
    st.session_state[_ROWS] = held
    return held


def _platoons(cfg: Config, user: dict) -> list[dict]:
    try:
        return people.list_platoons(cfg.db_path, user["id"])
    except BarorError:
        return []


def _platoon_label(p: dict) -> str:
    status = t("adm_has_mm") if p["mm_user_id"] else t("adm_no_mm")
    return t("adm_platoon_option", name=p["name"], status=status, n=p["cmdrs"], max=roles.MAX_CMDRS_PER_PLATOON)


# --------------------------------------------------------------------------------------------------------- the grant
def identity_block(cfg: Config, *, name: str, email: str, created_at: str, key: str) -> bool:
    """What the person who grants must see and confirm: name, e-mail, registration time, the warning that anyone can
    register under any e-mail, and the box 'הזהות אומתה מחוץ למערכת'. Returns whether the box is ticked."""
    st.text(t("adm_name_line", name=name))
    st.text(t("adm_email_line", email=email))
    st.text(t("adm_registered_at", date=registered_text(created_at, cfg.tz)))
    st.warning(t("adm_grant_note"), icon=":material/warning:")
    return st.checkbox(t("adm_verified"), key=f"{key}_verified")


def grant_body(cfg: Config, user: dict, row: dict, new_role: str, title: str | None, platoon_id: int | None,
               platoon_name: str | None) -> None:
    k = f"grant_{row['id']}"
    verified = identity_block(cfg, name=row["name"], email=row["email"], created_at=row["created_at"], key=k)
    role_line = role_text(new_role) if not title else f"{role_text(new_role)}, {title_text(new_role, title)}"
    st.text(t("adm_role_line", v=role_line))
    if platoon_name:
        st.text(t("adm_line_platoon", platoon=platoon_name))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        go = st.button(t("btn_adm_grant"), type="primary", width="stretch", key=f"{k}_ok", disabled=not verified,
                       icon=":material/verified_user:")
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=f"{k}_cancel"):
            st.rerun()
    if not go:
        return
    if not verified:
        st.error(t("adm_verify_needed"), icon=":material/error:")
        return
    try:
        people.set_role(cfg.db_path, user["id"], row["id"], new_role, title=title, platoon_id=platoon_id,
                        expected_name=row["name"], expected_created_at=row["created_at"])
    except BarorError as exc:
        if exc.message_key == "ACCOUNT_CHANGED":       # what was verified is no longer what is stored: start again with fresh data
            st.session_state["_notice_error"] = render_exception(exc)
            cadet_admin.invalidate()
            st.rerun()
        st.error(render_exception(exc), icon=":material/error:")
        return
    log_event("role_changed", user_id=user["id"])
    cadet_admin.finish("adm_saved")


@st.dialog(t("adm_grant_title"))
def grant_dialog(cfg: Config, user: dict, row: dict, new_role: str, title: str | None, platoon_id: int | None,
                 platoon_name: str | None) -> None:
    grant_body(cfg, user, row, new_role, title, platoon_id, platoon_name)


def demote_body(cfg: Config, user: dict, row: dict) -> None:
    k = f"demote_{row['id']}"
    st.text(t("adm_name_line", name=row["name"]))
    st.write(t("adm_demote_body"))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        go = st.button(t("btn_adm_demote"), type="primary", width="stretch", key=f"{k}_ok")
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=f"{k}_cancel"):
            st.rerun()
    if not go:
        return
    try:
        people.set_role(cfg.db_path, user["id"], row["id"], "cadet")
    except BarorError as exc:
        st.error(render_exception(exc), icon=":material/error:")
        return
    log_event("role_changed", user_id=user["id"])
    cadet_admin.finish("adm_saved")


@st.dialog(t("adm_demote_title"))
def demote_dialog(cfg: Config, user: dict, row: dict) -> None:
    demote_body(cfg, user, row)


def release_body(cfg: Config, user: dict, row: dict) -> None:
    k = f"release_{row['id']}"
    st.text(t("adm_name_line", name=row["name"]))
    st.text(t("adm_email_line", email=row["email"]))
    st.write(t("adm_release_body"))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        go = st.button(t("btn_adm_release_ok"), type="primary", width="stretch", key=f"del_ok_{k}")
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=f"{k}_cancel"):
            st.rerun()
    if not go:
        return
    try:
        people.release_email(cfg.db_path, user["id"], row["id"])
    except BarorError as exc:
        st.error(render_exception(exc), icon=":material/error:")
        return
    log_event("email_released", user_id=user["id"])
    cadet_admin.finish("adm_released")


@st.dialog(t("adm_release_title"))
def release_dialog(cfg: Config, user: dict, row: dict) -> None:
    release_body(cfg, user, row)


# ------------------------------------------------------------------------------------------------ the chosen account
def _edit_panel(cfg: Config, user: dict, row: dict) -> None:
    rid, gen = row["id"], st.session_state.get(_GEN, 0)
    platoons = {p["id"]: p for p in _platoons(cfg, user)}
    with st.container(border=True, key="acct_edit"):
        st.text(row["name"])                                              # first line = the heading (stylesheet)
        st.text(row["email"])
        st.caption(iso(t("adm_registered_at", date=registered_text(row["created_at"], cfg.tz))))
        st.text(f"{role_text(row['role'])}" + (f" · {title_text(row['role'], row['title'])}" if row["role"] in roles.TITLES_BY_ROLE else ""))
        if row.get("platoon"):
            st.text(t("adm_line_platoon", platoon=row["platoon"]))
        if row["role"] == "admin":
            st.info(t("adm_admin_readonly"), icon=":material/lock:")
            return
        if not row["active"]:
            st.caption(t("adm_inactive"))
            return
        if rid == user["id"]:
            st.caption(t("adm_self"))
            return
        targets = [r for r in roles.ROLES if r != "admin" and (r == row["role"] or roles.may_change(user["role"], row["role"], r))]
        if user["role"] == "chief" and row["role"] == "chief":
            st.caption(t("adm_chief_readonly"))
            return
        k = f"acct_{rid}_{gen}"
        new_role = st.selectbox(t("adm_field_role"), targets, index=targets.index(row["role"]), key=f"{k}_role",
                                format_func=role_text, filter_mode=None)
        title = pid = None
        if new_role in roles.TITLES_BY_ROLE:
            options = list(roles.TITLES_BY_ROLE[new_role])
            current = row["title"] if row["role"] == new_role and row["title"] in options else None
            title = st.selectbox(t("adm_field_title"), options, index=options.index(current) if current else None, key=f"{k}_title",
                                 format_func=lambda x: title_text(new_role, x), placeholder=t("choose_placeholder"),
                                 filter_mode=None)
        if new_role == "instructor":
            ids = list(platoons)
            current_p = row["platoon_id"] if row["role"] == "instructor" and row["platoon_id"] in platoons else None
            pid = st.selectbox(t("adm_field_platoon"), ids, index=ids.index(current_p) if current_p is not None else None,
                               key=f"{k}_platoon", format_func=lambda i: _platoon_label(platoons[i]),
                               placeholder=t("choose_placeholder"), filter_mode=None)
            chosen = platoons.get(pid)
            if chosen and pid != current_p:
                if title == "mm" and chosen["mm_user_id"] and chosen["mm_user_id"] != rid:
                    st.warning(t("adm_hint_mm_taken"), icon=":material/info:")
                if title != "mm" and chosen["cmdrs"] >= roles.MAX_CMDRS_PER_PLATOON:
                    st.warning(t("adm_hint_cmdr_full", max=roles.MAX_CMDRS_PER_PLATOON), icon=":material/info:")
        needs_title = new_role in roles.TITLES_BY_ROLE
        complete = (not needs_title or title is not None) and (new_role != "instructor" or pid is not None)
        changed = new_role != row["role"] or (needs_title and title != row["title"]) or \
            (new_role == "instructor" and pid != row["platoon_id"])
        if needs_title and not complete:
            st.caption(t("adm_fill_title_platoon" if new_role == "instructor" else "adm_fill_title"))
        if st.button(t("btn_adm_save"), key=f"{k}_save", type="primary", width="stretch", icon=":material/save:",
                     disabled=not (changed and complete)):
            _save(cfg, user, row, new_role, title, pid, platoons)
        if user["role"] == "admin" and row["role"] == "cadet":
            if st.button(t("btn_adm_release"), key=f"release_open_{rid}", width="stretch", type="tertiary",
                         icon=":material/mail_lock:"):
                release_dialog(cfg, user, row)


def _save(cfg: Config, user: dict, row: dict, new_role: str, title, pid, platoons: dict) -> None:
    if new_role != row["role"]:
        if new_role in ("instructor", "chief"):
            grant_dialog(cfg, user, row, new_role, title, pid, platoons[pid]["name"] if pid in platoons else None)
        else:
            demote_dialog(cfg, user, row)
        return
    try:                                                    # same role: a title and / or platoon change
        if new_role == "instructor" and pid != row["platoon_id"]:
            people.move_instructor(cfg.db_path, user["id"], row["id"], pid)
        if title != row["title"]:
            people.set_title(cfg.db_path, user["id"], row["id"], title)
    except BarorError as exc:
        cadet_admin.invalidate()                            # a first step may have been written
        st.error(render_exception(exc), icon=":material/error:")
        return
    log_event("role_changed", user_id=user["id"])
    cadet_admin.finish("adm_saved")


# ----------------------------------------------------------------------------------------------------------- render
def _choose(held: dict, table_rows: list, pick) -> int | None:
    """The chosen account id: whichever of the table and the drop-down was touched last (the other may still hold an old
    choice). Both are keyed by the read's generation, so a new search starts clean."""
    rows = held["res"]["rows"]
    tbl = rows[table_rows[0]]["id"] if table_rows and 0 <= table_rows[0] < len(rows) else None
    prev = st.session_state.get(_PREV)
    prev_gen, prev_t, prev_p, prev_sel = prev if prev and prev[0] == held["gen"] else (held["gen"], None, None, None)
    sel = tbl if tbl != prev_t else pick if pick != prev_p else prev_sel
    st.session_state[_PREV] = (held["gen"], tbl, pick, sel)
    return sel


def render(cfg: Config, user: dict) -> None:
    with st.container(key="accounts_panel"):
        st.subheader(t("adm_accounts_title"))
        st.caption(t("adm_accounts_intro"))
        role_opts = [r for r in roles.ROLES if user["role"] == "admin" or r != "admin"]
        held = st.session_state.get(_ROWS)
        with st.form("acct_search", border=False):
            query = st.text_input(t("adm_search_label"), key="acct_q", max_chars=80)
            role = st.selectbox(t("adm_role_filter"), role_opts, index=None, key="acct_role", format_func=role_text,
                                placeholder=t("adm_role_all"), filter_mode=None)
            go = st.form_submit_button(t("btn_adm_search"), width="stretch", icon=":material/search:")
        if go:
            held = _load(cfg, user, query.strip(), role)
        elif held is None:
            held = _load(cfg, user, "", None)
        if held is None:
            return
        rows, total = held["res"]["rows"], held["res"]["total"]
        if not rows:
            st.caption(t("adm_none"))
            return
        st.caption(t("adm_count" if total <= len(rows) else "adm_count_more", n=len(rows), total=total))
        frame = pd.DataFrame([{t("adm_col_name"): r["name"], t("adm_col_email"): r["email"],
                               t("adm_col_registered"): registered_text(r["created_at"], cfg.tz),
                               t("adm_col_role"): role_text(r["role"]), t("adm_col_title"): title_text(r["role"], r["title"]),
                               t("adm_col_platoon"): r["platoon"] or ""} for r in rows])
        with st.container(key="acct_table_box"):
            event = st.dataframe(frame, hide_index=True, on_select="rerun", selection_mode="single-row", width="stretch",
                                 key=f"acct_table_{held['gen']}")
        st.caption(t("adm_table_hint"))
        by_id = {r["id"]: r for r in rows}
        pick = st.selectbox(t("adm_pick_account"), list(by_id), index=None, key=f"acct_pick_{held['gen']}",
                            placeholder=t("choose_placeholder"), filter_mode=None,
                            format_func=lambda i: f"{by_id[i]['name']} · {by_id[i]['email']}")
        chosen = _choose(held, list(event.selection.rows), pick)
        if chosen in by_id:
            _edit_panel(cfg, user, by_id[chosen])
