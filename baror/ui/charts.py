"""Progress chart (T6): ONE chart at a time, run/walk or push-ups, with the pass and excellent levels drawn on it.

Pure part (no streamlit, unit-tested in tests/test_charts.py): ``points``, ``limits``, ``build_figure``, ``summary_text``,
``table_rows``. Streamlit part: ``render`` (the contract used by the summary page).

Design decisions (owner answers, UI/UX report "Second pass"):
* Faster times are drawn HIGHER (the time axis is reversed) so "up is better" holds for both charts; push-ups use the
  natural axis. A caption says so.
* The time axis is numeric seconds with explicit ``tickvals``/``ticktext`` in mm:ss (Plotly time axes take no tick
  format). Dates are dd/mm on the actual test dates, thinned when crowded.
* Levels come from the shipped rule set in force on the date of the latest point (``scoring.current_ruleset``): a
  dashed "pass" line, a dotted "excellent" line and a shaded excellent band. The walk track has no excellent level.
* Meaning is never colour alone: the personal best is a diamond with the word "שיא", the latest test is the largest
  circle; both are explained in a caption. A data table and a one-sentence summary sit under every chart (WCAG 1.1.1).

Colours (contrast on the white plot background; lines and markers need 3:1, text 4.5:1; computed in
tests/test_charts.py): data line olive #4a5c40, best marker rust #8a3b12, pass line #6b4e00, excellent line #2e6b34.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import plotly.graph_objects as go

from baror.domain import entry, scoring
from baror.domain.scoring import DomainError, Metric, Sex
from baror.messages_he import t

INK, LINE, AXIS, GRID = "#1f2a1a", "#4a5c40", "#6f7a68", "#d9d5c3"
BEST, PASS_LINE, EXC_LINE = "#8a3b12", "#6b4e00", "#2e6b34"
BAND = "rgba(46,107,52,0.16)"
PLOT_BG = "#ffffff"
FONT = "Heebo, Arial, sans-serif"
PLOTLY_CONFIG = {"displayModeBar": False, "scrollZoom": False, "doubleClick": False, "displaylogo": False}

AERO, PUSHUPS = "aero", "pushups"
MAX_X_TICKS = 5
_TIME_STEPS = (30, 60, 120, 300, 600, 900)       # seconds between y ticks
_REP_STEPS = (2, 5, 10, 20)


@dataclass(frozen=True)
class Point:
    day: dt.date
    value: int          # deciseconds (run/walk) or repetitions


# ---------------------------------------------------------------------------------------------------- data
def points(rows: list[dict], kind: str) -> list[Point]:
    """Chart points for one metric, oldest first; rows without a value for that metric are skipped."""
    key = "run_tenths" if kind == AERO else "pushups"
    out = [(dt.date.fromisoformat(r["test_date"]), r.get("id") or 0, r[key]) for r in rows if r.get(key) is not None]
    return [Point(d, v) for d, _, v in sorted(out, key=lambda x: (x[0], x[1]))]


def metric_of(kind: str, is_km: bool) -> Metric:
    return Metric.PUSHUPS if kind == PUSHUPS else Metric.aerobic_for(bool(is_km))


def limits(kind: str, gender: str, is_km: bool, on: dt.date):
    """(pass, excellent) in chart units (seconds / repetitions); excellent is None on the walk track.
    None when no rule applies (unknown gender, no rule set for the date): the chart is then drawn without levels."""
    metric = metric_of(kind, is_km)
    try:
        th = scoring.current_ruleset(on).get(metric, Sex.from_code(gender))
    except DomainError:
        return None
    unit = 10 if kind == AERO else 1
    return th.pass_limit / unit, (None if th.excellent_limit is None else th.excellent_limit / unit)


def _y(kind: str, value: int) -> float:
    return value / 10 if kind == AERO else float(value)


def fmt_seconds(seconds: float) -> str:
    """mm:ss for axis ticks and labels (minutes may exceed 59: the walk takes more than an hour)."""
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_value(kind: str, value: int) -> str:
    return scoring.format_deciseconds(value) if kind == AERO else str(value)


# ---------------------------------------------------------------------------------------------------- ticks
def y_ticks(lo: float, hi: float, kind: str):
    """(tickvals, ticktext, axis_lo, axis_hi): at most about six ticks; the axis ends ON a tick."""
    steps = _TIME_STEPS if kind == AERO else _REP_STEPS
    span = max(hi - lo, steps[0])
    step = steps[-1]
    for s in steps:
        if span / s <= 5:
            step = s
            break
    first = int(lo // step) * step
    if kind == PUSHUPS:
        first = max(0, first)
    last = int(-(-hi // step)) * step
    vals = list(range(first, last + 1, step))
    if len(vals) < 2:
        vals.append(vals[-1] + step)
    text = [fmt_seconds(v) if kind == AERO else str(v) for v in vals]
    return vals, text, vals[0], vals[-1]


def x_ticks(days: list[dt.date]) -> list[dt.date]:
    """Distinct test dates to label: all when few, else evenly spaced including the first and the last."""
    uniq = sorted(set(days))
    if len(uniq) <= MAX_X_TICKS:
        return uniq
    n = len(uniq)
    idx = sorted({round(i * (n - 1) / (MAX_X_TICKS - 1)) for i in range(MAX_X_TICKS)})
    return [uniq[i] for i in idx]


def x_range(days: list[dt.date]):
    first, last = min(days), max(days)
    if first == last:
        return [first - dt.timedelta(days=5), last + dt.timedelta(days=5)]
    pad = max(1, round((last - first).days * 0.06))
    return [first - dt.timedelta(days=pad), last + dt.timedelta(days=pad)]


# ---------------------------------------------------------------------------------------------------- figure
def best_index(kind: str, pts: list[Point]) -> int:
    """Index of the personal best: lowest time / highest repetitions; the earliest point wins a tie."""
    vals = [p.value for p in pts]
    target = min(vals) if kind == AERO else max(vals)
    return vals.index(target)


def build_figure(pts: list[Point], kind: str, lim) -> go.Figure:
    """``pts`` oldest first and not empty; ``lim`` = ``limits(...)`` result or None."""
    ys = [_y(kind, p.value) for p in pts]
    days = [p.day for p in pts]
    pass_v, exc_v = (lim if lim else (None, None))
    marks = ys + [v for v in (pass_v, exc_v) if v is not None]
    lo, hi = min(marks), max(marks)
    pad = max((hi - lo) * 0.12, 20 if kind == AERO else 2)
    tickvals, ticktext, ylo, yhi = y_ticks(lo - pad, hi + pad, kind)
    yrange = [yhi, ylo] if kind == AERO else [ylo, yhi]            # faster = higher: the time axis is reversed
    fig = go.Figure()
    annot_font = dict(size=12, family=FONT)
    if exc_v is not None:
        if kind == AERO:
            fig.add_hrect(y0=ylo, y1=exc_v, fillcolor=BAND, line_width=0, layer="below")
        else:
            fig.add_hrect(y0=exc_v, y1=yhi, fillcolor=BAND, line_width=0, layer="below")
        fig.add_hline(y=exc_v, line_width=2, line_dash="dot", line_color=EXC_LINE, annotation_position="top left",
                      annotation_text=f"{t('chart_line_excellent')} {fmt_seconds(exc_v) if kind == AERO else int(exc_v)}",
                      annotation_font=dict(color=EXC_LINE, **annot_font), annotation_bgcolor="rgba(255,255,255,0.9)")
    if pass_v is not None:
        fig.add_hline(y=pass_v, line_width=2, line_dash="dash", line_color=PASS_LINE, annotation_position="top left",
                      annotation_text=f"{t('chart_line_pass')} {fmt_seconds(pass_v) if kind == AERO else int(pass_v)}",
                      annotation_font=dict(color=PASS_LINE, **annot_font), annotation_bgcolor="rgba(255,255,255,0.9)")
    bi, last = best_index(kind, pts), len(pts) - 1
    size = [9] * len(pts)
    symbol = ["circle"] * len(pts)
    size[last] = 14                                                  # latest test: the largest circle
    size[bi], symbol[bi] = (15 if bi == last else 13), "diamond"     # personal best: a diamond (shape, not only colour)
    colour = [BEST if i == bi else LINE for i in range(len(pts))]
    label = [t("chart_best_label") if i == bi and len(pts) > 1 else "" for i in range(len(pts))]
    fig.add_trace(go.Scatter(
        x=days, y=ys, mode="lines+markers+text", text=label, textposition="top center",
        textfont=dict(size=12, color=BEST, family=FONT),
        customdata=[fmt_value(kind, p.value) for p in pts],
        line=dict(color=LINE, width=3),
        marker=dict(color=colour, size=size, symbol=symbol, line=dict(color="#ffffff", width=2)),
        hovertemplate="%{x|%d/%m/%Y}<br>%{customdata}<extra></extra>", showlegend=False, cliponaxis=False))
    ticks = x_ticks(days)
    fig.update_layout(
        height=300, margin=dict(l=52, r=16, t=20, b=44), paper_bgcolor=PLOT_BG, plot_bgcolor=PLOT_BG,
        font=dict(family=FONT, size=13, color=INK), dragmode=False, hovermode="closest",
        hoverlabel=dict(font=dict(family=FONT, size=14, color=INK), bgcolor="#ffffff", bordercolor=LINE),
        xaxis=dict(type="date", tickmode="array", tickvals=ticks, ticktext=[d.strftime("%d/%m") for d in ticks],
                   range=x_range(days), fixedrange=True, showgrid=False, linecolor=AXIS, ticks="outside",
                   tickcolor=AXIS, tickfont=dict(size=12, color=INK)),
        yaxis=dict(tickmode="array", tickvals=tickvals, ticktext=ticktext, range=yrange, fixedrange=True,
                   gridcolor=GRID, zeroline=False, tickfont=dict(size=12, color=INK)))
    return fig


# ---------------------------------------------------------------------------------------------------- text
def summary_text(pts: list[Point], kind: str, wrap=lambda s: s) -> str:
    """One sentence for the caption and the alt text. ``wrap`` isolates times/numbers (components.iso) in the UI."""
    first, last = fmt_value(kind, pts[0].value), fmt_value(kind, pts[-1].value)
    best = fmt_value(kind, pts[best_index(kind, pts)].value)
    suffix = "time" if kind == AERO else "reps"
    if len(pts) == 1:
        return t(f"chart_sum_{suffix}_one", last=wrap(last))
    return t(f"chart_sum_{suffix}", n=len(pts), first=wrap(first), last=wrap(last), best=wrap(best))


def table_rows(pts: list[Point], kind: str, gender: str, is_km: bool) -> list[dict]:
    """Newest first: date dd/mm/yyyy, value, level (text, so the table carries what the colours show)."""
    from baror.ui.format import level_text          # local import: format pulls in the domain, not streamlit
    test = "pushups" if kind == PUSHUPS else ("walk" if is_km else "run")
    value_col = t("chart_col_reps") if kind == PUSHUPS else t("chart_col_walk" if is_km else "chart_col_run")
    out = []
    for p in reversed(pts):
        g = entry.grade_value(test, gender, p.day.isoformat(), p.value)
        out.append({t("col_date"): p.day.strftime("%d/%m/%Y"), value_col: fmt_value(kind, p.value),
                    t("chart_col_status"): level_text(g.level)})
    return out


# ---------------------------------------------------------------------------------------------------- streamlit
def render(rows: list[dict], user: dict, key: str = "chart") -> None:
    """CONTRACT used by baror/ui/personal.py (summary): the progress block of the signed-in user. Draws nothing when
    there are no results at all (the summary shows its own empty state)."""
    import pandas as pd
    import streamlit as st

    from baror.ui.components import iso

    is_km = bool(user["is_km"])
    data = {AERO: points(rows, AERO), PUSHUPS: points(rows, PUSHUPS)}
    if not data[AERO] and not data[PUSHUPS]:
        return
    st.subheader(t("trend_title"))
    if st.session_state.get(f"{key}_metric") not in data:
        st.session_state[f"{key}_metric"] = AERO if data[AERO] else PUSHUPS
    kind = st.segmented_control(
        t("chart_metric_label"), list(data), key=f"{key}_metric", required=True, label_visibility="collapsed",
        width="stretch", format_func=lambda k: t("test_pushups") if k == PUSHUPS else t("test_walk" if is_km else "test_run"))
    pts = data.get(kind) or []
    if not pts:
        st.caption(t("chart_empty_metric"))
        return
    lim = limits(kind, user["gender"], is_km, pts[-1].day)
    summary = summary_text(pts, kind, wrap=iso)
    with st.container(key=f"chart_card_{key}"):
        st.plotly_chart(build_figure(pts, kind, lim), config=PLOTLY_CONFIG, theme=None, width="stretch",
                        key=f"{key}_main", alt=t("chart_alt", summary=summary_text(pts, kind)))
    st.write(summary)                                     # the text alternative of the picture: body text, not a caption
    st.caption(f"{t('chart_hint_up')} {t('chart_few') if len(pts) == 1 else t('chart_marks')}")
    with st.expander(t("chart_table")):
        st.table(pd.DataFrame(table_rows(pts, kind, user["gender"], is_km)).set_index(t("col_date")))


# ------------------------------------------------------------------------------------------ chief: platoon bars
LEVEL_COLOURS = {"excellent": "#245c2b", "pass": "#4f7d43", "fail": "#9a5b00"}     # white text on each: 7.95 / 4.83 / 5.43
LEVELS = ("excellent", "pass", "fail")


def platoon_figure(rows: list[tuple[str, dict | None]]) -> go.Figure:
    """Stacked horizontal bars, one bar per platoon: the number of cadets in each level of ONE test. ``rows`` =
    [(platoon label, block)] where block is ``{'excellent','pass','fail'}`` counts, or None for a group that the
    small-group rule hides: such a row is drawn EMPTY with a text instead of any figure. Right-to-left: bars start at
    the right edge and the platoon names are on the right. The counts are written inside the bars and the legend uses
    the status words (colour is never the only cue)."""
    labels = [label for label, _ in rows]
    fig = go.Figure()
    for level in LEVELS:
        counts = [(block or {}).get(level, 0) for _, block in rows]
        fig.add_trace(go.Bar(
            y=labels, x=counts, orientation="h", name=t("status_" + level),
            marker=dict(color=LEVEL_COLOURS[level], line=dict(color="#ffffff", width=2)),
            text=[str(c) if c else "" for c in counts], textposition="inside", insidetextanchor="middle",
            textfont=dict(color="#ffffff", size=14, family=FONT),
            hovertemplate="%{y}<br>" + t("status_" + level) + ": %{x}<extra></extra>"))
    top = max([sum(b[lv] for lv in LEVELS) for _, b in rows if b] or [1])
    for label, block in rows:
        if block is None:
            fig.add_annotation(xref="paper", x=0.5, yref="y", y=label, text=t("group_hidden"), showarrow=False,
                               font=dict(size=13, color="#454e40", family=FONT))
    fig.update_layout(
        barmode="stack", height=90 + 58 * len(rows), margin=dict(l=8, r=104, t=8, b=36), showlegend=True,
        legend=dict(orientation="h", x=1, xanchor="right", y=-0.18, yanchor="top", font=dict(size=13, color=INK, family=FONT),
                    traceorder="normal"),
        paper_bgcolor=PLOT_BG, plot_bgcolor=PLOT_BG, font=dict(family=FONT, size=13, color=INK), dragmode=False,
        xaxis=dict(autorange="reversed", range=[top + 0.5, 0], dtick=1 if top <= 12 else None, fixedrange=True,
                   gridcolor=GRID, zeroline=False, tickfont=dict(size=12, color=INK)),
        yaxis=dict(side="right", autorange="reversed", fixedrange=True,
                   tickfont=dict(size=14, color=INK, family=FONT)))
    return fig


def platoon_table(rows: list[tuple[str, dict | None]]) -> list[dict]:
    """The text alternative of the bars: one row per platoon, one column per level; a hidden group shows a dash."""
    out = []
    for label, block in rows:
        row = {t("col_platoon"): label}
        for lv in LEVELS:
            row[t("status_" + lv)] = "—" if block is None else block[lv]
        out.append(row)
    return out
