---
name: xlsx
description: Build, edit or clean up Excel workbooks (.xlsx) and CSV files. Use whenever the user asks for a spreadsheet, a table of numbers, Excel, 表格, 試算表, 報表; attaches an .xlsx/.csv; or wants figures organised, totalled, compared or charted. The deliverable is always an .xlsx file saved in the working directory.
---

# Workbooks for PM / BD colleagues

You are making a workbook that a product manager or business developer will open
in Excel **and keep using**: they will change a number and expect the totals to
follow, sort a column, forward it to a customer. So totals are formulas, numbers
are numbers, and the first sheet looks finished the moment it opens. Write labels
in the language the user wrote in.

Everything you need is preinstalled and works offline (the network is
allow-listed, so `pip install` will fail — do not try):

| Need | Use |
|---|---|
| Build a **new** workbook, or **edit** one that was attached | `openpyxl` (`from openpyxl import Workbook, load_workbook`) |
| Read a workbook someone attached | `markitdown file.xlsx` (one Markdown table per sheet) — or `load_workbook(f, data_only=True)` for the cached values |
| Read / write CSV | the stdlib `csv` module (encodings: see §3) |
| Check your result | `python3 .claude/skills/xlsx/scripts/check.py out.xlsx` — **required before you finish** |

There is **no Excel and no LibreOffice** in this container, so **formulas are not
calculated here**. `openpyxl` stores the formula text; Excel computes it when the
user opens the file. Consequences:

- Reading a file you just wrote with `data_only=True` gives `None` for every
  formula cell. That is not a bug in your file. Do not "fix" it by pasting values.
- You cannot see `#REF!`, `#NAME?` or `#DIV/0!` yourself. `check.py` catches the
  usual causes structurally (§5); read its output.

Paths above are relative to this skill's directory: `.claude/skills/xlsx/`.

## 1. Choose the route first

1. **An `.xlsx` is attached and the user wants it changed** (a column added, a
   summary sheet, totals fixed, data cleaned) → **edit route**. Open it with
   `load_workbook("their.xlsx")` — **never** `data_only=True` when you intend to
   save, that would replace every formula in their file with a stale value or
   `None`. Keep their sheets, styles and column order; add, do not rebuild.
2. **A `.csv` or pasted numbers / a document is attached** → **build route**:
   new `Workbook()`, one table per sheet.
3. **Both** (their template plus new data) → open the template, write the data
   into it, save under a new name.

If several files are attached, read each one fully first (`markitdown`), then
decide which data goes on which sheet.

## 2. Build route — openpyxl

```python
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

wb = Workbook()
ws = wb.active
ws.title = "Q3 營收"                      # ≤ 31 chars, none of  [ ] : * ? / \
headers = ["產品", "數量", "單價", "小計"]
ws.append(headers)
rows = [("方案 A", 12, 1500), ("方案 B", 3, 42000)]
for r, (name, qty, price) in enumerate(rows, start=2):
    ws.cell(r, 1, name)
    ws.cell(r, 2, qty)                     # a number, not "12"
    ws.cell(r, 3, price).number_format = '#,##0'
    ws.cell(r, 4, f"=B{r}*C{r}").number_format = '#,##0'
last = ws.max_row
ws.cell(last + 1, 1, "合計")
ws.cell(last + 1, 4, f"=SUM(D2:D{last})").number_format = '#,##0'

# Finish the sheet so it opens looking done.
for c in ws[1]:
    c.font = Font(bold=True)
    c.fill = PatternFill("solid", fgColor="DDEBF7")
    c.alignment = Alignment(horizontal="center")
ws.freeze_panes = "A2"
ws.auto_filter.ref = ws.dimensions
for i, h in enumerate(headers, 1):
    width = max(len(str(c.value or "")) for c in ws[get_column_letter(i)])
    ws.column_dimensions[get_column_letter(i)].width = min(max(10, width * 1.6), 50)
wb.save("報表.xlsx")
```

Rules that make the difference between "a file" and "a workbook":

- **Numbers are numbers.** Write `1500`, not `"1,500"` or `"1500"`. Strip
  thousands separators and currency signs from the source and put the look back
  with `number_format` (`'#,##0'`, `'0.0%'`, `'"US$"#,##0.00'`, `'yyyy-mm-dd'`).
  Dates are `datetime.date` objects, never strings.
- **Anything derived is a formula.** Totals, subtotals, growth rates, shares:
  `=SUM(...)`, `=B2/B$10`, `=IFERROR(C2/B2-1, "")`. If the user changes an input,
  the sheet must still be right. `check.py` flags a "合計/Total" row of literals.
- **Formulas are written in English with commas**, whatever the user's locale:
  `=SUM(A1:A9)`, `=IF(A1>0, "yes", "no")`. Excel translates on open.
  Sheet names with spaces are quoted: `='Q3 營收'!D12`.
- **Stick to functions every Excel has**: `SUM AVERAGE COUNT COUNTA MIN MAX IF
  IFERROR SUMIF SUMIFS COUNTIF COUNTIFS VLOOKUP INDEX MATCH ROUND TEXT`.
  Newer ones (`XLOOKUP`, `FILTER`, `UNIQUE`, `LET`, `TEXTJOIN`) must be written
  as `=_xlfn.XLOOKUP(...)` or Excel shows `#NAME?` — and they still fail on older
  Excel. Prefer `INDEX`/`MATCH`.
- **Do not include the header row in a range.** `=SUM(B1:B20)` when B1 is a
  header is the most common formula mistake; start at row 2.
- **Column widths are not automatic.** Unset columns are ~8 characters wide and
  long headers get cut off. Set `column_dimensions[...].width` as above.
- **One table per sheet, starting at A1**, header in row 1, no title rows above
  it, no blank columns inside it — that is what makes sort, filter and pivot work
  for the user later. A title or notes go on a separate `說明` sheet.
- Charts: `openpyxl.chart` (`BarChart`, `LineChart`, `PieChart`) with
  `Reference(ws, min_col=, min_row=, max_col=, max_row=)`; `chart.add_data(ref,
  titles_from_data=True)`; `chart.set_categories(...)`; `ws.add_chart(chart,
  "F2")`. One chart per question, titled with the point it makes.

## 3. Edit route — their workbook

```python
from openpyxl import load_workbook
wb = load_workbook("their.xlsx")            # NOT data_only=True — see above
for ws in wb.worksheets:
    print(ws.title, ws.dimensions, ws.max_row, "rows")
    for row in ws.iter_rows(min_row=1, max_row=3, values_only=True):
        print("   ", row)
```

- **A load/save round trip through openpyxl drops charts, images and pivot
  tables** from the original file. Check the original for them (`ws._charts`,
  `ws._images`; `check.py` reports charts) and, if present, either put your work
  on a **new sheet of a new file** and tell the user, or say plainly that the
  chart did not survive. Do not let them discover it in the meeting.
- Conditional formatting, data validation, freeze panes and named ranges survive.
- Insert columns with `ws.insert_cols(idx)` — but existing formulas are **not**
  adjusted for you. Prefer appending to the right / bottom of the table.
- Match their formatting: copy `number_format`, `font`, `fill` from the
  neighbouring cell (`copy(src.font)`, etc.) instead of inventing a new look.
- Save under a new name (`their-更新.xlsx`), never over the attachment.

CSV encodings: Excel on Taiwanese Windows exports **cp950 (Big5)**, not UTF-8.
Read with `open(p, encoding="utf-8-sig")` and, on `UnicodeDecodeError`, retry with
`encoding="cp950"`. If asked for a CSV back, write `encoding="utf-8-sig"` so Excel
shows Chinese correctly on double-click.

## 4. What goes on a sheet

PM/BD workbooks are read at a glance and reused afterwards. So:

- The **first sheet answers the question** (the summary), raw data sits behind it.
  Name sheets by content (`摘要`, `明細`, `原始資料`), not `Sheet1`.
- Every column has a header a colleague understands without asking. Units go in
  the header (`金額 (NT$)`, `成長率 %`), not in each cell.
- Keep the source's terms. If the meeting notes say 「試玩期」, the column says
  「試玩期」.
- Highlight sparingly: bold header, one fill colour, `number_format` for
  negatives (`'#,##0;[Red]-#,##0'`). No rainbow.
- Do not add what was not asked for (extra sheets of "analysis", a dashboard).
  Three columns asked for = three columns.

## 5. QA — required, every time

1. `python3 .claude/skills/xlsx/scripts/check.py out.xlsx`
   Reopens the file (a file that does not open is not done), prints every sheet
   with its header and size, counts formulas, and flags: numbers stored as text,
   totals that are literals, formulas referencing a missing sheet / row 1 header /
   cells outside the data, unbalanced parentheses, new functions without
   `_xlfn.`, leftover placeholder text, headers wider than their column, big
   sheets without frozen header. **Fix what it flags and run it again** until it
   reports clean, or explain in your final message exactly why a flag is fine.
2. `markitdown out.xlsx` and read it as the user would: is the summary on the
   first sheet, are the columns the ones they asked for, are any source rows
   missing (count them against the input)?
3. Say in your final message where the file is, one line per sheet (what it
   holds, how many rows), and which cells are formulas the user can trust to
   update. The user downloads the file from this job; they will not see your
   working directory.

## Do not

- Do not deliver a CSV, Markdown table or HTML when an `.xlsx` was asked for.
  Fix the failure or say clearly what blocked you.
- Do not paste computed values where a formula belongs, and do not compute totals
  in Python and write the result — Excel will, live.
- Do not `pip install`; it will fail and waste the budget.
- Do not open with `data_only=True` and then save. Do not save over the attached
  file.
