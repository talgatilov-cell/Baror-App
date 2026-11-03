"""The cadet's own data as a PDF (Hebrew, right to left). Standard library only; never imports streamlit.

    build_pdf(data, today=date) -> bytes        ``data`` is what ``baror.storage.repo.export_user_data`` returns.

Why a hand-written writer: the project allows no third-party package, and a PDF is a small, well-defined format.
What is inside, and what was decided:

* FONT. Heebo (SIL OFL, the licence file stays in static/fonts) is read from the self-hosted WOFF the web app already
  serves, unpacked with ``zlib`` into a plain TrueType file and embedded WHOLE as a CIDFontType2 with Identity-H
  encoding (the glyph ids are the text). A ``ToUnicode`` CMap makes the text searchable and copyable. The font is a
  variable font; viewers draw its default instance (weight 400). "Bold" for headings is the same glyphs drawn with a thin
  outline (text rendering mode 2), because the weight axis is not instantiated here.
* BIDI. Hebrew has no shaping, but a line mixes Hebrew with digits, times (15:40), dates, e-mail addresses and
  parentheses. ``visual_order`` implements the part of the Unicode bidirectional algorithm this needs (weak types W1-W7,
  neutrals N1-N2, levels I1-I2, reordering L2, trailing white space L1, mirroring of brackets) for a right-to-left
  paragraph with no explicit embeddings. Bidirectional control characters in the DATA are dropped first, so a name cannot
  reorder the page around it.
* NO INJECTION. Every string reaches the file as a hexadecimal glyph string inside a content stream; no data value is
  ever written as PDF syntax. Characters the font lacks are drawn as its .notdef glyph. The e-mail address of the account
  owner is included (it is their own data); the password hash and ``created_by`` never are (``export_user_data`` does
  not return them, and this module reads only the fields it lists).
* LAYOUT. A4, 50 pt margins, tables whose first column is at the right, rows that wrap inside their cell, a header row
  repeated on each page, "page n of N" in the footer. Output is deterministic for the same input and ``today``.
"""
from __future__ import annotations

import datetime as dt
import functools
import struct
import unicodedata
import zlib
from pathlib import Path
from typing import Iterable

from baror.domain.entry import grade_value
from baror.domain.scoring import format_deciseconds
from baror.hebrew import count_phrase, duration_phrase
from baror.messages_he import t

FONT_WOFF = Path(__file__).resolve().parent.parent / "static" / "fonts" / "Heebo-VF-subset.woff"

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 50.0
INK, OLIVE, MUTED, SAND, RULE = (0.12, 0.16, 0.10), (0.29, 0.36, 0.25), (0.27, 0.31, 0.25), (0.91, 0.90, 0.83), (0.62, 0.64, 0.58)

# bidirectional control characters and zero-width marks: dropped from data (they would reorder or hide text)
_STRIP = set("​‌‍‎‏‪‫‬‭‮⁠⁦⁧⁨⁩﻿")
_MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<", "«": "»", "»": "«"}


# ------------------------------------------------------------------------------------------------------------ font
def _woff_to_sfnt(woff: bytes) -> bytes:
    """Unpack a WOFF 1.0 container (per-table zlib) into the equivalent TrueType (sfnt) file."""
    if woff[:4] != b"wOFF":
        raise ValueError("not a WOFF file")
    flavor, _length, count = struct.unpack(">4sIH", woff[4:14])
    tables = []
    for i in range(count):
        tag, offset, comp, orig, checksum = struct.unpack(">4sIIII", woff[44 + 20 * i:64 + 20 * i])
        blob = woff[offset:offset + comp]
        if comp < orig:
            blob = zlib.decompress(blob)
        if len(blob) != orig:
            raise ValueError(f"WOFF table {tag!r} has the wrong size")
        tables.append((tag, checksum, blob))
    tables.sort(key=lambda x: x[0])
    entry_selector = max(0, count.bit_length() - 1)
    search_range = (1 << entry_selector) * 16
    out = bytearray(struct.pack(">4sHHHH", flavor, count, search_range, entry_selector, count * 16 - search_range))
    offset = 12 + 16 * count
    body = bytearray()
    for tag, checksum, blob in tables:
        out += struct.pack(">4sIII", tag, checksum, offset + len(body), len(blob))
        body += blob + b"\0" * (-len(blob) % 4)
    return bytes(out + body)


class Font:
    """The few TrueType facts the writer needs: units per em, advances, ascent, descent, bbox and the cmap."""

    def __init__(self, sfnt: bytes):
        self.data = sfnt
        count = struct.unpack(">H", sfnt[4:6])[0]
        self.tables = {}
        for i in range(count):
            tag, _cs, off, ln = struct.unpack(">4sIII", sfnt[12 + 16 * i:28 + 16 * i])
            self.tables[tag.decode("latin-1")] = sfnt[off:off + ln]
        head, hhea, os2 = self.tables["head"], self.tables["hhea"], self.tables["OS/2"]
        self.upem = struct.unpack(">H", head[18:20])[0]
        self.bbox = struct.unpack(">hhhh", head[36:44])
        self.ascent, self.descent = struct.unpack(">hh", hhea[4:8])
        self.cap_height = struct.unpack(">h", os2[88:90])[0] if len(os2) >= 90 else self.ascent
        n_metrics = struct.unpack(">H", hhea[34:36])[0]
        hmtx = self.tables["hmtx"]
        self._advances = [struct.unpack(">H", hmtx[4 * i:4 * i + 2])[0] for i in range(n_metrics)]
        self._cmap = self._read_cmap(self.tables["cmap"])

    @staticmethod
    def _read_cmap(cmap: bytes) -> dict[int, int]:
        n = struct.unpack(">H", cmap[2:4])[0]
        best = None
        for i in range(n):
            platform, encoding, offset = struct.unpack(">HHI", cmap[4 + 8 * i:12 + 8 * i])
            fmt = struct.unpack(">H", cmap[offset:offset + 2])[0]
            if (platform, encoding) in ((3, 10), (0, 4), (0, 6)) and fmt == 12:
                best = (offset, fmt)
                break
            if (platform, encoding) in ((3, 1), (0, 3), (0, 4)) and fmt == 4 and best is None:
                best = (offset, fmt)
        if best is None:
            raise ValueError("the font has no Unicode cmap")
        offset, fmt = best
        mapping: dict[int, int] = {}
        if fmt == 12:
            groups = struct.unpack(">I", cmap[offset + 12:offset + 16])[0]
            for g in range(groups):
                start, end, gid = struct.unpack(">III", cmap[offset + 16 + 12 * g:offset + 28 + 12 * g])
                for cp in range(start, end + 1):
                    mapping[cp] = gid + cp - start
            return mapping
        seg = struct.unpack(">H", cmap[offset + 6:offset + 8])[0] // 2
        base = offset + 14
        ends = struct.unpack(f">{seg}H", cmap[base:base + 2 * seg])
        starts = struct.unpack(f">{seg}H", cmap[base + 2 * seg + 2:base + 4 * seg + 2])
        deltas = struct.unpack(f">{seg}h", cmap[base + 4 * seg + 2:base + 6 * seg + 2])
        range_base = base + 6 * seg + 2
        ranges = struct.unpack(f">{seg}H", cmap[range_base:range_base + 2 * seg])
        for i in range(seg):
            for cp in range(starts[i], ends[i] + 1):
                if cp == 0xFFFF:
                    continue
                if ranges[i] == 0:
                    gid = (cp + deltas[i]) & 0xFFFF
                else:
                    at = range_base + 2 * i + ranges[i] + 2 * (cp - starts[i])
                    gid = struct.unpack(">H", cmap[at:at + 2])[0]
                    if gid:
                        gid = (gid + deltas[i]) & 0xFFFF
                if gid:
                    mapping[cp] = gid
        return mapping

    def gid(self, ch: str) -> int:
        return self._cmap.get(ord(ch), 0)

    def advance(self, gid: int) -> float:
        """Advance in 1/1000 em."""
        units = self._advances[min(gid, len(self._advances) - 1)]
        return units * 1000.0 / self.upem

    def width(self, text: str, size: float) -> float:
        return sum(self.advance(self.gid(ch)) for ch in text) * size / 1000.0

    def has(self, ch: str) -> bool:
        return ord(ch) in self._cmap


@functools.lru_cache(maxsize=1)
def load_font() -> Font:
    return Font(_woff_to_sfnt(FONT_WOFF.read_bytes()))


# ------------------------------------------------------------------------------------------------------------ bidi
def clean(text) -> str:
    """Data text safe to draw: no bidirectional controls, no control characters, no line breaks (one space instead)."""
    out = []
    for ch in str(text):
        if ch in _STRIP:
            continue
        if ch in "\r\n\t":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cs", "Co", "Cn"):
            continue
        else:
            out.append(ch)
    return "".join(out)


def visual_order(text: str) -> str:
    """The characters of one line of a right-to-left paragraph, in the order they are drawn from the LEFT edge."""
    if not text:
        return text
    types = [unicodedata.bidirectional(ch) for ch in text]
    types = [("ON" if tp in ("LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI") else tp) for tp in types]
    sos = "R"
    n = len(types)
    for i in range(n):                                              # W1
        if types[i] == "NSM":
            types[i] = types[i - 1] if i else sos
    last = sos
    for i in range(n):                                              # W2 and W3
        if types[i] in ("L", "R", "AL"):
            last = types[i]
        elif types[i] == "EN" and last == "AL":
            types[i] = "AN"
    types = ["R" if tp == "AL" else tp for tp in types]
    for i in range(1, n - 1):                                       # W4
        if types[i] == "ES" and types[i - 1] == "EN" and types[i + 1] == "EN":
            types[i] = "EN"
        elif types[i] == "CS" and types[i - 1] == types[i + 1] and types[i - 1] in ("EN", "AN"):
            types[i] = types[i - 1]
    i = 0
    while i < n:                                                    # W5: a run of ET next to EN becomes EN
        if types[i] == "ET":
            j = i
            while j < n and types[j] == "ET":
                j += 1
            if (i > 0 and types[i - 1] == "EN") or (j < n and types[j] == "EN"):
                for k in range(i, j):
                    types[k] = "EN"
            i = j
        else:
            i += 1
    types = ["ON" if tp in ("ES", "ET", "CS") else tp for tp in types]   # W6
    last = sos
    for i in range(n):                                              # W7
        if types[i] in ("L", "R"):
            last = types[i]
        elif types[i] == "EN" and last == "L":
            types[i] = "L"
    ni = {"B", "S", "WS", "ON", "BN"}
    i = 0
    while i < n:                                                    # N1 and N2 (embedding direction R)
        if types[i] in ni:
            j = i
            while j < n and types[j] in ni:
                j += 1
            strong = lambda tp: "R" if tp in ("R", "EN", "AN") else "L"
            before = strong(types[i - 1]) if i else sos
            after = strong(types[j]) if j < n else sos
            for k in range(i, j):
                types[k] = before if before == after else "R"
            i = j
        else:
            i += 1
    levels = [{"R": 1, "L": 2, "EN": 2, "AN": 2}[tp] for tp in types]          # I1 and I2 (base level 1)
    k = n
    while k > 0 and unicodedata.bidirectional(text[k - 1]) in ("WS", "S", "B"):    # L1: trailing white space
        k -= 1
        levels[k] = 1
    order = list(range(n))
    top = max(levels)
    for lvl in range(top, 0, -1):                                   # L2: lowest odd level is 1
        i = 0
        while i < n:
            if levels[order[i]] >= lvl:
                j = i
                while j < n and levels[order[j]] >= lvl:
                    j += 1
                order[i:j] = reversed(order[i:j])
                i = j
            else:
                i += 1
    out = []
    for idx in order:
        ch = text[idx]
        out.append(_MIRROR.get(ch, ch) if levels[idx] % 2 == 1 else ch)           # L4: mirror in right-to-left runs
    return "".join(out)


# ------------------------------------------------------------------------------------------------------- pdf objects
class _Writer:
    def __init__(self):
        self.objects: list[bytes] = []

    def add(self, body: bytes) -> int:
        self.objects.append(body)
        return len(self.objects)

    def reserve(self) -> int:
        self.objects.append(b"")
        return len(self.objects)

    def set(self, number: int, body: bytes) -> None:
        self.objects[number - 1] = body

    def stream(self, dictionary: str, data: bytes, compress: bool = True) -> bytes:
        payload = zlib.compress(data, 9) if compress else data
        extra = " /Filter /FlateDecode" if compress else ""
        return f"<< {dictionary}{extra} /Length {len(payload)} >>\nstream\n".encode() + payload + b"\nendstream"

    def render(self, root: int, info: int) -> bytes:
        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(self.objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(self.objects) + 1}\n".encode() + b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(self.objects) + 1} /Root {root} 0 R /Info {info} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        return bytes(out)


def _hex_utf16(text: str) -> str:
    return "<FEFF" + text.encode("utf-16-be").hex().upper() + ">"


def _fmt(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") or "0"


# --------------------------------------------------------------------------------------------------------- the page
class _Doc:
    """Collects the drawing operations of every page; ``finish`` writes the file."""

    def __init__(self, font: Font):
        self.font = font
        self.pages: list[list[str]] = []
        self.used: dict[int, str] = {}                   # glyph id -> a character it stands for (ToUnicode)
        self.y = 0.0
        self.new_page()

    def new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    @property
    def ops(self) -> list[str]:
        return self.pages[-1]

    def keep(self, height: float) -> None:
        """Start a new page unless ``height`` points still fit (a section heading is never left alone at a page end)."""
        if not self.room(height):
            self.new_page()

    def room(self, height: float) -> bool:
        return self.y - height >= MARGIN + 18            # keep the footer clear

    # ---- text
    def _glyphs(self, text: str) -> str:
        parts = []
        for ch in visual_order(text):
            gid = self.font.gid(ch)
            if gid and gid not in self.used:
                self.used[gid] = ch
            parts.append(f"{gid:04X}")
        return "".join(parts)

    def draw(self, text: str, x_right: float, y: float, size: float, *, bold: bool = False, color=INK,
             ops: list[str] | None = None, align: str = "right", x_left: float | None = None) -> None:
        text = clean(text)
        if not text:
            return
        width = self.font.width(text, size)
        if align == "center" and x_left is not None:
            x = x_left + (x_right - x_left - width) / 2
        else:
            x = x_right - width
        r, g, b = color
        # line width and stroke colour are graphics-state operators: they belong OUTSIDE the text object (BT ... ET)
        pre = f"{_fmt(size * 0.025)} w {r:.3f} {g:.3f} {b:.3f} RG " if bold else ""
        mode = "2 Tr " if bold else "0 Tr "
        (self.ops if ops is None else ops).append(
            f"{pre}BT {r:.3f} {g:.3f} {b:.3f} rg {mode}/F1 {_fmt(size)} Tf {_fmt(x)} {_fmt(y)} Td <{self._glyphs(text)}> Tj ET")

    def wrap(self, text: str, width: float, size: float) -> list[str]:
        """Greedy line breaking on spaces by measured width; a word wider than the line is cut by characters."""
        text = clean(text).strip()
        if not text:
            return [""]
        lines, current = [], ""
        for word in text.split(" "):
            trial = word if not current else current + " " + word
            if self.font.width(trial, size) <= width:
                current = trial
                continue
            if current:
                lines.append(current)
                current = ""
            while self.font.width(word, size) > width and len(word) > 1:
                cut = len(word) - 1
                while cut > 1 and self.font.width(word[:cut], size) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
        return lines

    # ---- blocks
    def paragraph(self, text: str, size: float = 10.5, *, bold: bool = False, color=INK, gap: float = 4.0) -> None:
        for line in self.wrap(text, PAGE_W - 2 * MARGIN, size):
            leading = size * 1.45
            if not self.room(leading):
                self.new_page()
            self.y -= leading
            self.draw(line, PAGE_W - MARGIN, self.y, size, bold=bold, color=color)
        self.y -= gap

    def table(self, headers: list[str], rows: list[list[str]], widths: list[float], size: float = 9.0) -> None:
        """Columns run right to left: ``headers[0]`` is the rightmost. ``widths`` must add up to the text width."""
        pad, leading = 4.0, size * 1.4
        right = PAGE_W - MARGIN

        def layout(cells):
            return [self.wrap(c, w - 2 * pad, size) for c, w in zip(cells, widths)]

        def paint(cells_lines, header: bool):
            height = max(len(x) for x in cells_lines) * leading + 2 * pad - (leading - size)
            if not self.room(height):
                self.new_page()
                paint(layout(headers), True) if not header else None
            top = self.y
            if header:
                self.ops.append(f"{SAND[0]:.3f} {SAND[1]:.3f} {SAND[2]:.3f} rg {_fmt(MARGIN)} {_fmt(top - height)} "
                                f"{_fmt(PAGE_W - 2 * MARGIN)} {_fmt(height)} re f")
            x = right
            for lines, w in zip(cells_lines, widths):
                for k, line in enumerate(lines):
                    base = top - pad - size - k * leading + 1.5
                    self.draw(line, x - pad, base, size, bold=header, color=INK)
                x -= w
            self.y = top - height
            self.ops.append(f"{RULE[0]:.3f} {RULE[1]:.3f} {RULE[2]:.3f} RG 0.5 w {_fmt(MARGIN)} {_fmt(self.y)} m "
                            f"{_fmt(PAGE_W - MARGIN)} {_fmt(self.y)} l S")

        paint(layout(headers), True)
        for row in rows:
            paint(layout(row), False)
        self.y -= 8

    # ---- output
    def finish(self, title: str, today: dt.date) -> bytes:
        w = _Writer()
        catalog, pages_obj, info = w.reserve(), w.reserve(), w.reserve()
        font_obj, cid_obj, desc_obj, file_obj, tounicode_obj = w.reserve(), w.reserve(), w.reserve(), w.reserve(), w.reserve()
        total = len(self.pages)
        for n, ops in enumerate(self.pages, start=1):
            footer = visual_page_label(n, total)
            self.draw(footer, PAGE_W / 2 + 60, 28, 8.5, color=MUTED, ops=ops, align="center", x_left=PAGE_W / 2 - 60)
        # (the footer is drawn before the glyph tables are built, so its glyphs are included)
        font = self.font
        w.set(file_obj, w.stream(f"/Length1 {len(font.data)}", font.data))
        scale = 1000.0 / font.upem
        widths = " ".join(f"{gid} [{_fmt(font.advance(gid))}]" for gid in sorted(self.used))
        w.set(desc_obj, (f"<< /Type /FontDescriptor /FontName /Heebo /Flags 4 /FontBBox [{_fmt(font.bbox[0] * scale)} "
                         f"{_fmt(font.bbox[1] * scale)} {_fmt(font.bbox[2] * scale)} {_fmt(font.bbox[3] * scale)}] "
                         f"/ItalicAngle 0 /Ascent {_fmt(font.ascent * scale)} /Descent {_fmt(font.descent * scale)} "
                         f"/CapHeight {_fmt(font.cap_height * scale)} /StemV 80 /FontFile2 {file_obj} 0 R >>").encode())
        w.set(cid_obj, (f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Heebo /CIDSystemInfo << /Registry (Adobe) "
                        f"/Ordering (Identity) /Supplement 0 >> /FontDescriptor {desc_obj} 0 R /CIDToGIDMap /Identity "
                        f"/DW 500 /W [{widths}] >>").encode())
        w.set(font_obj, (f"<< /Type /Font /Subtype /Type0 /BaseFont /Heebo /Encoding /Identity-H "
                         f"/DescendantFonts [{cid_obj} 0 R] /ToUnicode {tounicode_obj} 0 R >>").encode())
        w.set(tounicode_obj, w.stream("", self._to_unicode().encode("ascii")))
        kids = []
        for ops in self.pages:
            content = w.add(w.stream("", "\n".join(ops).encode("ascii")))
            kids.append(w.add((f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {_fmt(PAGE_W)} {_fmt(PAGE_H)}] "
                               f"/Resources << /Font << /F1 {font_obj} 0 R >> >> /Contents {content} 0 R >>").encode()))
        w.set(pages_obj, f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode())
        w.set(catalog, f"<< /Type /Catalog /Pages {pages_obj} 0 R /Lang (he) >>".encode())
        stamp = today.strftime("D:%Y%m%d000000Z")
        w.set(info, f"<< /Title {_hex_utf16(title)} /Producer (Baror App) /CreationDate ({stamp}) >>".encode())
        return w.render(catalog, info)

    def _to_unicode(self) -> str:
        entries = [f"<{gid:04X}> <{ord(ch):04X}>" for gid, ch in sorted(self.used.items()) if ord(ch) <= 0xFFFF]
        blocks = []
        for i in range(0, len(entries), 100):
            chunk = entries[i:i + 100]
            blocks.append(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar")
        return ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n/CMapName /Adobe-Identity-UCS def\n"
                "/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n" + "\n".join(blocks) +
                "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")


def visual_page_label(n: int, total: int) -> str:
    return t("pdf_page", n=n, total=total)


# ------------------------------------------------------------------------------------------------------- the content
def _date(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%d/%m/%Y")


def _status_text(test: str, gender: str, test_date: str, value: int | None) -> str:
    """Status word and gap, worded as in the app (no overall verdict: one text per test)."""
    if value is None:
        return ""
    g = grade_value(test, gender, test_date, value)
    if g.level is None:
        return t("status_ungraded")
    word = {"excellent": "status_excellent", "pass": "status_pass", "fail": "status_fail"}[g.level]
    if not g.gap:
        return t(word)
    phrase = count_phrase("rep", g.gap) if test == "pushups" else duration_phrase(g.gap)
    one = g.gap == 1
    if g.level == "fail":
        return t("fail_with_gap_one" if one else "fail_with_gap", gap=phrase)
    return t(word) + ". " + t("gap_to_excellent_one" if one else "gap_to_excellent", gap=phrase)


def _track_line(user: dict) -> str:
    gender = t("scale_female" if user.get("gender") == "F" else "scale_male")
    scale = t("scale_walk", gender=gender) if user.get("is_km") else gender
    return t("scale_line", scale=scale)


def build_pdf(data: dict, *, today: dt.date) -> bytes:
    """``data`` = ``repo.export_user_data(...)``: keys ``user``, ``results``, ``accommodations``, ``accessed_by``."""
    from baror.domain import accommodation as acc_rules

    user = data["user"]
    doc = _Doc(load_font())
    aero = "walk" if user.get("is_km") else "run"
    doc.paragraph(t("pdf_title"), 20, bold=True, color=OLIVE, gap=2)
    doc.paragraph(t("pdf_generated", date=today.strftime("%d/%m/%Y")), 9.5, color=MUTED, gap=10)
    doc.paragraph(user.get("name", ""), 13, bold=True, gap=1)
    doc.paragraph(t("pdf_email", email=user.get("email", "")), 10, gap=1)
    doc.paragraph(_track_line(user), 10, gap=8)

    doc.keep(95)
    doc.paragraph(t("pdf_section_results"), 14, bold=True, color=OLIVE, gap=6)
    results = data.get("results") or []
    if results:
        rows = [[_date(r["test_date"]),
                 "" if r["run_tenths"] is None else format_deciseconds(r["run_tenths"]),
                 _status_text(aero, user.get("gender"), r["test_date"], r["run_tenths"]),
                 "" if r["pushups"] is None else str(r["pushups"]),
                 _status_text("pushups", user.get("gender"), r["test_date"], r["pushups"])] for r in results]
        doc.table([t("col_date"), t("test_walk") if aero == "walk" else t("pdf_col_run"), t("pdf_col_run_status"),
                   t("pdf_col_pushups"), t("pdf_col_pushups_status")], rows, [62, 48, 148, 48, 189])
    else:
        doc.paragraph(t("pdf_no_results"), 10, color=MUTED)

    doc.keep(95)
    doc.paragraph(t("pdf_section_accommodations"), 14, bold=True, color=OLIVE, gap=6)
    records = data.get("accommodations") or []
    if records:
        rows = []
        for a in records:
            status = acc_rules.status(a, today)      # 'pending' / 'rejected' when the chief has not approved the record
            state = t(("acc_approval_" if status in ("pending", "rejected") else "acc_status_") + status)
            if a.get("delete_requested"):
                state += ". " + t("acc_delete_pending")
            rows.append([t("acc_kind_" + a["kind"]), t("acc_scope_" + a["scope"]), _date(a["start_date"]), _date(a["end_date"]),
                         state, t("acc_source_" + a["source"]), a.get("note") or ""])
        doc.table([t("pdf_col_kind"), t("pdf_col_scope"), t("pdf_col_from"), t("pdf_col_to"), t("col_status"),
                   t("pdf_col_source"), t("pdf_col_note")], rows, [36, 66, 58, 58, 82, 70, 125])
    else:
        doc.paragraph(t("pdf_no_accommodations"), 10, color=MUTED)

    doc.keep(95)
    doc.paragraph(t("pdf_section_views"), 14, bold=True, color=OLIVE, gap=6)
    views = data.get("accessed_by") or []
    if views:
        rows = [[_date(v["date"]), v["reader"] if v.get("reader") else t("pdf_reader_gone"), str(v["reads"])] for v in views]
        doc.table([t("col_date"), t("pdf_col_reader"), t("pdf_col_reads")], rows, [90, 305, 100])
    else:
        doc.paragraph(t("pdf_no_views"), 10, color=MUTED)
    return doc.finish(t("pdf_title"), today)
