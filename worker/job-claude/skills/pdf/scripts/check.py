#!/usr/bin/env python3
"""Structural QA for a PDF you just produced. Exit code 1 if anything is flagged.

    check.py out.pdf [--expect-text] [--password PW]

Reopens the file and prints, per page, size / rotation / text length / fonts.
Flags what the user would notice first: a page with no text at all (scan or
blank), Chinese that came out as black boxes because the font has no CJK glyphs,
a form field still empty, a merged file whose pages jump between sizes.

`--expect-text` makes "no text on page" a flag even when the whole file has no
text layer (use it for a PDF you created yourself). `--password` opens a file you
encrypted, so you can verify the protected output too.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

import pdfplumber
from pypdf import PdfReader

CJK = re.compile(r"[぀-ヿ㐀-鿿]")
BASE14 = ("Helvetica", "Times", "Courier", "Symbol", "Arial")
# reportlab draws a glyph it does not have as ZapfDingbats "n" — a black square.
MISSING_GLYPH = ("ZapfDingbats", "n")
PAGE_SIZES = {
    (595, 842): "A4",
    (842, 595): "A4 landscape",
    (612, 792): "Letter",
    (792, 612): "Letter landscape",
    (420, 595): "A5",
}
UNTITLED = {"", "untitled", "anonymous"}


def _size_name(w: float, h: float) -> str:
    return PAGE_SIZES.get((round(w), round(h)), f"{w:.0f}x{h:.0f} pt")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--expect-text", action="store_true")
    ap.add_argument("--password", default=None)
    a = ap.parse_args()
    path = a.pdf

    try:
        reader = PdfReader(path)
        encrypted = reader.is_encrypted
        if encrypted:
            if a.password is None:
                print(
                    f"{path}: encrypted. Rerun with --password to check the contents."
                )
                return 1
            if not reader.decrypt(a.password):
                print(f"FAIL  {path}: the password does not open it")
                return 1
        n = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 — this *is* the check
        print(f"FAIL  cannot open {path}: {exc}")
        return 1

    flags = 0
    meta = reader.metadata or {}
    title = str(meta.get("/Title") or "").strip()
    print(
        f"{path}: {n} page(s), title {title!r}"
        f"{', encrypted (opened with the password)' if encrypted else ''}\n"
    )
    if n == 0:
        print("FAIL  no pages")
        return 1

    sizes: Counter[str] = Counter()
    empty_pages: list[int] = []
    total_chars = 0
    try:
        plumber = pdfplumber.open(path, password=a.password)
    except Exception as exc:  # noqa: BLE001
        plumber = None
        print(f"    (pdfplumber could not open it: {exc}; font checks skipped)")

    for i, page in enumerate(reader.pages, 1):
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        size = _size_name(w, h)
        sizes[size] += 1
        rot = page.rotation
        # pypdf decodes the built-in CID fonts (MSung-Light …) correctly;
        # pdfplumber/pdfminer prints them as the wrong characters. So text comes
        # from pypdf and only the per-glyph font names come from pdfplumber.
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        fonts: Counter[str] = Counter()
        boxes = 0
        cjk_in_latin = 0
        if plumber is not None:
            for ch in plumber.pages[i - 1].chars:
                font = str(ch.get("fontname", "?"))
                fonts[font] += 1
                if MISSING_GLYPH[0] in font and ch["text"] == MISSING_GLYPH[1]:
                    boxes += 1
                elif CJK.match(ch["text"]) and any(b in font for b in BASE14):
                    cjk_in_latin += 1
        total_chars += len(text)
        font_desc = ", ".join(
            f"{f.split('+')[-1]}×{c}" for f, c in fonts.most_common(3)
        )
        print(
            f"--- page {i}: {size}{f', rotated {rot}°' if rot else ''}, "
            f"{len(text)} chars{f', fonts: {font_desc}' if font_desc else ''}"
        )
        if text.strip():
            print(f"    {text.strip()[:90]!r}")
        elif not boxes:
            empty_pages.append(i)
        if boxes or cjk_in_latin:
            print(
                f"    FLAG  {boxes + cjk_in_latin} glyph(s) the font cannot draw (black boxes) — "
                "for Chinese register UnicodeCIDFont('MSung-Light')"
            )
            flags += 1

    print()
    if empty_pages:
        if total_chars == 0 and not a.expect_text:
            print(
                "NOTE  no text layer on any page — a scan or pure images. There is no OCR "
                "here; say so instead of guessing the content."
            )
        else:
            print(
                f"FLAG  no text on page(s) {empty_pages} — blank, scanned, or fonts not embedded"
            )
            flags += 1
    if len(sizes) > 1:
        print(
            f"FLAG  mixed page sizes: {dict(sizes)} — fine for a merge, wrong for a created file"
        )
        flags += 1
    if title.lower() in UNTITLED:
        print(
            "FLAG  no /Title metadata (PdfWriter.add_metadata({'/Title': …}) / "
            "SimpleDocTemplate(title=…))"
        )
        flags += 1

    try:
        fields = reader.get_fields() or {}
    except Exception:  # noqa: BLE001
        fields = {}
    if fields:
        empty = [
            k
            for k, v in fields.items()
            if v.get("/FT") in ("/Tx", "/Ch") and not str(v.get("/V") or "").strip()
        ]
        unchecked = [
            k
            for k, v in fields.items()
            if v.get("/FT") == "/Btn" and v.get("/V") in (None, "/Off")
        ]
        print(
            f"form: {len(fields)} field(s), {len(empty)} empty, {len(unchecked)} unchecked"
        )
        if empty:
            print(
                f"    FLAG  empty field(s): {empty[:12]}{' …' if len(empty) > 12 else ''}"
            )
            flags += 1
        if unchecked:
            print(
                f"    unchecked: {unchecked[:12]} — fine if the user did not tick them"
            )

    if plumber is not None:
        plumber.close()
    print()
    if flags:
        print(
            f"{flags} flag(s). Fix them, or say in your final message why each is fine."
        )
        return 1
    print("clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
