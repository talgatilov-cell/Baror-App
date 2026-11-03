"""The cadet's own screens: summary, add result (with the inline result card) and history with edit / delete dialogs.
Everything is read and written through the user-scoped repository with the id of the SIGNED-IN user (never a widget
value). Each test is graded on its own; there is deliberately no overall verdict ("כ.מ" and the overall rule are open).

State kept between runs lives under keys that start with an underscore (``session.end`` removes them at sign-out):
``_form_errors`` (field -> message of the last failed save), ``_pending`` (a save waiting for the cadet's confirmation:
0 push-ups, or a result identical to a stored one), ``_last_result`` (what the result card shows), ``_notice``.
"""
from __future__ import annotations

import datetime as dt
import html
from dataclasses import asdict

import streamlit as st

from baror import boundary
from baror.config import Config
from baror.domain.entry import Entry, Graded, grade_entry, grade_value, parse_entry
from baror.domain.scoring import PLAUSIBILITY, Metric, format_deciseconds
from baror.errors import BarorError, NotFoundError, ValidationError
from baror.logging_setup import log_event
from baror.messages_he import duration_phrase, render_exception, t
from baror.storage import repo
from baror.ui import charts, components, nav
from baror.ui.format import thresholds_for, time_text
from baror.ui.text import esc

FORM_KEYS = ("add_date", "add_min", "add_sec", "add_push")
HISTORY_PAGE = 5


# ------------------------------------------------------------------------------------------------ shared helpers
def _today(cfg: Config) -> dt.date:
    return dt.datetime.now(cfg.tz).date()


def _aerobic(user: dict) -> str:
    return "walk" if user["is_km"] else "run"


def minute_options(aero: str, current: int | None = None) -> list[int]:
    """Whole minutes a cadet can choose: the domain's hard plausibility bounds (7..60 for the run, 25..180 for the walk),
    so an impossible time cannot be picked. A stored legacy value outside the range stays selectable when editing."""
    bounds = PLAUSIBILITY[Metric.WALK_6KM if aero == "walk" else Metric.RUN_3KM]
    options = list(range(-(-bounds.hard_min // 600), bounds.hard_max // 600 + 1))
    if current is not None and current not in options:
        options = sorted([*options, current])
    return options


SECOND_OPTIONS = list(range(60))
PUSHUP_OPTIONS = list(range(201))              # 0..200 (MAX_PUSHUPS in the domain)


def _picker(label: str, options: list[int], key: str, placeholder: str, value: int | None = None, fmt=str) -> int | None:
    """A select-only drop-down (no typing, so no on-screen keyboard): ``filter_mode=None`` turns the search box off.
    Empty until chosen unless a stored ``value`` is given (edit dialog)."""
    index = options.index(value) if value in options else None
    return st.selectbox(label, options, index=index, placeholder=placeholder, key=key, filter_mode=None, format_func=fmt)


def _scale_label(user: dict) -> str:
    gender = t("scale_female" if user["gender"] == "F" else "scale_male")
    return t("scale_walk", gender=gender) if user["is_km"] else gender


def fmt_date(iso: str, short: bool = False) -> str:
    d = dt.date.fromisoformat(iso)
    return d.strftime("%d/%m" if short else "%d/%m/%Y")


def gap_text(g: Graded) -> str | None:
    from baror.hebrew import count_phrase
    if g.level is None:
        return None
    if g.level == "excellent":
        return t("at_top")
    if not g.gap:
        return None
    phrase = count_phrase("rep", g.gap) if g.test == "pushups" else duration_phrase(g.gap)
    one = g.gap == 1
    key = ("fail_with_gap_one" if one else "fail_with_gap") if g.level == "fail" else \
          ("gap_to_excellent_one" if one else "gap_to_excellent")
    return t(key, gap=phrase)


def _mmss(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _delta_text(unit: str, diff: int) -> str:
    from baror.hebrew import count_phrase
    return count_phrase("rep", diff) if unit == "pushups" else duration_phrase(diff)


def vs_previous(test: str, previous: int | None, current: int) -> str | None:
    """Change against the previous test, as a noun phrase (no clock format). Run/walk values are tenths of a second
    (lower is better, shown in whole seconds); push-ups are repetitions (higher is better)."""
    if previous is None:
        return None
    if test == "pushups":
        diff = current - previous
        amount = abs(diff)
    else:
        diff = previous - current
        amount = abs(diff) // 10
    if amount == 0:
        return t("vs_prev_same")
    return t("vs_prev_better" if diff > 0 else "vs_prev_worse", gap=_delta_text(test, amount))


def _status_block(test_key: str, value: str, g: Graded, extra: list[str | None] = ()) -> None:
    """Test name + value + status badge (icon and word) and the sentences under it."""
    st.markdown(f"**{t(test_key)} {components.iso(value)}** {components.status_badge(g.level)}")
    for line in (gap_text(g), *extra):
        if line:
            st.caption(line)
    if g.warn:
        st.caption(t("result_warning"))


# ------------------------------------------------------------------------------------------------------ summary
def _latest_two(rows: list[dict], field: str):
    have = [r for r in rows if r[field] is not None]
    return (have[0] if have else None), (have[1] if len(have) > 1 else None)


def _kpis(rows: list[dict], user: dict, key: str = "kpis") -> None:
    aero = _aerobic(user)
    last_run, prev_run = _latest_two(rows, "run_tenths")
    last_pu, prev_pu = _latest_two(rows, "pushups")
    best = min((r["run_tenths"] for r in rows if r["run_tenths"] is not None), default=None)
    kpis = st.container(key=key)
    c1, c2, c3 = kpis.columns(3, wrap=False, gap="xsmall")
    with c1:
        label = t("kpi_last_walk" if aero == "walk" else "kpi_last_run")
        if last_run is None:
            st.metric(label, "—", border=True)
        else:
            cur = last_run["run_tenths"]
            prev = prev_run["run_tenths"] if prev_run else None
            if prev is not None and prev // 10 != cur // 10:
                slower = cur > prev
                st.metric(label, time_text(cur), _mmss(abs(cur - prev) // 10),
                          delta_color="red" if slower else "green", delta_arrow="up" if slower else "down", border=True)
            else:
                st.metric(label, time_text(cur), border=True)
    with c2:
        st.metric(t("kpi_best"), "—" if best is None else time_text(best), border=True)
    with c3:
        if last_pu is None:
            st.metric(t("kpi_pushups"), "—", border=True)
        else:
            cur = last_pu["pushups"]
            prev = prev_pu["pushups"] if prev_pu else None
            if prev is not None and prev != cur:
                st.metric(t("kpi_pushups"), str(cur), str(abs(cur - prev)), delta_color="green" if cur > prev else "red",
                          delta_arrow="up" if cur > prev else "down", border=True)
            else:
                st.metric(t("kpi_pushups"), str(cur), border=True)
    if prev_run is not None or prev_pu is not None:        # the note explains the arrows: only when there are arrows
        st.caption(t("kpi_note"))


def summary_page(cfg: Config, user: dict) -> None:
    st.title(t("greeting", name=esc(user["name"])))
    st.caption(t("scale_line", scale=_scale_label(user)))
    rows = repo.list_results(cfg.db_path, user["id"])
    if not rows:
        with st.container(border=True, key="empty_state"):
            st.subheader(t("empty_summary_title"))
            st.write(t("empty_summary_body"))
            st.button(t("btn_first_result"), type="primary", width="stretch", icon=":material/add_circle:",
                      on_click=nav.go, args=("add",), key="empty_cta")
        return
    _kpis(rows, user)
    aero = _aerobic(user)
    last_run, _ = _latest_two(rows, "run_tenths")
    last_pu, _ = _latest_two(rows, "pushups")
    with st.container(key="status_rows"):
        st.subheader(t("latest_status_title"))
        if last_run:
            g = grade_value(aero, user["gender"], last_run["test_date"], last_run["run_tenths"])
            _status_block("test_walk" if aero == "walk" else "test_run", time_text(last_run["run_tenths"]), g)
        if last_pu:
            g = grade_value("pushups", user["gender"], last_pu["test_date"], last_pu["pushups"])
            _status_block("test_pushups", str(last_pu["pushups"]), g)
    charts.render(rows, user)


# -------------------------------------------------------------------------------------------- add result: logic
def validate_form(*, day, minutes, seconds, pushups, today: dt.date, is_km: bool) -> tuple[Entry | None, dict[str, str]]:
    """Widget values -> (Entry, {}) or (None, {field: message}). Fields: date, time, push, form. Pure: no streamlit."""
    errs: dict[str, str] = {}
    if day is None:
        errs["date"] = t("err_date_missing")
    run_text = None
    if minutes is not None or seconds is not None:
        if minutes is None:
            errs["time"] = t("err_minutes_missing")
        elif seconds is None:
            errs["time"] = t("err_seconds_missing")
        else:
            run_text = f"{int(minutes)}:{int(seconds):02d}"
    if errs:
        return None, errs
    try:
        entry = parse_entry(run_text=run_text, pushups=None if pushups is None else int(pushups), test_date=day,
                            today=today, is_km=is_km)
    except ValidationError as exc:
        k = exc.message_key
        if k == "FUTURE_DATE":
            errs["date"] = t("err_future_date")
        elif k == "TIME_ZERO":
            errs["time"] = t("err_time_zero")
        elif k == "TIME_OUT_OF_RANGE":
            errs["time"] = t("err_time_implausible", time=components.iso(f"{int(minutes):02d}:{int(seconds):02d}"))
        elif k == "TIME_SECONDS_RANGE":
            errs["time"] = t("err_seconds_range")
        elif k == "INVALID_TIME_FORMAT":
            errs["time"] = t("err_minutes_missing")
        elif k == "INVALID_RESULT":
            errs["push"] = t("err_pushups_range")
        elif k == "EMPTY_RESULT":
            errs["form"] = t("err_none")
        else:
            errs["form"] = render_exception(exc)
        return None, errs
    return entry, {}


def _previous_values(rows: list[dict], on: str) -> dict:
    """Latest earlier-or-same-day value of each test and the best of all stored values (before the new result)."""
    prior = [r for r in rows if r["test_date"] <= on]          # rows are newest first
    pick = lambda field: next((r[field] for r in prior if r[field] is not None), None)
    runs = [r["run_tenths"] for r in rows if r["run_tenths"] is not None]
    pushes = [r["pushups"] for r in rows if r["pushups"] is not None]
    return {"prev_run": pick("run_tenths"), "prev_pu": pick("pushups"),
            "best_run": min(runs) if runs else None, "best_pu": max(pushes) if pushes else None}


def _commit(cfg: Config, user: dict, entry: Entry) -> None:
    rows = repo.list_results(cfg.db_path, user["id"])
    before = _previous_values(rows, entry.test_date)
    try:
        repo.add_result(cfg.db_path, user["id"], test_date=entry.test_date, run_tenths=entry.run_tenths,
                        pushups=entry.pushups)
    except BarorError as exc:
        st.session_state["_form_errors"] = {"form": render_exception(exc)}
        return
    log_event("result_added", user_id=user["id"])
    st.session_state["_last_result"] = {
        **asdict(entry), **before,
        "pb_run": entry.run_tenths is not None and before["best_run"] is not None and entry.run_tenths < before["best_run"],
        "pb_pu": entry.pushups is not None and before["best_pu"] is not None and entry.pushups > before["best_pu"]}
    st.session_state["_pending"] = None
    st.session_state["_form_errors"] = {}
    st.session_state["_reset_form"] = True


def _on_save(cfg: Config, user: dict) -> None:
    """Save-button callback (runs before the script, so the page that follows already shows the outcome)."""
    s = st.session_state
    entry, errs = validate_form(day=s.get("add_date"), minutes=s.get("add_min"), seconds=s.get("add_sec"),
                                pushups=s.get("add_push"), today=_today(cfg), is_km=bool(user["is_km"]))
    s["_form_errors"] = errs
    if entry is None:
        return
    reasons = []
    if entry.pushups == 0:
        reasons.append("zero")
    if repo.find_duplicate(cfg.db_path, user["id"], test_date=entry.test_date, run_tenths=entry.run_tenths,
                           pushups=entry.pushups):
        reasons.append("duplicate")
    if reasons:
        s["_pending"] = {"entry": asdict(entry), "reasons": reasons}
        return
    _commit(cfg, user, entry)


def _on_confirm(cfg: Config, user: dict) -> None:
    pending = st.session_state.get("_pending")
    if pending:
        _commit(cfg, user, Entry(**pending["entry"]))


def _on_fix() -> None:
    st.session_state["_pending"] = None


def _on_another() -> None:
    st.session_state["_last_result"] = None


def _on_to(destination: str) -> None:
    st.session_state["_last_result"] = None
    nav.go(destination)


# -------------------------------------------------------------------------------------------- add result: screen
def _chips(user: dict, today: dt.date, test: str) -> None:
    th = thresholds_for(user, today, test)
    if th is None:
        return
    pass_limit, exc_limit = th
    key = "chip_atleast" if test == "pushups" else "chip_upto"
    show = str if test == "pushups" else format_deciseconds
    items = [(label, t(key, v=show(limit)), is_exc)
             for label, limit, is_exc in ((t("chip_pass"), pass_limit, False), (t("chip_excellent"), exc_limit, True))
             if limit is not None]
    components.target_chips(items)


def _result_card(cfg: Config, user: dict, last: dict) -> None:
    entry = Entry(last["test_date"], last["run_tenths"], last["pushups"])
    graded = {g.test: g for g in grade_entry(entry, user["gender"], bool(user["is_km"]))}
    aero = _aerobic(user)
    with st.container(border=True, key="result_card"):
        st.markdown(f"#### :material/check_circle: {t('saved')}")
        st.caption(components.iso(fmt_date(entry.test_date)))
        if entry.run_tenths is not None:
            extra = [vs_previous(aero, last["prev_run"], entry.run_tenths)]
            _status_block("test_walk" if aero == "walk" else "test_run", time_text(entry.run_tenths), graded[aero], extra)
            if last["pb_run"]:
                st.markdown(f":gray-badge[:material/emoji_events: {t('new_personal_best')}]")
        if entry.pushups is not None:
            extra = [vs_previous("pushups", last["prev_pu"], entry.pushups)]
            _status_block("test_pushups", str(entry.pushups), graded["pushups"], extra)
            if last["pb_pu"]:
                st.markdown(f":gray-badge[:material/emoji_events: {t('new_personal_best')}]")
        st.button(t("btn_tip_link"), icon=":material/lightbulb:", type="tertiary", on_click=_on_to, args=("more",),
                  key="tip_link")
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        st.button(t("btn_add_another"), width="stretch", icon=":material/add:", on_click=_on_another, key="again")
    with c2:
        st.button(t("btn_to_summary"), type="primary", width="stretch", icon=":material/home:", on_click=_on_to,
                  args=("summary",), key="to_summary")


def add_page(cfg: Config, user: dict) -> None:
    s = st.session_state
    st.title(t("add_title"))
    last = s.get("_last_result")
    if last:
        _result_card(cfg, user, last)
        return
    if s.pop("_reset_form", False):
        # assigned, not popped: a value set from the server reaches the browser (a popped key can keep the old choice)
        s.update({"add_date": _today(cfg), "add_min": None, "add_sec": None, "add_push": None})
    errs = s.get("_form_errors") or {}
    today = _today(cfg)
    aero = _aerobic(user)
    st.date_input(t("field_date"), value=today, max_value=today, format="DD/MM/YYYY", key="add_date")
    components.field_error(errs.get("date"))
    st.markdown(f"**{t('section_walk' if aero == 'walk' else 'section_run')}**")
    ca, cb = st.columns(2, wrap=False, gap="small")
    with ca:
        mins = _picker(t("field_minutes"), minute_options(aero), "add_min", t("ph_minutes"))
    with cb:
        secs = _picker(t("field_seconds"), SECOND_OPTIONS, "add_sec", t("ph_seconds"), fmt=lambda v: f"{v:02d}")
    if mins is not None and secs is not None:
        text = f"{int(mins):02d}:{int(secs):02d}"
        st.html(f'<p class="readback" aria-live="polite">{components.html_msg("readback", time=text)}</p>')
    else:
        st.html(f'<p class="readback readback-empty" aria-live="polite">{html.escape(t("readback_empty"))}</p>')
    components.field_error(errs.get("time"))
    _chips(user, today, aero)
    st.markdown(f"**{t('section_pushups')}**")
    _picker(t("field_pushups"), PUSHUP_OPTIONS, "add_push", t("ph_pushups"))
    components.field_error(errs.get("push"))
    _chips(user, today, "pushups")
    components.field_error(errs.get("form"))
    pending = s.get("_pending")
    if pending:
        if "zero" in pending["reasons"]:
            st.warning(t("warn_zero_pushups"), icon=":material/warning:")
        if "duplicate" in pending["reasons"]:
            st.warning(t("warn_duplicate"), icon=":material/warning:")
        c1, c2 = st.columns(2, wrap=False)
        with c1:
            st.button(t("btn_confirm_save"), type="primary", width="stretch", on_click=boundary.guarded_callback(_on_confirm, where="save"), args=(cfg, user),
                      key="confirm_save")
        with c2:
            st.button(t("btn_fix"), width="stretch", on_click=_on_fix, key="fix_entry")
        return
    if errs:
        st.caption(t("input_kept"))
    st.button(t("btn_save_result"), type="primary", width="stretch", icon=":material/save:", on_click=boundary.guarded_callback(_on_save, where="save"),
              args=(cfg, user), key="save_btn")


# --------------------------------------------------------------------------------------------------------- history
def apply_edit(cfg: Config, user: dict, rid: int, *, day, minutes, seconds, pushups) -> dict[str, str]:
    """Validate and store an edited result. Returns {} on success, else {field: message} (nothing stored)."""
    entry, errs = validate_form(day=day, minutes=minutes, seconds=seconds, pushups=pushups, today=_today(cfg),
                                is_km=bool(user["is_km"]))
    if entry is None:
        return errs
    try:
        repo.update_result(cfg.db_path, user["id"], rid, test_date=entry.test_date, run_tenths=entry.run_tenths,
                           pushups=entry.pushups)
    except NotFoundError as exc:                       # also the answer for somebody else's id
        return {"form": render_exception(exc)}
    log_event("result_updated", user_id=user["id"])
    return {}


def apply_delete(cfg: Config, user: dict, rid: int) -> None:
    try:
        repo.delete_result(cfg.db_path, user["id"], rid)
    except NotFoundError:
        pass                                           # already gone: the list is the truth
    else:
        log_event("result_deleted", user_id=user["id"])


@st.dialog(t("edit_title"))
def _edit_dialog(cfg: Config, user: dict, row: dict) -> None:
    rid, today, aero = row["id"], _today(cfg), _aerobic(user)
    day = st.date_input(t("field_date"), value=dt.date.fromisoformat(row["test_date"]), max_value=today,
                        format="DD/MM/YYYY", key=f"ed_date_{rid}")
    m0, s0 = (divmod(row["run_tenths"] // 10, 60) if row["run_tenths"] is not None else (None, None))
    ca, cb = st.columns(2, wrap=False, gap="small")
    with ca:
        mins = _picker(t("field_minutes"), minute_options(aero, m0), f"ed_min_{rid}", t("ph_minutes"), value=m0)
    with cb:
        secs = _picker(t("field_seconds"), SECOND_OPTIONS, f"ed_sec_{rid}", t("ph_seconds"), value=s0,
                       fmt=lambda v: f"{v:02d}")
    push = _picker(t("field_pushups"), PUSHUP_OPTIONS, f"ed_push_{rid}", t("ph_pushups"), value=row["pushups"])
    errs = st.session_state.get(f"_ed_errors_{rid}") or {}
    for field in ("date", "time", "push", "form"):
        components.field_error(errs.get(field))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        save = st.button(t("btn_save_changes"), type="primary", width="stretch", key=f"ed_save_{rid}")
    with c2:
        if st.button(t("btn_cancel"), width="stretch", key=f"ed_cancel_{rid}"):
            st.rerun()
    if save:
        errs = apply_edit(cfg, user, rid, day=day, minutes=mins, seconds=secs, pushups=push)
        st.session_state[f"_ed_errors_{rid}"] = errs
        if errs:
            st.rerun(scope="fragment")                   # the dialog stays open and shows the problems beside the fields
        st.session_state.pop(f"_ed_errors_{rid}", None)
        st.session_state["_notice"] = t("updated")
        st.rerun()


@st.dialog(t("delete_dialog_title"))
def _delete_dialog(cfg: Config, user: dict, row: dict) -> None:
    st.subheader(t("delete_title", date=components.iso(fmt_date(row["test_date"]))))
    st.write(t("delete_body"))
    c1, c2 = st.columns(2, wrap=False)
    with c1:
        if st.button(t("btn_delete_confirm"), type="primary", width="stretch", icon=":material/delete:",
                     key=f"del_ok_{row['id']}"):
            apply_delete(cfg, user, row["id"])
            st.session_state["_notice"] = t("deleted")
            st.rerun()
    with c2:
        if st.button(t("btn_keep"), width="stretch", key=f"del_keep_{row['id']}"):
            st.rerun()


def history_page(cfg: Config, user: dict) -> None:
    st.title(t("history_title"))
    flash = st.session_state.pop("_notice", None)
    if flash:
        st.success(flash, icon=":material/check_circle:")
    rows = repo.list_results(cfg.db_path, user["id"])
    if not rows:
        st.info(t("empty_history"))
        st.button(t("btn_first_result"), type="primary", width="stretch", icon=":material/add_circle:",
                  on_click=nav.go, args=("add",), key="history_empty_cta")      # an empty state names the next step
        return
    shown = st.session_state.setdefault("_hist_shown", HISTORY_PAGE)
    aero = _aerobic(user)
    for r in rows[:shown]:
        with st.container(border=True, key=f"row_{r['id']}"):
            st.markdown(f"**{components.iso(fmt_date(r['test_date']))}**")
            if r["run_tenths"] is not None:
                g = grade_value(aero, user["gender"], r["test_date"], r["run_tenths"])
                st.markdown(f"{t('test_walk' if aero == 'walk' else 'test_run')} {components.iso(time_text(r['run_tenths']))} "
                            f"{components.status_badge(g.level)}")
            if r["pushups"] is not None:
                g = grade_value("pushups", user["gender"], r["test_date"], r["pushups"])
                st.markdown(f"{t('test_pushups')} {components.iso(str(r['pushups']))} {components.status_badge(g.level)}")
            c1, c2 = st.columns(2, wrap=False, gap="xsmall")
            with c1:
                if st.button(t("btn_edit"), icon=":material/edit:", width="stretch", key=f"edit_{r['id']}"):
                    _edit_dialog(cfg, user, r)
            with c2:
                if st.button(t("btn_delete"), icon=":material/delete:", width="stretch", key=f"delete_{r['id']}"):
                    _delete_dialog(cfg, user, r)
    if len(rows) > shown:
        st.button(t("btn_show_older"), width="stretch", key="hist_more",
                  on_click=lambda: st.session_state.__setitem__("_hist_shown", shown + HISTORY_PAGE))
