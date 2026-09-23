#!/usr/bin/env python3
"""Structural QA for a .pptx you just produced. Exit code 1 if anything is flagged.

    check.py out.pptx

There is no renderer in this container, so this is the closest thing to looking
at the deck: it reopens the file, prints every slide's text, and flags the
mistakes that a PM/BD audience notices immediately.
"""

from __future__ import annotations

import re
import sys

from pptx import Presentation
from pptx.util import Emu

# Strings that only ever appear in templates and samples, never in a finished deck.
LEFTOVER = re.compile(
    r"lorem ipsum|click to (add|edit)|按一下以|請輸入|sample text|placeholder|"
    r"\[(company|name|date|title|your [a-z ]+)\]|xx+/xx+|company name here|"
    r"insert (title|text|image)|your (title|text|name|company) here",
    re.IGNORECASE,
)
MAX_BULLETS = 6
MAX_WORDS = 12  # for space-separated languages
MAX_CJK_CHARS = 28  # for Chinese/Japanese bullets


def words(text: str) -> int:
    cjk = len(re.findall(r"[぀-ヿ㐀-鿿]", text))
    if cjk > len(text) / 2:
        return cjk // (MAX_CJK_CHARS // MAX_WORDS)  # normalise to the same scale
    return len(text.split())


def main(path: str) -> int:
    try:
        prs = Presentation(path)
    except Exception as exc:  # noqa: BLE001 — this *is* the check
        print(f"FAIL  cannot open {path}: {exc}")
        return 1

    flags = 0
    n = len(prs.slides)
    if n == 0:
        print("FAIL  deck has no slides")
        return 1
    print(
        f"{path}: {n} slide(s), {Emu(prs.slide_width).inches:.2f} x "
        f"{Emu(prs.slide_height).inches:.2f} in\n"
    )

    for i, slide in enumerate(prs.slides, 1):
        title_shape = slide.shapes.title
        text_shapes = [
            sh
            for sh in slide.shapes
            if sh.has_text_frame and sh.text_frame.text.strip()
        ]
        if title_shape is None and text_shapes:
            # Decks built with pptxgenjs have no title *placeholder*; the title is
            # simply the topmost text box. Treat it as such, or every generated
            # deck gets a false "no title" on every slide.
            title_shape = min(text_shapes, key=lambda sh: (sh.top or 0, sh.left or 0))
        title = title_shape.text_frame.text.strip() if title_shape is not None else ""
        bullets: list[str] = []
        all_text: list[str] = []
        for shape in text_shapes:
            for p in shape.text_frame.paragraphs:
                t = "".join(r.text for r in p.runs).strip()
                if t:
                    all_text.append(t)
                    if shape is not title_shape:
                        bullets.append(t)
        print(f"--- slide {i}: {title or '(no title)'}")
        for b in bullets:
            print(f"    • {b[:90]}")

        if not all_text:
            print("    FLAG  empty slide")
            flags += 1
        if not title:
            print("    FLAG  no title")
            flags += 1
        if len(bullets) > MAX_BULLETS:
            print(f"    FLAG  {len(bullets)} bullets (max {MAX_BULLETS})")
            flags += 1
        for b in bullets:
            if words(b) > MAX_WORDS:
                print(f"    FLAG  too long for a slide: {b[:60]}…")
                flags += 1
        for t in all_text:
            if LEFTOVER.search(t):
                print(f"    FLAG  template/sample text left in: {t[:60]}")
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
