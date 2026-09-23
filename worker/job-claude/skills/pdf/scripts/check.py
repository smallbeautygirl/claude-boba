#!/usr/bin/env python3
"""Structural QA for a PDF you just produced. Exit code 1 if anything is flagged.

    check.py out.pdf [--expect-text] [--password PW]

Reopens the file and prints, per page, size / rotation / text length / fonts.
Flags what the user would notice first: a page with no text at all (scan or
blank), Chinese drawn with a font that cannot draw it (black boxes) or with a
font that is not embedded at all (text layer fine, page blank in most readers),
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
CJK_FONT = "/usr/share/fonts/truetype/arphic/uming.ttc"
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


def _unembedded_fonts(page) -> set[str]:
    """BaseFont names used on this page whose glyphs are NOT inside the file.

    This is the check that was missing on 2026-09-23. A PDF drawn with an Adobe
    CID font (MSung-Light …) carries the Chinese in its text layer but **no
    glyphs at all** — it expects the reader to own the Asian font pack. Every
    other check passes: extract_text() returns the Chinese, copy-paste works,
    the font name looks like a Chinese font. The page is simply blank.

    A font is embedded iff its descriptor carries /FontFile, /FontFile2 or
    /FontFile3. For Type0 (CID) fonts the descriptor lives one level down, on
    the descendant font — looking only at the top level reports every CID font
    as unembedded, including properly embedded ones.
    """
    out: set[str] = set()
    try:
        res = page.get("/Resources")
        table = res.get_object().get("/Font") if res is not None else None
        if table is None:
            return out
        for ref in table.get_object().values():
            font = ref.get_object()
            base = str(font.get("/BaseFont") or "").lstrip("/").split("+")[-1]
            kids = font.get("/DescendantFonts")
            targets = (
                [d.get_object() for d in kids.get_object()]
                if kids is not None
                else [font]
            )
            embedded = any(
                any(
                    k in t.get("/FontDescriptor").get_object()
                    for k in ("/FontFile", "/FontFile2", "/FontFile3")
                )
                for t in targets
                if t.get("/FontDescriptor") is not None
            )
            if base and not embedded:
                out.add(base)
    except Exception as exc:  # noqa: BLE001 — a broken font table is not this check's job
        # 不要讓這條檢查把整個 QA 弄倒：它是**附加**的一條規則，其餘的檢查
        # （頁數、文字、表單）仍然有價值。但也不要沉默 —— 沉默的結果就是
        # 這個 bug 當初的樣子：一份壞檔案配一句「clean」。
        print(f"    (font embedding check skipped: {exc})")
    return out


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
        cjk_unembedded = 0
        unembedded = _unembedded_fonts(page)
        if plumber is not None:
            for ch in plumber.pages[i - 1].chars:
                font = str(ch.get("fontname", "?"))
                fonts[font] += 1
                if MISSING_GLYPH[0] in font and ch["text"] == MISSING_GLYPH[1]:
                    boxes += 1
                elif CJK.match(ch["text"]) and any(b in font for b in BASE14):
                    cjk_in_latin += 1
                elif CJK.match(ch["text"]) and font.split("+")[-1] in unembedded:
                    cjk_unembedded += 1
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
                f"    FLAG  {boxes + cjk_in_latin} glyph(s) the font cannot draw "
                "(black boxes) — for Chinese use the embedded font, see below"
            )
            flags += 1
        if cjk_unembedded:
            # 這一條不講「可能有問題」：它是確定畫不出來，只是抽字看不出來。
            print(
                f"    FLAG  {cjk_unembedded} Chinese glyph(s) drawn with a "
                "NOT-EMBEDDED font — the text is in the file but the page is "
                "blank in most readers. The text layer and extract_text() look "
                "perfect; only the pixels are missing.\n"
                f"          Fix: pdfmetrics.registerFont(TTFont('UMing', '{CJK_FONT}')) "
                "and draw with fontName='UMing'. Then render.py the page and look."
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
