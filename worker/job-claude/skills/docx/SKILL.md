---
name: docx
description: Build, edit or fill Word documents (.docx / .dotx). Use whenever the user asks for a report, memo, proposal, meeting minutes, formal letter, Word 文件, 報告, 備忘錄, 會議紀錄, 提案書; attaches a .docx/.dotx; or wants parts of an existing document rewritten or a template filled in. The deliverable is always a .docx file saved in the working directory.
---

# Documents for PM / BD colleagues

You are writing a document that a product manager or business developer will
send **as is** — to a customer, a manager, a partner — usually built from
material they attached (notes, a spec, an email thread) or by filling in a
company template. They will open it in Word or Pages and will not fix styles.
Write in the language the user wrote in.

Everything you need is preinstalled and works offline (the network is
allow-listed, so `pip install` / `npm install` will fail — do not try):

| Need | Use |
|---|---|
| Build a **new** document, **edit** or **fill** an attached one | `python-docx` (`from docx import Document`) plus `scripts/docx_helpers.py` |
| Read a document someone attached | `markitdown their.docx` (headings, lists and tables come out as Markdown) |
| Fonts, page numbers, TOC, placeholder replacement, `.dotx` | `scripts/docx_helpers.py` — see §3 |
| Check your result | `python3 .claude/skills/docx/scripts/check.py out.docx` — **required before you finish** |

There is **no Word and no LibreOffice**, so you cannot render pages, count pages
or convert to PDF. Fields (page numbers, table of contents) are stored as
instructions that Word fills in when the file opens. Your QA is structural:
reopen, read back, check the outline.

Paths above are relative to this skill's directory: `.claude/skills/docx/`.
Import the helpers with:

```python
import sys; sys.path.insert(0, ".claude/skills/docx/scripts")
from docx_helpers import *
```

## 1. Choose the route first

1. **A `.docx` is attached and the user wants it changed** (sections rewritten,
   a table updated, numbers corrected) → **edit route**. Open it, change text
   *inside* the existing paragraphs and runs. Never rebuild their document: the
   value is in keeping *their* styles, header, footer and logo.
2. **A template (`.dotx`, or a `.docx` full of `[公司名稱]` / `{{date}}` slots) is
   attached** → **fill route**: `dotx_to_docx()` first if needed, then
   `replace_text()` every placeholder, then add the body under their headings.
3. **No document attached** → **build route**: new `Document()`, styles set once
   with `set_document_fonts()`, then headings, paragraphs, lists, tables.

If several files are attached, `markitdown` each one and read it fully before
deciding the outline.

## 2. Build route — python-docx

```python
import sys; sys.path.insert(0, ".claude/skills/docx/scripts")
from docx import Document
from docx.shared import Pt, Cm
from docx_helpers import set_document_fonts, add_page_number, add_toc, update_fields_on_open, repeat_table_header

doc = Document()
set_document_fonts(doc, latin="Calibri", east_asia="微軟正黑體", size_pt=11)
for s in doc.sections:
    s.left_margin = s.right_margin = Cm(2.5)
    add_page_number(s.footer.paragraphs[0], "第 {PAGE} 頁 / 共 {NUMPAGES} 頁")

doc.add_heading("Q3 試玩期回顧", level=0)          # level 0 = Title style
p = doc.add_paragraph("本文件整理 …")                  # Normal
doc.add_heading("1. 背景", level=1)
doc.add_paragraph("第一點", style="List Bullet")
doc.add_paragraph("第一步", style="List Number")

t = doc.add_table(rows=1, cols=3, style="Table Grid")
for cell, h in zip(t.rows[0].cells, ["項目", "數量", "金額"]):
    cell.text = h
    cell.paragraphs[0].runs[0].bold = True
repeat_table_header(t.rows[0])
for name, qty, amt in rows:
    c = t.add_row().cells
    c[0].text, c[1].text, c[2].text = name, str(qty), f"{amt:,}"

doc.save("回顧.docx")
```

Gotchas that cost real time:

- **Chinese needs the East Asian font slot.** `run.font.name = "微軟正黑體"` sets
  only the Latin font; CJK characters still fall back to whatever Word picks
  (usually 新細明體, which looks like a fax). `set_document_fonts()` sets both
  slots on Normal, Title, Heading 1–3 and the List styles — call it once, first.
  Use a font the reader's machine has: `微軟正黑體` (Windows) or `PingFang TC` (Mac).
- **Use the built-in styles, not manual formatting.** `Heading 1/2/3`, `Title`,
  `List Bullet`, `List Number`, `Table Grid`. That is what makes Word's navigation
  pane, TOC and "update all headings" work for the user later. Do not fake a
  heading by bolding a Normal paragraph, and do not type `1.` / `•` by hand in a
  list style.
- **Spacing is a paragraph property**, `paragraph_format.space_after = Pt(6)`;
  never add empty paragraphs to make room. `check.py` flags runs of blank lines.
- **Table of contents**: `add_toc(doc.add_paragraph())` inserts the field and
  `update_fields_on_open(doc)` makes Word fill it when the file opens (Word asks
  "update fields?" — the user clicks Yes). Until then the TOC reads
  「請在 Word 中按 F9 更新目錄」; tell the user that in your final message.
- Page break before a chapter: `doc.add_page_break()`, only if asked or the
  document has a cover page.

## 3. Edit / fill route — their document

```python
from docx import Document
doc = Document("their.docx")
for i, p in enumerate(doc.paragraphs):
    if p.text.strip():
        print(i, p.style.name, repr(p.text[:70]))
for ti, t in enumerate(doc.tables):
    print("table", ti, len(t.rows), "x", len(t.columns), [c.text for c in t.rows[0].cells])
```

- **`.dotx` will not open** with `Document()` — python-docx rejects the template
  content type. `dotx_to_docx("their.dotx", "work.docx")` rewrites the one
  content-type entry; then edit `work.docx` and deliver a `.docx`.
- **Change text through runs, not `paragraph.text`.** Assigning `paragraph.text`
  throws away bold / colour / font of every run. Use
  `replace_text(doc, "[公司名稱]", "LinkerVision")` — it finds placeholders even
  when Word split them across runs, and covers tables, headers and footers.
  It returns the number of replacements; if it returns 0, print the paragraphs
  and look at how the placeholder is actually spelt.
- To insert new paragraphs **after** an existing one (not at the end):
  `insert_paragraph_after(p, "text", style="Normal")`.
- Add rows to their table with `table.add_row()`; the new row inherits the table
  style. Delete a row with `row._tr.getparent().remove(row._tr)`.
- Headers and footers are per section: `doc.sections[0].header.paragraphs`.
- Pictures and logos stay where they are as long as you do not remove their
  paragraph. Never re-insert them.

When filling a template:

- **Template slots ≠ your content.** Every sample string the template shipped
  with (`Lorem ipsum`, `[日期]`, `{{customer}}`, `Click here to enter text`) must
  be gone or replaced. `check.py` flags the common shapes; `markitdown` the
  result and read it for the rest.
- Keep their heading hierarchy; put your content under *their* headings rather
  than adding a parallel set.

## 4. What goes in the document

PM/BD documents are skimmed by someone busy. So:

- **First paragraph says what this is and what is being asked** (a decision, a
  budget, a sign-off). Not history.
- Headings are sentences a reader can navigate by. `Heading 1` for the 3–6 main
  parts, `Heading 2` inside them; never skip a level (H1 → H3).
- One idea per paragraph, 2–5 sentences. Longer detail goes in an appendix
  section at the end, not inline.
- Numbers go in a table with a header row and units in the header, not in prose.
- Keep the source's terms. If the notes say 「試玩期」, the document says 「試玩期」.
- Two pages asked for ≈ 900 Chinese characters or 700 English words. Do not pad,
  and do not add sections (「結論」「附錄」) that were not asked for and have no content.

## 5. QA — required, every time

1. `python3 .claude/skills/docx/scripts/check.py out.docx`
   Reopens the file (a file that does not open is not done), prints the heading
   outline with paragraph and table counts, and flags: leftover placeholder /
   sample text, skipped heading levels, empty headings, runs of blank paragraphs,
   a long document with no headings, tables with an empty header cell, Chinese
   text with no East Asian font set, a TOC field without update-on-open.
   **Fix what it flags and run it again** until it reports clean, or explain in
   your final message exactly why a flag is acceptable.
2. `markitdown out.docx` and read it as the recipient would: does the first
   paragraph say what this is, is the order right, is anything from the source
   missing?
3. Say in your final message where the file is and give the outline (one line
   per heading). Mention if Word will ask to update fields. The user downloads
   the file from this job; they will not see your working directory.

## Do not

- Do not produce Markdown, HTML or a PDF instead of a `.docx` because something
  failed. Fix the failure or say clearly what blocked you. If they also want a
  PDF, say that "Save as PDF" in Word is one click — you cannot convert here.
- Do not `pip install` / `npm install`; it will fail and waste the budget.
- Do not unzip and hand-edit `document.xml` unless python-docx genuinely cannot
  do it; if you must, run `check.py` afterwards — Word refuses malformed XML with
  a message that does not say where.
- Do not save over the attached file; save under a new name.
