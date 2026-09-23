---
name: pdf
description: Read, extract from, merge, split, reorder, rotate, watermark, fill, protect or create PDF files. Use whenever the user attaches a .pdf or asks for one; mentions PDF, 合併, 拆分, 抽頁, 加水印, 加密, 解密, 填表, 讀出表格; or wants text or tables pulled out of a PDF into something editable. The deliverable is a .pdf, or the extracted content as .md / .xlsx, saved in the working directory.
---

# PDFs for PM / BD colleagues

Most PDF jobs here are small and concrete: "merge these five", "give me pages
3–7", "pull the table out of this quote", "put our watermark on it", "fill this
form". The user wants the result file, done right the first time, and a one-line
account of what you did. Work in the language the user wrote in.

Everything you need is preinstalled and works offline (the network is
allow-listed, so `pip install` will fail — do not try):

| Need | Use |
|---|---|
| Read text and **tables** out of a PDF | `pdfplumber` — or `markitdown file.pdf` for a quick full-text dump |
| Merge / split / reorder / rotate / metadata / **forms** / encrypt / decrypt | `pypdf` (`PdfReader`, `PdfWriter`) |
| **Create** a PDF, or an overlay (watermark, stamp, page numbers) | `reportlab` |
| Images → PDF, PDF pages → PNG | `Pillow`; `scripts/render.py` (uses `pypdfium2`) |
| Check your result | `python3 .claude/skills/pdf/scripts/check.py out.pdf` — **required before you finish** |

Two things this container does **not** have:

- **No OCR.** A scanned PDF has pictures of text, not text. `pdfplumber` returns
  `""` and `check.py` reports `0 chars` for those pages. Say so; do not guess
  what the page says.
- **No LibreOffice / Word.** You cannot convert `.docx` / `.pptx` / `.xlsx` to
  PDF. If asked, make the Office file with the matching skill and say that
  "Save as PDF" in Office is one click.

You **can see pages**: `python3 .claude/skills/pdf/scripts/render.py out.pdf 1 3`
writes `out-p1.png`, `out-p3.png`, and you can `Read` those PNGs. Use it whenever
layout matters (watermarks, stamps, created pages, rotated scans).

Paths above are relative to this skill's directory: `.claude/skills/pdf/`.

## 1. Look before you touch

```python
from pypdf import PdfReader
r = PdfReader("their.pdf")
print(len(r.pages), "pages;", "encrypted" if r.is_encrypted else "open")
for i, p in enumerate(r.pages, 1):
    box = p.mediabox
    print(i, f"{float(box.width):.0f}x{float(box.height):.0f} pt", "rot", p.rotation,
          len(p.extract_text() or ""), "chars")
print(r.get_fields() and list(r.get_fields()))     # form fields, if any
```

Then pick the route. **Page numbers**: users count from 1, `pypdf` counts from
0. Convert once, at the edge, and echo the 1-based numbers back in your message
("kept pages 3–7") so an off-by-one is visible to them and to you.

## 2. Routes

**Merge / split / reorder / rotate** — `pypdf`

```python
from pypdf import PdfReader, PdfWriter
w = PdfWriter()
w.append(PdfReader("a.pdf"))                       # whole file, bookmarks kept
w.append(PdfReader("b.pdf"), pages=[2, 3, 4])      # 0-based → user's pages 3–5
for p in w.pages:
    if p.rotation:                                 # a scan that opens sideways
        p.transfer_rotation_to_content()
w.add_metadata({"/Title": "合併版"})
w.write("merged.pdf")
```

`PdfWriter.append()` copies pages and bookmarks but **not** the title metadata —
call `add_metadata` on every writer, including one you are about to `encrypt`.
`page.rotate(90)` is clockwise. For "split into one file per page" loop over
`reader.pages` creating a writer each; name outputs `原檔名-p03.pdf` so they sort.

**Extract text / tables** — `pdfplumber`

```python
import pdfplumber
with pdfplumber.open("quote.pdf") as pdf:
    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        tables = page.extract_tables()
        print(i, len(text), "chars,", len(tables), "table(s)")
        for t in tables:
            for row in t: print("   ", row)
```

- Tables drawn with ruling lines extract well. Tables that are just aligned text
  need `page.extract_tables({"vertical_strategy": "text",
  "horizontal_strategy": "text"})`; check that every row has the same number of
  columns before trusting it, and look at a rendered page (`render.py`) when the
  shape looks odd.
- A table that spans pages comes back as several tables with the header repeated;
  drop the repeated header rows when you join them.
- Deliver tables as an `.xlsx` (see the `xlsx` skill: numbers as numbers, header
  row, widths) unless the user asked for text. Deliver text as `.md`.
- Two-column layouts interleave lines. Crop first:
  `page.crop((0, 0, page.width / 2, page.height)).extract_text()`.

**Fill a form** — `pypdf`

```python
r = PdfReader("form.pdf"); w = PdfWriter(); w.append(r)
fields = r.get_fields()                            # name → {'/FT': type, '/V': value, '/_States_': [...]}
values = {"姓名": "王小明", "同意": "/Yes"}          # checkbox: one of its /_States_
for page in w.pages:
    w.update_page_form_field_values(page, values, auto_regenerate=False)
w.set_need_appearances_writer(True)                # so viewers draw the values
w.write("form-filled.pdf")
```

Print the field list first and map the user's answers to the **exact** field
names. `check.py` lists which fields are still empty afterwards.

**Watermark / stamp / page numbers** — `reportlab` overlay + `pypdf` merge

```python
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))   # see §3 — Chinese

def overlay(w, h, text):
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=(w, h))
    c.saveState(); c.setFont("MSung-Light", 48); c.setFillGray(0.5, 0.25)
    c.translate(w / 2, h / 2); c.rotate(35); c.drawCentredString(0, 0, text)
    c.restoreState(); c.save(); buf.seek(0)
    return PdfReader(buf).pages[0]

for page in w.pages:                               # pages can differ in size
    page.merge_page(overlay(float(page.mediabox.width), float(page.mediabox.height), "機密"))
```

Page numbers: same pattern, `c.drawRightString(w - 40, 24, f"{i} / {n}")`.
Then `render.py` two pages and look — a watermark is judged by eye.

**Create a PDF** — `reportlab` platypus

```python
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
body = ParagraphStyle("body", fontName="MSung-Light", fontSize=11, leading=17, wordWrap="CJK")
h1 = ParagraphStyle("h1", parent=body, fontSize=16, leading=22, spaceAfter=8)
doc = SimpleDocTemplate("報告.pdf", pagesize=A4, title="Q3 回顧")
story = [Paragraph("Q3 試玩期回顧", h1), Paragraph("本文件整理 …", body), Spacer(1, 8)]
t = Table([["項目", "數量"], ["方案 A", "12"]], hAlign="LEFT")
t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "MSung-Light"),
                       ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                       ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEBF7"))]))
story.append(t); doc.build(story)
```

Only build a PDF from scratch when the user asked for a PDF. If they asked for a
"document" or "report", they want Word (`docx` skill).

**Images ↔ PDF** — `Pillow` / `pypdf`

`Image.open(a).convert("RGB").save("scans.pdf", save_all=True, append_images=[...])`
puts photos or screenshots into one PDF (one image per page, page = image size).
`page.images` in `pypdf` gives embedded images back (`img.data`, `img.name`).

**Encrypt / decrypt** — `pypdf`

`w.encrypt(user_password="...", owner_password="...", algorithm="AES-256")`;
opening a protected file: `r.decrypt("password")` then read as usual. Both work
here (`cryptography` is installed). The password came from the user; repeat it
in your final message only if they asked you to invent one.

## 3. Chinese text in PDFs you create

`reportlab`'s built-in fonts (Helvetica, Times) have **no CJK glyphs**; Chinese
drawn with them comes out as blank or black boxes, and there are no CJK `.ttf`
files in this container. Use the built-in CID fonts, which need no font file
(the viewer supplies the glyphs):

| Language | Font name |
|---|---|
| Traditional Chinese | `MSung-Light` |
| Simplified Chinese | `STSong-Light` |
| Japanese | `HeiseiMin-W3` |

```python
pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
```

There is no bold variant: emphasise with size or colour. Paragraph styles need
`wordWrap="CJK"` or Chinese never line-breaks (reportlab breaks on spaces).
`check.py` flags CJK characters drawn with a Latin base font.

## 4. QA — required, every time

1. `python3 .claude/skills/pdf/scripts/check.py out.pdf`
   Reopens the file (a file that does not open is not done) and prints per page:
   size, rotation, characters of text, fonts. Flags: pages with no text at all
   (scanned or blank — say which), CJK text drawn with a Latin base font, form
   fields left empty, encryption state, mixed page sizes in a merged file,
   missing title metadata. **Fix what it flags and run it again** until it
   reports clean, or explain in your final message exactly why a flag is fine.
2. For anything visual — watermarks, stamps, created pages, rotations —
   `render.py out.pdf 1` and `Read` the PNG. Judge it as the reader would.
3. For extraction, count: rows in the PDF table vs rows in your `.xlsx`, pages
   read vs pages in the file.
4. Say in your final message where each output file is and what was done, with
   **1-based** page numbers. The user downloads files from this job; they will
   not see your working directory.

## Do not

- Do not overwrite the attached PDF; write a new file next to it.
- Do not describe the content of a page that has no text layer as if you had
  read it. Report it as scanned and stop there.
- Do not `pip install`; it will fail and waste the budget.
- Do not hand back an `.html` or `.md` when a `.pdf` was asked for. Fix the
  failure or say clearly what blocked you.
