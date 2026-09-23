#!/usr/bin/env python3
"""Structural QA for an .xlsx you just produced. Exit code 1 if anything is flagged.

    check.py out.xlsx

There is no Excel in this container, so formulas are never calculated here. This
is the closest thing to opening the workbook: it reopens the file, prints every
sheet's header and size, and flags the mistakes a PM/BD user hits within a minute
of opening it — a total that does not update, a `#NAME?`, a header cut off.
"""

from __future__ import annotations

import re
import sys

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

LEFTOVER = re.compile(
    r"lorem ipsum|sample (text|data)|placeholder|請輸入|請填|填入|\bTODO\b|\bTBD\b|"
    r"\bxxx+\b|\[(company|name|date|title|your [a-z ]+)\]|column ?\d+$|^sheet\d+$",
    re.IGNORECASE,
)
TOTAL_ROW = re.compile(
    r"^(總計|合計|小計|總和|total|subtotal|sum|grand total)\b", re.IGNORECASE
)
NUMBER_AS_TEXT = re.compile(r"^\s*[-+]?[$€£¥]?\s*\d[\d,]*(\.\d+)?\s*%?\s*$")
# Functions Excel only knows with the _xlfn. prefix when written by a library.
NEW_FUNCS = re.compile(
    r"(?<![A-Z_.])(XLOOKUP|XMATCH|FILTER|UNIQUE|SORT|SORTBY|SEQUENCE|LET|LAMBDA|"
    r"TEXTJOIN|CONCAT|IFS|SWITCH|MAXIFS|MINIFS|TEXTSPLIT|VSTACK|HSTACK)\(",
)
FUNC_NAME = re.compile(r"[A-Z][A-Z0-9_.]*\(")
# 'Sheet name'!A1  or  Sheet1!A1:B9  — sheet part optional
REF = re.compile(
    r"(?:(?:'([^']+)'|([A-Za-z0-9_一-鿿][^'!:(),\s]*))!)?"
    r"\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?"
)
DEFAULT_WIDTH = 8.43
BIG_SHEET_ROWS = 25


def _text_len(value: object) -> int:
    s = str(value)
    # CJK glyphs take about two Excel character widths.
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def check_formula(
    formula: str,
    sheet_names: set[str],
    own: str,
    header_text_cols: set[int],
    max_row: int,
    max_col: int,
) -> list[str]:
    problems: list[str] = []
    if formula.count("(") != formula.count(")"):
        problems.append("unbalanced parentheses")
    if "#REF!" in formula:
        problems.append("contains #REF!")
    if m := NEW_FUNCS.search(formula):
        problems.append(
            f"{m.group(1)} needs the _xlfn. prefix (and older Excel lacks it)"
        )
    # Strip string literals so text like "Total (x)" is not parsed as references.
    body = re.sub(r'"[^"]*"', '""', formula)
    for m in REF.finditer(body):
        sheet = m.group(1) or m.group(2)
        if sheet and sheet not in sheet_names:
            problems.append(f"references sheet '{sheet}' which does not exist")
            continue
        if sheet and sheet != own:
            continue  # cannot judge another sheet's layout from here
        # A function name directly before the "ref" (e.g. LOG10) is not a reference.
        if m.start() > 0 and body[m.start() - 1].isalpha():
            continue
        c1, r1 = column_index_from_string(m.group(3)), int(m.group(4))
        c2 = column_index_from_string(m.group(5)) if m.group(5) else c1
        r2 = int(m.group(6)) if m.group(6) else r1
        if r1 == 1 and r2 > 1 and any(c in header_text_cols for c in range(c1, c2 + 1)):
            problems.append(f"range {m.group(0)} includes the header row")
        if r1 > max_row and r2 > max_row > 1:
            problems.append(f"{m.group(0)} points below the data (last row {max_row})")
        if c1 > max_col and c2 > max_col:
            problems.append(f"{m.group(0)} points right of the data")
    return problems


def main(path: str) -> int:
    try:
        wb = load_workbook(path)
    except Exception as exc:  # noqa: BLE001 — this *is* the check
        print(f"FAIL  cannot open {path}: {exc}")
        return 1

    flags = 0
    names = set(wb.sheetnames)
    print(f"{path}: {len(wb.sheetnames)} sheet(s): {', '.join(wb.sheetnames)}\n")

    for ws in wb.worksheets:
        max_row, max_col = ws.max_row, ws.max_column
        header = [ws.cell(1, c).value for c in range(1, max_col + 1)]
        header_text_cols = {
            c for c, h in enumerate(header, 1) if isinstance(h, str) and h.strip()
        }
        formulas = 0
        text_numbers: list[str] = []
        widths_ok = True
        print(
            f"--- sheet {ws.title!r}: {ws.dimensions}, {max_row} row(s) x {max_col} col(s)"
        )
        if ws._charts:
            print(f"    {len(ws._charts)} chart(s)")
        print(f"    header: {header}")

        if max_row <= 1 and all(v is None for v in header):
            print("    FLAG  empty sheet")
            flags += 1
            continue
        if any(h is None or (isinstance(h, str) and not h.strip()) for h in header):
            print("    FLAG  header row has an empty cell — every column needs a name")
            flags += 1
        if max_row > 1 and not any(isinstance(h, str) for h in header):
            print("    FLAG  row 1 is not a header (no text cells)")
            flags += 1

        for row in ws.iter_rows(min_row=1, max_row=max_row):
            first = row[0].value if row else None
            is_total_row = isinstance(first, str) and bool(
                TOTAL_ROW.search(first.strip())
            )
            literal_numbers_in_total = 0
            for cell in row:
                v = cell.value
                if v is None:
                    continue
                if isinstance(v, str) and v.startswith("="):
                    formulas += 1
                    for p in check_formula(
                        v, names, ws.title, header_text_cols, max_row, max_col
                    ):
                        print(f"    FLAG  {cell.coordinate} {v[:50]}: {p}")
                        flags += 1
                elif isinstance(v, str):
                    if cell.row > 1 and NUMBER_AS_TEXT.match(v):
                        text_numbers.append(cell.coordinate)
                    if LEFTOVER.search(v.strip()):
                        print(
                            f"    FLAG  {cell.coordinate} placeholder text left in: {v[:50]}"
                        )
                        flags += 1
                elif isinstance(v, int | float) and is_total_row:
                    literal_numbers_in_total += 1
            if is_total_row and literal_numbers_in_total:
                print(
                    f"    FLAG  row {row[0].row} ({first.strip()}) has "
                    f"{literal_numbers_in_total} literal number(s) — totals must be formulas"
                )
                flags += 1

        if text_numbers:
            sample = ", ".join(text_numbers[:5])
            print(
                f"    FLAG  {len(text_numbers)} number(s) stored as text, e.g. {sample}"
            )
            flags += 1

        for c in range(1, max_col + 1):
            letter = get_column_letter(c)
            width = ws.column_dimensions[letter].width or DEFAULT_WIDTH
            h = header[c - 1]
            if h is not None and _text_len(h) > width + 1:
                print(
                    f"    FLAG  header {letter}1 {str(h)[:30]!r} is wider than its column"
                )
                flags += 1
                widths_ok = False
        if max_row > BIG_SHEET_ROWS and not ws.freeze_panes:
            print(
                f"    FLAG  {max_row} rows and the header is not frozen (ws.freeze_panes)"
            )
            flags += 1

        print(
            f"    {formulas} formula(s){'' if widths_ok else ', column widths need work'}"
        )
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
