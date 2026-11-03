"""The aggregated period report (weekly / monthly) as a PDF: Hebrew, right to left, embedded Heebo, searchable. Standard
library only; never imports streamlit. It reuses the writer of ``baror.export_pdf`` (font, bidi, tables, paging) and adds
only the content.

    build_report_pdf(data, generated=date) -> bytes     ``data`` is what ``baror.storage.reports.compute`` returns.

The data already carries the small-group rule (a hidden figure is None or {'suppressed': True}); this module never
recomputes or reveals anything, it only prints the text for a hidden figure instead of a number. No cadet names: the data
has none. Platoon names are fixed strings chosen by the operator and are drawn like any other text (as glyphs, never as PDF
syntax). Output is deterministic for the same data and date.
"""
from __future__ import annotations

import datetime as dt

from baror.domain.scoring import format_deciseconds
from baror.export_pdf import MUTED, OLIVE, _Doc, _date, load_font
from baror.messages_he import APP_NAME, month_label, t

_TESTS = ("run", "walk", "pushups")
_TEST_KEY = {"run": "test_run", "walk": "test_walk", "pushups": "test_pushups"}
_SCOPES = ("run", "pushups", "both")
_KINDS = ("relief", "exemption")
_TEST_WIDTHS = [72, 42, 56, 56, 56, 56, 56, 101]
_ACC_WIDTHS = [63, 144, 144, 144]


def _count(value) -> str:
    return t("report_hidden") if value is None else str(value)


def _time(tenths) -> str:
    return format_deciseconds(int(tenths))


def _reps(value) -> str:
    return f"{value:.1f}"


def _change_text(test: str, change) -> str:
    if change is None:
        return t("report_omitted")
    if test == "pushups":
        if change == 0:
            return t("report_change_same")
        return t("report_change_more" if change > 0 else "report_change_less", v=_reps(abs(change)))
    if change == 0:
        return t("report_change_same")
    return t("report_change_slower" if change > 0 else "report_change_faster", v=_time(abs(change)))


def _test_rows(doc: _Doc, tests: dict) -> None:
    """One table with a row per SHOWN test, then one line per hidden test."""
    rows, hidden = [], []
    for m in _TESTS:
        b = tests[m]
        if b["suppressed"]:
            hidden.append(m)
            continue
        fmt = _reps if m == "pushups" else _time
        rows.append([t(_TEST_KEY[m]), str(b["n"]), str(b["fail"]), str(b["pass"]), str(b["excellent"]),
                     fmt(b["avg"]), str(b["best"]) if m == "pushups" else _time(b["best"]), _change_text(m, b["change"])])
    if rows:
        doc.table([t("report_col_test"), t("report_col_n"), t("col_fail"), t("col_pass"), t("col_excellent"),
                   t("report_col_avg"), t("report_col_best"), t("report_col_change")], rows, _TEST_WIDTHS)
    for m in hidden:
        doc.paragraph(t("report_test_hidden", test=t(_TEST_KEY[m])), 9.5, color=MUTED, gap=1)
    if hidden:
        doc.y -= 6


def _accommodations(doc: _Doc, acc: dict) -> None:
    doc.keep(80)
    doc.paragraph(t("report_section_acc"), 11, bold=True, color=OLIVE, gap=4)
    if all(acc[k][s] is None for k in _KINDS for s in _SCOPES):
        doc.paragraph(t("report_acc_hidden"), 9.5, color=MUTED)
        return
    rows = [[t("acc_kind_" + k), *[_count(acc[k][s]) for s in _SCOPES]] for k in _KINDS]
    doc.table([t("report_acc_col_kind"), *[t("acc_scope_" + s) for s in _SCOPES]], rows, _ACC_WIDTHS)


def _headline(doc: _Doc, block: dict) -> None:
    for key, field in (("report_line_cadets", "cadets"), ("report_line_tested", "tested"),
                       ("report_line_results", "results")):
        doc.paragraph(t(key, v=_count(block[field])), 10.5, gap=1)
    doc.y -= 6


def build_report_pdf(data: dict, *, generated: dt.date) -> bytes:
    kind, period = data["kind"], data["period"]
    start, end = dt.date.fromisoformat(period["start"]), dt.date.fromisoformat(period["end"])
    doc = _Doc(load_font())
    title = t("report_pdf_title_" + kind)
    doc.paragraph(title, 20, bold=True, color=OLIVE, gap=2)
    doc.paragraph(t("report_pdf_course", name=APP_NAME), 11, gap=1)
    if kind == "monthly":
        doc.paragraph(t("report_pdf_month", label=month_label(start)), 11, gap=1)
    doc.paragraph(t("report_pdf_period", start=_date(period["start"]), end=_date(period["end"])), 11, gap=1)
    doc.paragraph(t("report_pdf_data_until", date=end.strftime("%d/%m/%Y")), 10, gap=1)
    doc.paragraph(t("pdf_generated", date=generated.strftime("%d/%m/%Y")), 9.5, color=MUTED, gap=10)
    if data["empty"]:
        doc.paragraph(t("report_empty"), 10.5, bold=True, gap=8)

    doc.keep(140)
    doc.paragraph(t("report_pdf_section_course"), 14, bold=True, color=OLIVE, gap=6)
    _headline(doc, data["total"])
    _test_rows(doc, data["total"]["tests"])
    _accommodations(doc, data["total"]["accommodations"])

    for p in data["platoons"]:
        name = p["platoon"] if p["platoon"] is not None else t("unassigned")
        if p["suppressed"]:
            doc.paragraph(t("report_platoon_hidden", platoon=name), 10.5, color=MUTED, gap=3)
            continue
        doc.keep(160)
        doc.paragraph(t("report_pdf_section_platoon", platoon=name), 14, bold=True, color=OLIVE, gap=6)
        _headline(doc, p)
        _test_rows(doc, p["tests"])
        _accommodations(doc, p["accommodations"])

    doc.keep(150)
    doc.paragraph(t("report_section_notes"), 12, bold=True, color=OLIVE, gap=4)
    notes = [t("report_note_cohort"), t("report_note_latest"), t("report_note_" + kind), t("report_note_small"),
             t("report_note_change", start=_date(period["previous_start"]), end=_date(period["previous_end"])),
             t("report_note_acc"), t("report_note_grading")]
    for note in notes:
        doc.paragraph(note, 9, color=MUTED, gap=3)
    return doc.finish(title, generated)
