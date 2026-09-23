#!/usr/bin/env python3
"""Duplicate or delete a slide in a .pptx, because python-pptx cannot.

    dup_slide.py deck.pptx 3 -o out.pptx        # copy slide 3 (1-based) to the end
    dup_slide.py deck.pptx --delete 5 -o out.pptx

Without -o the input file is rewritten in place.

Duplicating copies the slide's shapes and its relationships (pictures, charts,
notes). Parts that are *referenced* rather than embedded — charts, embedded
objects — stay shared with the source slide, so editing one changes both.
Duplicate a slide that only has text and pictures if you need to edit freely.
"""

from __future__ import annotations

import argparse
import copy
import sys

from pptx import Presentation


def duplicate(prs, index: int) -> int:
    src = prs.slides[index - 1]
    layout = src.slide_layout
    dst = prs.slides.add_slide(layout)
    # add_slide() pre-populates placeholders from the layout; we want a verbatim copy.
    for shape in list(dst.shapes):
        shape._element.getparent().remove(shape._element)
    for shape in src.shapes:
        dst.shapes._spTree.append(copy.deepcopy(shape._element))
    # Carry over relationships except the layout (already set) and notes.
    for rel in src.part.rels.values():
        if rel.reltype.endswith("/slideLayout") or rel.reltype.endswith("/notesSlide"):
            continue
        dst.part.rels.get_or_add(rel.reltype, rel._target)
    return len(prs.slides)


def delete(prs, index: int) -> None:
    sld_ids = prs.slides._sldIdLst
    sld_id = list(sld_ids)[index - 1]
    prs.part.drop_rel(sld_id.rId)
    sld_ids.remove(sld_id)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deck")
    ap.add_argument("index", nargs="?", type=int, help="1-based slide to duplicate")
    ap.add_argument("--delete", type=int, metavar="N", help="1-based slide to delete")
    ap.add_argument("-o", "--out", help="output path (default: rewrite input)")
    a = ap.parse_args()

    prs = Presentation(a.deck)
    n = len(prs.slides)
    if a.delete is not None:
        if not 1 <= a.delete <= n:
            sys.exit(f"deck has {n} slides; cannot delete slide {a.delete}")
        delete(prs, a.delete)
        print(f"deleted slide {a.delete}; deck now has {len(prs.slides)} slides")
    elif a.index is not None:
        if not 1 <= a.index <= n:
            sys.exit(f"deck has {n} slides; cannot duplicate slide {a.index}")
        new_n = duplicate(prs, a.index)
        print(f"copied slide {a.index} -> new slide {new_n}")
    else:
        ap.error("give a slide index to duplicate, or --delete N")

    prs.save(a.out or a.deck)
    print(f"saved {a.out or a.deck}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
