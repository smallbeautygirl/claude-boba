#!/usr/bin/env python3
"""Render PDF pages to PNG so you can *look* at them with the Read tool.

    render.py out.pdf            # page 1
    render.py out.pdf 1 3 7      # these 1-based pages
    render.py out.pdf all        # every page (capped at 20)

Writes <stem>-p<N>.png next to the PDF at ~110 dpi — big enough to judge a
watermark, a rotated scan or a table layout, small enough to read quickly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pypdfium2 as pdfium

MAX_PAGES = 20
SCALE = 1.5  # 72 dpi × 1.5 ≈ 108 dpi


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    path = Path(argv[0])
    pdf = pdfium.PdfDocument(str(path))
    n = len(pdf)
    if len(argv) == 1:
        wanted = [1]
    elif argv[1] == "all":
        wanted = list(range(1, min(n, MAX_PAGES) + 1))
    else:
        wanted = [int(a) for a in argv[1:]]
    for p in wanted:
        if not 1 <= p <= n:
            print(f"skip page {p}: file has {n} page(s)")
            continue
        out = path.with_name(f"{path.stem}-p{p}.png")
        pdf[p - 1].render(scale=SCALE).to_pil().save(out)
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
