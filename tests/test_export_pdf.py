"""The PDF export (stdlib writer): file structure, text through the ToUnicode map, bidirectional layout, privacy, paging."""
import datetime as dt
import re
import unittest
import zlib

from baror import export_pdf, messages_he
from baror.export_pdf import visual_order
from baror.storage import accommodations as acc
from baror.storage import audit, db, ops, repo
from tests.v2base import V2Case

TODAY = dt.date(2026, 11, 3)
NOW = dt.datetime(2026, 11, 3, 8, 0, tzinfo=dt.timezone.utc)


def parse(pdf: bytes):
    """-> (objects {n: bytes}, text lines in the order drawn, number of pages). A tiny reader just for these tests."""
    assert pdf.startswith(b"%PDF-1.") and pdf.rstrip().endswith(b"%%EOF")
    start = int(re.search(rb"startxref\n(\d+)\n%%EOF", pdf).group(1))
    assert pdf[start:start + 4] == b"xref"
    size = int(re.search(rb"xref\n0 (\d+)\n", pdf[start:]).group(1))
    entries = re.findall(rb"(\d{10}) (\d{5}) ([nf]) \n", pdf[start:])
    assert len(entries) == size
    objects = {}
    for n, (off, _gen, kind) in enumerate(entries):
        if kind == b"n":
            at = int(off)
            assert pdf[at:].startswith(f"{n} 0 obj\n".encode()), f"xref offset of object {n} is wrong"
            objects[n] = pdf[at:pdf.index(b"endobj", at)]
    trailer = pdf[pdf.index(b"trailer"):]
    assert f"/Size {size}".encode() in trailer and b"/Root" in trailer
    streams = []
    for body in objects.values():
        m = re.search(rb"/Length (\d+) >>\nstream\n", body)
        if m:
            raw = body[m.end():m.end() + int(m.group(1))]
            assert body[m.end() + int(m.group(1)):].startswith(b"\nendstream"), "stream /Length is wrong"
            streams.append(zlib.decompress(raw) if b"/FlateDecode" in body[:m.end()] else raw)
    cmap_text = next(s.decode("ascii") for s in streams if b"beginbfchar" in s)
    to_uni = {int(a, 16): chr(int(b, 16)) for a, b in re.findall(r"<([0-9A-F]{4})> <([0-9A-F]{4})>", cmap_text)}
    lines = []
    for s in streams:
        if b"BT" in s and b"Tj" in s:
            for hexed in re.findall(rb"<([0-9A-F]+)> Tj", s):
                text = hexed.decode()
                lines.append("".join(to_uni.get(int(text[i:i + 4], 16), "�") for i in range(0, len(text), 4)))
    pages = int(re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", pdf).group(1))
    return objects, lines, pages


def data_for(**over):
    base = {"user": {"id": 1, "email": "yuval@example.com", "name": "יובל מזרחי", "gender": "M", "is_km": 0},
            "results": [{"id": 1, "test_date": "2026-10-01", "run_tenths": 9400, "pushups": 30, "created_at": "x"},
                        {"id": 2, "test_date": "2026-09-01", "run_tenths": 9500, "pushups": 21, "created_at": "x"}],
            "accommodations": [{"id": 1, "kind": "relief", "scope": "run", "start_date": "2026-11-01", "end_date": "2027-01-31",
                                "note": "כאב ברך (קל), 3 שבועות", "source": "cadet", "created_at": "x", "delete_requested": True}],
            "accessed_by": [{"date": "2026-11-02", "reader": "רינה מדריכה", "reads": 3}, {"date": "2026-11-01", "reader": None, "reads": 1}]}
    base.update(over)
    return base


class Bidi(unittest.TestCase):
    def test_a_hebrew_word_is_reversed_for_drawing_from_the_left(self):
        self.assertEqual(visual_order("אבג"), "גבא")

    def test_digits_times_and_latin_keep_their_own_direction_inside_hebrew(self):
        self.assertEqual(visual_order("שלום 15:40"), "15:40 םולש")
        self.assertEqual(visual_order("תאריך 03/11/2026"), "03/11/2026 ךיראת")
        self.assertEqual(visual_order("אימייל: a@b.com"), "a@b.com :ליימיא")

    def test_brackets_are_mirrored_in_right_to_left_text(self):
        self.assertEqual(visual_order("(קל)"), "(לק)")
        self.assertEqual(visual_order("ב-(3)"), "(3)-ב")

    def test_empty_and_neutral_only(self):
        self.assertEqual(visual_order(""), "")
        self.assertEqual(visual_order("..."), "...")

    def test_controls_are_removed_from_data(self):
        self.assertEqual(export_pdf.clean("דנה\u202Eכהן\u2066x\n\ty\x00z"), "דנהכהןx  yz")


class Font(unittest.TestCase):
    def test_the_font_covers_every_character_the_document_can_contain(self):
        font = export_pdf.load_font()
        keys = [k for k in messages_he.M if k.startswith(("pdf_", "acc_", "status_", "fail_", "gap_", "col_", "test_", "scale_"))]
        text = "".join(messages_he.M[k] for k in keys) + "0123456789:/.-@ ()abcxyz ABC" + "—"
        missing = sorted({ch for ch in text if ch not in "{}" and not font.has(ch) and not ch.isspace() and not ch.isalpha()
                          or (ch.isalpha() and not font.has(ch))})
        self.assertEqual(missing, [], "these characters would be drawn as the empty-box glyph")

    def test_variable_font_default_instance_and_metrics_are_sane(self):
        font = export_pdf.load_font()
        self.assertEqual(font.upem, 2048)
        self.assertGreater(font.advance(font.gid("א")), 300)
        self.assertGreater(font.width("שלום", 10), 10)


class Structure(unittest.TestCase):
    def test_a_valid_document_with_hebrew_text_through_the_unicode_map(self):
        pdf = export_pdf.build_pdf(data_for(), today=TODAY)
        objects, lines, pages = parse(pdf)
        self.assertEqual(pages, 1)
        text = "\n".join(lines)
        for expected in (messages_he.t("pdf_title"), "יובל מזרחי", messages_he.t("pdf_section_results"),
                         messages_he.t("pdf_section_accommodations"), messages_he.t("pdf_section_views"), "רינה מדריכה",
                         "כאב ברך (קל), 3 שבועות", "הצוער"):         # (long cells wrap, so check a word of the source label)
            self.assertIn(visual_order(expected), text, expected)
        for plain in ("15:40", "01/10/2026", "yuval@example.com"):
            self.assertIn(plain, text)
        self.assertIn(visual_order(messages_he.t("pdf_page", n=1, total=1)), text)
        self.assertTrue(any(b"/FontFile2" in o for o in objects.values()))
        self.assertTrue(any(b"/Identity-H" in o and b"/ToUnicode" in o for o in objects.values()))

    def test_statuses_and_gaps_are_the_apps_words_and_never_an_overall_verdict(self):
        text = "\n".join(parse(export_pdf.build_pdf(data_for(), today=TODAY))[1])
        for word in ("נכשל", "עובר", "כשיר"):
            self.assertNotIn(visual_order(word), text)
        self.assertIn(visual_order("טרם הושג הרף — חסרות 10 שניות"), text)          # 15:50 against a pass limit of 15:40
        self.assertIn(visual_order(messages_he.t("status_excellent")), "\n".join(parse(export_pdf.build_pdf(data_for(
            results=[{"id": 1, "test_date": "2026-10-01", "run_tenths": 7000, "pushups": 40}]), today=TODAY))[1]))

    def test_output_is_deterministic_for_the_same_input(self):
        self.assertEqual(export_pdf.build_pdf(data_for(), today=TODAY), export_pdf.build_pdf(data_for(), today=TODAY))

    def test_an_empty_account_still_builds_with_the_empty_state_sentences(self):
        pdf = export_pdf.build_pdf(data_for(results=[], accommodations=[], accessed_by=[]), today=TODAY)
        _, lines, pages = parse(pdf)
        text = "\n".join(lines)
        self.assertEqual(pages, 1)
        for key in ("pdf_no_results", "pdf_no_accommodations", "pdf_no_views"):
            self.assertIn(visual_order(messages_he.t(key)), text)

    def test_a_long_list_paginates_and_repeats_the_header_and_numbers_the_pages(self):
        rows = [{"id": i, "test_date": (dt.date(2026, 10, 1) - dt.timedelta(days=i)).isoformat(), "run_tenths": 9000 + i, "pushups": 20}
                for i in range(1, 121)]
        pdf = export_pdf.build_pdf(data_for(results=rows), today=TODAY)
        _, lines, pages = parse(pdf)
        self.assertGreaterEqual(pages, 4)
        text = "\n".join(lines)
        self.assertGreaterEqual(text.count(visual_order(messages_he.t("pdf_col_run_status"))), 3, "header repeated on the pages of the table")
        self.assertIn(visual_order(messages_he.t("pdf_page", n=pages, total=pages)), text)

    def test_walk_track_uses_the_walk_heading_and_the_track_line(self):
        d = data_for()
        d["user"] = dict(d["user"], is_km=1, gender="F")
        text = "\n".join(parse(export_pdf.build_pdf(d, today=TODAY))[1])
        self.assertIn(visual_order(messages_he.t("test_walk")), text)
        self.assertIn(visual_order(messages_he.t("scale_line", scale=messages_he.t("scale_walk", gender=messages_he.t("scale_female")))), text)


class Privacy(unittest.TestCase):
    def test_only_the_listed_fields_are_ever_drawn(self):
        d = data_for()
        d["user"] = dict(d["user"], pw_hash="scrypt$SECRETHASH", role="admin", platoon_id=7, created_at="2026-01-01T00:00:00Z")
        d["accommodations"][0]["created_by"] = 99
        d["accommodations"][0]["delete_requested_by"] = "Hidden Requester"
        raw = export_pdf.build_pdf(d, today=TODAY)
        text = "\n".join(parse(raw)[1])
        for secret in ("SECRETHASH", "scrypt", "admin", "Hidden", "99"):
            self.assertNotIn(secret, text)
        self.assertNotIn(b"SECRETHASH", raw)

    def test_hostile_text_cannot_inject_pdf_syntax_or_reorder_the_page(self):
        evil = "x) Tj ET /F1 99 Tf (\\ >> endobj \u202E\u2066abc"
        d = data_for()
        d["user"] = dict(d["user"], name=evil)
        d["accessed_by"] = [{"date": "2026-11-02", "reader": evil, "reads": 1}]
        raw = export_pdf.build_pdf(d, today=TODAY)
        parse(raw)                                                        # still a structurally valid file
        text = "\n".join(parse(raw)[1])
        self.assertNotIn("\u202E", text)
        self.assertNotIn(b"endobj \xe2", raw)


class FromTheDatabase(V2Case):
    def test_the_export_of_one_cadet_contains_nothing_of_another(self):
        pa = self.platoon("A")
        me = self.cadet("me@x.com", pa, name="Alef Me")
        other = self.cadet("other@secret.example", pa, name="Zed Other")
        ins = self.staff("i@x.com", platoons=[pa], name="Ins Reader")
        repo.add_result(self.path, me, test_date="2026-10-01", run_tenths=9400, pushups=30)
        repo.add_result(self.path, other, test_date="2026-10-02", run_tenths=7000, pushups=77)
        acc.add(self.path, me, me, kind="relief", scope="run", start_date="2026-11-01", end_date="2026-12-01", note="mine", now=NOW, today=TODAY)
        acc.add(self.path, ins, other, kind="exemption", scope="both", start_date="2026-11-01", end_date="2026-12-01",
                note="theirs", now=NOW, today=TODAY)
        from baror.storage import cohort
        cohort.get_cadet_results(self.path, ins, me, now=NOW)
        text = "\n".join(parse(export_pdf.build_pdf(repo.export_user_data(self.path, me), today=TODAY))[1])
        self.assertIn("Alef Me", text)
        self.assertIn("me@x.com", text)
        self.assertIn("Ins Reader", text)
        self.assertIn("mine", text)
        for foreign in ("other@secret.example", "Zed Other", "theirs", "77", "07:00"):
            self.assertNotIn(foreign, text)
        self.assertNotIn("scrypt", text)


if __name__ == "__main__":
    unittest.main()
