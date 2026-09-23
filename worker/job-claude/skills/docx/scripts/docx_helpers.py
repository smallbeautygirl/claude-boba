"""Things python-docx does not do out of the box, needed in nearly every PM/BD document.

    import sys; sys.path.insert(0, ".claude/skills/docx/scripts")
    from docx_helpers import *

Everything here edits the OOXML that python-docx leaves untouched: the East Asian
font slot, field codes (PAGE / NUMPAGES / TOC), placeholder text split across
runs, and the .dotx content type. Written from the OOXML spec, not copied.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph

__all__ = [
    "add_field",
    "add_page_number",
    "add_toc",
    "dotx_to_docx",
    "insert_paragraph_after",
    "iter_paragraphs",
    "repeat_table_header",
    "replace_text",
    "set_document_fonts",
    "set_font",
    "update_fields_on_open",
]

STYLES_WITH_TEXT = (
    "Normal",
    "Title",
    "Subtitle",
    "Heading 1",
    "Heading 2",
    "Heading 3",
    "List Bullet",
    "List Number",
    "Table Grid",
)


# --- fonts -----------------------------------------------------------------


def set_font(
    target,
    latin: str | None = None,
    east_asia: str | None = None,
    size_pt: float | None = None,
) -> None:
    """Set the Latin and/or East Asian font of a style or a run.

    `run.font.name` only fills the ascii/hAnsi slots; CJK glyphs are drawn with
    the `w:eastAsia` slot, which python-docx has no API for. Setting both is the
    only way Chinese text comes out in the font you asked for.
    """
    rpr = target.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    if latin:
        rfonts.set(qn("w:ascii"), latin)
        rfonts.set(qn("w:hAnsi"), latin)
        rfonts.set(qn("w:cs"), latin)
    if east_asia:
        rfonts.set(qn("w:eastAsia"), east_asia)
        # Without this Word may still pick the theme's East Asian font.
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            rfonts.attrib.pop(qn(attr), None)
    if size_pt:
        target.font.size = Pt(size_pt)


def set_document_fonts(
    doc,
    latin: str = "Calibri",
    east_asia: str = "微軟正黑體",
    size_pt: float | None = None,
) -> None:
    """Apply the font pair to every style a normal document uses.

    Heading sizes are left alone; only Normal gets `size_pt`.
    """
    for name in STYLES_WITH_TEXT:
        try:
            style = doc.styles[name]
        except KeyError:
            continue
        if style.type not in (WD_STYLE_TYPE.PARAGRAPH, WD_STYLE_TYPE.TABLE):
            continue
        set_font(style, latin, east_asia, size_pt if name == "Normal" else None)


# --- fields ----------------------------------------------------------------


def add_field(paragraph: Paragraph, instruction: str, placeholder: str = "") -> None:
    """Append a complex field (begin / instrText / separate / result / end) to a paragraph.

    `instruction` is the field code, e.g. "PAGE", "NUMPAGES", 'TOC \\o "1-3" \\h \\z \\u'.
    `placeholder` is what shows until Word updates the field.
    """
    run = paragraph.add_run()
    for kind in ("begin", "instr", "separate", "result", "end"):
        if kind == "instr":
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = f" {instruction} "
        elif kind == "result":
            el = OxmlElement("w:t")
            el.set(qn("xml:space"), "preserve")
            el.text = placeholder
        else:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        run._r.append(el)


def add_page_number(
    paragraph: Paragraph, template: str = "{PAGE} / {NUMPAGES}"
) -> None:
    """Write e.g. 第 {PAGE} 頁 / 共 {NUMPAGES} 頁 into a footer paragraph, centred."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for piece in re.split(r"(\{PAGE\}|\{NUMPAGES\})", template):
        if piece == "{PAGE}":
            add_field(paragraph, "PAGE", "1")
        elif piece == "{NUMPAGES}":
            add_field(paragraph, "NUMPAGES", "1")
        elif piece:
            paragraph.add_run(piece)


def add_toc(paragraph: Paragraph, levels: str = "1-3") -> None:
    """Insert a table-of-contents field; call update_fields_on_open(doc) too."""
    add_field(
        paragraph,
        f'TOC \\o "{levels}" \\h \\z \\u',
        "請在 Word 中按 F9 更新目錄 / Right-click → Update Field",
    )


def update_fields_on_open(doc) -> None:
    """Ask Word to refresh every field (TOC, NUMPAGES) when the file is opened."""
    settings = doc.settings.element
    el = settings.find(qn("w:updateFields"))
    if el is None:
        el = OxmlElement("w:updateFields")
        settings.append(el)
    el.set(qn("w:val"), "true")


# --- text ------------------------------------------------------------------


def _paragraphs_in(container):
    yield from container.paragraphs
    for t in container.tables:
        for row in t.rows:
            for cell in row.cells:
                yield from _paragraphs_in(cell)


def iter_paragraphs(doc):
    """Every paragraph in the body, its tables (nested too), headers and footers."""
    yield from _paragraphs_in(doc)
    for section in doc.sections:
        for part in (
            section.header,
            section.footer,
            section.first_page_header,
            section.first_page_footer,
        ):
            yield from _paragraphs_in(part)


def replace_text(doc, old: str, new: str) -> int:
    """Replace `old` with `new` everywhere, keeping formatting. Returns the count.

    Word splits text into runs at every formatting or spell-check boundary, so
    `[公司名稱]` is often three runs. When the match spans runs, the whole match is
    written into the first run of the span (keeping *its* formatting) and removed
    from the others.
    """
    count = 0
    for p in iter_paragraphs(doc):
        if old not in p.text:
            continue
        runs = p.runs
        # Fast path: match inside a single run.
        done_here = False
        for r in runs:
            if old in r.text:
                r.text = r.text.replace(old, new)
                count += 1
                done_here = True
        if done_here or old not in p.text:
            continue
        # Slow path: match spans runs. Map character offsets to runs.
        text = "".join(r.text for r in runs)
        start = text.find(old)
        while start != -1:
            end = start + len(old)
            pos = 0
            first = None
            for r in runs:
                r_start, r_end = pos, pos + len(r.text)
                pos = r_end
                if r_end <= start or r_start >= end:
                    continue
                keep_head = r.text[: max(0, start - r_start)]
                keep_tail = r.text[max(0, end - r_start) :]
                if first is None:
                    first = r
                    r.text = keep_head + new + keep_tail
                else:
                    r.text = keep_head + keep_tail
            count += 1
            text = "".join(r.text for r in runs)
            start = text.find(old)
    return count


def insert_paragraph_after(
    paragraph: Paragraph, text: str = "", style: str | None = None
):
    """python-docx only appends at the end; this inserts right after `paragraph`."""
    new_p = OxmlElement("w:p")
    paragraph._p.addnext(new_p)
    para = Paragraph(new_p, paragraph._parent)
    if text:
        para.add_run(text)
    if style:
        para.style = style
    return para


# --- tables ----------------------------------------------------------------


def repeat_table_header(row) -> None:
    """Mark a table row as a header that repeats on every page."""
    trpr = row._tr.get_or_add_trPr()
    el = trpr.find(qn("w:tblHeader"))
    if el is None:
        el = OxmlElement("w:tblHeader")
        trpr.append(el)
    el.set(qn("w:val"), "true")


# --- templates -------------------------------------------------------------

_TEMPLATE_CT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"
)
_DOCUMENT_CT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


def dotx_to_docx(src: str | Path, dst: str | Path) -> Path:
    """Turn a .dotx into a .docx python-docx will open. Nothing else is changed."""
    src, dst = Path(src), Path(dst)
    with (
        zipfile.ZipFile(src) as zin,
        zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(_TEMPLATE_CT.encode(), _DOCUMENT_CT.encode())
            zout.writestr(item, data)
    if src.resolve() == dst.resolve():  # pragma: no cover — defensive
        shutil.move(dst, src)
    return dst
