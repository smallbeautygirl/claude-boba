#!/usr/bin/env python3
"""Structural QA for a .docx you just produced. Exit code 1 if anything is flagged.

    check.py out.docx

There is no Word in this container, so this is the closest thing to opening the
document: it reopens the file, prints the heading outline, and flags what the
recipient notices on the first page — leftover template text, a fax-looking
Chinese font, blank lines used as spacing, a report with no structure.
"""

from __future__ import annotations

import re
import sys

from docx import Document
from docx.oxml.ns import qn

LEFTOVER = re.compile(
    r"lorem ipsum|click (here )?to (add|edit|enter)|choose an item|enter text|"
    r"請輸入|請填|按一下以|sample text|placeholder|\bTBD\b|\bTODO\b|\bxxx+\b|"
    r"\{\{[^}]*\}\}|\[(company|name|date|title|customer|your [a-z ]+|公司名稱|日期|姓名|客戶)\]",
    re.IGNORECASE,
)
CJK = re.compile(r"[぀-ヿ㐀-鿿]")
HEADING = re.compile(r"^(Title|Heading (\d))$")
MIN_PARAS_FOR_STRUCTURE = 15


def _east_asia_font_set(doc) -> bool:
    """True if any style, the document defaults, or any run names an East Asian font."""
    styles_el = doc.styles.element
    if styles_el.find(f".//{qn('w:rFonts')}[@{qn('w:eastAsia')}]") is not None:
        return True
    return (
        doc.element.body.find(f".//{qn('w:rFonts')}[@{qn('w:eastAsia')}]") is not None
    )


def _all_paragraphs(doc):
    yield from doc.paragraphs
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for s in doc.sections:
        for part in (s.header, s.footer):
            yield from part.paragraphs


def main(path: str) -> int:
    try:
        doc = Document(path)
    except Exception as exc:  # noqa: BLE001 — this *is* the check
        print(f"FAIL  cannot open {path}: {exc}")
        if path.lower().endswith(".dotx"):
            print("      (.dotx: convert with docx_helpers.dotx_to_docx first)")
        return 1

    flags = 0
    body = doc.paragraphs
    non_empty = [p for p in body if p.text.strip()]
    print(
        f"{path}: {len(non_empty)} paragraph(s), {len(doc.tables)} table(s), "
        f"{len(doc.sections)} section(s)\n"
    )

    # Outline
    prev_level = 0
    headings = 0
    for p in body:
        m = HEADING.match(p.style.name)
        if not m:
            continue
        headings += 1
        level = 0 if m.group(1) == "Title" else int(m.group(2))
        indent = "  " * max(level - 1, 0)
        print(
            f"{indent}{'#' * max(level, 1)} {p.text.strip()[:80] or '(empty heading)'}"
        )
        if not p.text.strip():
            print(f"{indent}   FLAG  empty heading")
            flags += 1
        if level > prev_level + 1 and prev_level > 0:
            print(
                f"{indent}   FLAG  jumps from Heading {prev_level} to Heading {level}"
            )
            flags += 1
        if level:
            prev_level = level
    if headings == 0 and len(non_empty) >= MIN_PARAS_FOR_STRUCTURE:
        print(
            f"FLAG  {len(non_empty)} paragraphs and no headings — use Heading 1/2 styles"
        )
        flags += 1
    print()

    # Blank-line spacing
    blank_run = 0
    for p in body:
        if (
            p.text.strip()
            or p._p.findall(f".//{qn('w:drawing')}")
            or p._p.findall(f".//{qn('w:br')}")
        ):
            blank_run = 0
            continue
        blank_run += 1
        if blank_run == 2:
            print(
                "FLAG  consecutive empty paragraphs used as spacing (use space_after)"
            )
            flags += 1

    # Leftover template text, anywhere
    all_text: list[str] = []
    for p in _all_paragraphs(doc):
        t = p.text.strip()
        if not t:
            continue
        all_text.append(t)
        if LEFTOVER.search(t):
            print(f"FLAG  template/sample text left in: {t[:70]}")
            flags += 1

    # Tables
    for i, t in enumerate(doc.tables):
        if not t.rows:
            print(f"FLAG  table {i} has no rows")
            flags += 1
            continue
        header = [c.text.strip() for c in t.rows[0].cells]
        print(f"table {i}: {len(t.rows)} x {len(t.columns)}, header {header}")
        if any(not h for h in header):
            print("   FLAG  header row has an empty cell")
            flags += 1

    # Fonts
    if any(CJK.search(t) for t in all_text) and not _east_asia_font_set(doc):
        print(
            "FLAG  Chinese/Japanese text but no East Asian font set — "
            "Word will fall back (docx_helpers.set_document_fonts)"
        )
        flags += 1

    # TOC without update-on-open
    instrs = " ".join(el.text or "" for el in doc.element.body.iter(qn("w:instrText")))
    if re.search(r"\bTOC\b", instrs):
        upd = doc.settings.element.find(qn("w:updateFields"))
        if upd is None or upd.get(qn("w:val")) not in ("true", "1", "on"):
            print("FLAG  TOC field present but fields are not updated on open")
            flags += 1

    print()
    if flags:
        print(
            f"{flags} flag(s). Fix them, or say in your final message why each is fine."
        )
        return 1
    print("clean.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
