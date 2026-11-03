"""Progress charts (original two charts; owner answer 9: faster times are drawn higher)."""
from __future__ import annotations

import pandas as pd
import plotly.express as px

from baror.messages_he import t

_GREEN = "#4a5c40"


def _style(fig, ytitle: str, reverse: bool):
    fig.update_traces(line_color=_GREEN, marker=dict(size=10, color=_GREEN))
    fig.update_layout(xaxis_title=t("axis_date"), yaxis_title=ytitle, plot_bgcolor="rgba(255,255,255,0.8)",
                      paper_bgcolor="rgba(255,255,255,0.8)", margin=dict(l=0, r=0, t=10, b=0))
    fig.update_xaxes(type="category")                      # one tick per test date
    if reverse:
        fig.update_yaxes(autorange="reversed")             # lower time = faster = higher on the page
    return fig


def run_chart(rows: list[dict], is_km: bool):
    pts = sorted(((r["test_date"], r["run_tenths"] / 600) for r in rows if r["run_tenths"] is not None))
    if not pts:
        return None
    df = pd.DataFrame(pts, columns=["date", "minutes"])
    return _style(px.line(df, x="date", y="minutes", markers=True), t("axis_minutes"), reverse=True)


def pushups_chart(rows: list[dict]):
    pts = sorted(((r["test_date"], r["pushups"]) for r in rows if r["pushups"] is not None))
    if not pts:
        return None
    df = pd.DataFrame(pts, columns=["date", "pushups"])
    return _style(px.line(df, x="date", y="pushups", markers=True), t("axis_reps"), reverse=False)
