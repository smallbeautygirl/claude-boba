---
name: pptx
description: Build, edit, or fill PowerPoint decks (.pptx / .potx). Use whenever the user asks for slides, a deck, a presentation, 簡報 or 投影片; attaches a .pptx/.potx; or wants pages of an existing deck changed. The deliverable is always a .pptx file saved in the working directory.
---

# Decks for PM / BD colleagues

You are making a deck that a product manager or business developer will present
**tomorrow**, usually built from a document they attached (meeting notes, a spec,
a proposal) or by changing a few pages of a deck they already have. They will open
the result in PowerPoint or Keynote and will not fix XML. Write the deck in the
language the user wrote in.

Everything you need is preinstalled and works offline (the network is
allow-listed, so `pip install` / `npm install` will fail — do not try):

| Need | Use |
|---|---|
| Build a **new** deck | `pptxgenjs` from Node (`NODE_PATH` is set; `require("pptxgenjs")` works) |
| Edit / fill an **existing** deck or template | `python-pptx` (`import pptx`) plus `scripts/dup_slide.py` |
| Read a deck someone attached | `markitdown file.pptx` (one block per slide) |
| Check your result | `python3 scripts/check.py out.pptx` — **required before you finish** |

There is **no LibreOffice**, so you cannot render slides to images. Your QA is
structural: reopen, read back, and check text — do that carefully, because you
will never see the slide.

Paths above are relative to this skill's directory: `.claude/skills/pptx/`.

## 1. Choose the route first

1. **A `.pptx` / `.potx` is attached and the user wants it changed or filled** →
   edit route (python-pptx). Never rebuild their deck from scratch: the value is
   in keeping *their* layouts, colours and logo.
2. **A template is attached and they want new content in it** → template route:
   open the template with python-pptx, add slides from **its** layouts, delete the
   sample slides it came with.
3. **No deck attached** → build route (pptxgenjs). Pick 16:9.

If several files are attached, `markitdown` each one first and read them fully
before deciding what goes on which slide.

## 2. Build route — pptxgenjs

```js
const pptxgen = require("pptxgenjs");
const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";               // 10 x 5.625 in
const s = pres.addSlide();
s.background = { color: "FFFFFF" };         // hex, no '#'
s.addText("標題", { x: 0.5, y: 0.4, w: 9, h: 0.9, fontSize: 30, bold: true,
                     fontFace: "Microsoft JhengHei", color: "1F1F1F" });
s.addText([{ text: "第一點", options: { bullet: true } },
           { text: "第二點", options: { bullet: true } }],
          { x: 0.5, y: 1.5, w: 9, h: 3.5, fontSize: 18, fontFace: "Microsoft JhengHei" });
pres.writeFile({ fileName: "deck.pptx" }).then(() => console.log("wrote deck.pptx"));
```

Gotchas that cost real time:

- `writeFile` is async. If the script exits before the promise resolves you get a
  zero-byte or missing file. Always `.then(...)` or `await`.
- Colours are `RRGGBB` strings **without** `#`. `#1F1F1F` silently produces black.
- `x/y/w/h` are inches on a 10 × 5.625 canvas. Text that does not fit is **not**
  wrapped for you — keep bullets short (see §5) or lower `fontSize`.
- For Chinese text set `fontFace` to a font the audience's machine has:
  `Microsoft JhengHei` (Windows), `PingFang TC` (Mac). Untitled fonts fall back
  to something ugly.
- One `addSlide()` per slide; do not reuse a slide object.

## 3. Edit / template route — python-pptx

```python
from pptx import Presentation
prs = Presentation("their.pptx")
for i, slide in enumerate(prs.slides, 1):
    for shape in slide.shapes:
        if shape.has_text_frame:
            print(i, shape.name, repr(shape.text_frame.text[:60]))
```

Rules that keep their formatting intact:

- **Change text through runs, not `text_frame.text`.** Assigning
  `text_frame.text = "..."` throws away the font, size, colour and bullet of every
  run. Instead: `paragraph.runs[0].text = "..."` and delete the extra runs, or
  `paragraph.clear()` then `add_run()` and copy the font properties you saw.
- **python-pptx cannot duplicate a slide.** Use
  `python3 scripts/dup_slide.py their.pptx 3 -o out.pptx` to copy slide 3 (1-based)
  to the end, then edit the copy. Do all duplicating / deleting / reordering
  **before** you edit any text — a duplicate made after editing carries the edits.
- To **delete** a slide: remove its `sldId` from `prs.slides._sldIdLst` and drop
  the relationship (`prs.part.drop_rel(rId)`). `dup_slide.py --delete N` does this.
- Pictures in templates are often SVG/EMF; python-pptx cannot read those. Reuse
  them by duplicating the slide that has them, never by re-inserting.
- Keep the template's extension: a `.potx` opened and saved stays a template;
  save your filled-in result as `.pptx`.

When filling a template:

- **Template slots ≠ your items.** A layout showing four boxes with three items
  means the fourth box is deleted, not left blank or left with sample text.
- Every sample string the template shipped with (names, "Lorem ipsum",
  "Click to add", dates, `[Company]`) must be gone. `check.py` flags the common
  ones; read the deck back with `markitdown` for the rest.

## 4. What goes on a slide

PM/BD decks are read in a meeting, not studied. Per slide:

- One idea. The title states the point (a sentence), not the topic (a noun).
- At most 6 bullets, at most ~12 words each. Anything longer is speaker notes —
  put it there (`slide.notes_slide.notes_text_frame.text` / `addNotes`).
- Numbers get their own slide or a table; do not bury a figure in prose.
- Three pages asked for = three content pages. A title page is extra only if
  the user's material has a natural title; never pad with an "Agenda" or
  "Thank you" slide unless asked.
- Keep the source's terms. If the notes say 「試玩期」, the slide says 「試玩期」.

## 5. QA — required, every time

1. `python3 .claude/skills/pptx/scripts/check.py out.pptx`
   Reopens the file (a file that does not open is not done), lists every slide
   with its text, and flags: leftover template/sample text, empty slides,
   bullets over ~12 words, slides with more than 6 bullets, missing titles.
   **Fix what it flags and run it again** until it reports clean, or explain in
   your final message exactly why a flag is acceptable.
2. `markitdown out.pptx` and read it as the audience would: does each slide say
   one thing, is the order right, is anything from the source missing?
3. Say in your final message where the file is and what is on each slide, in one
   line per slide. The user downloads the file from this job; they will not see
   your working directory.

## Do not

- Do not produce HTML, Markdown or a PDF instead of a `.pptx` because something
  failed. Fix the failure or say clearly what blocked you.
- Do not `pip install` / `npm install`; it will fail and waste the budget.
- Do not unzip and hand-edit slide XML unless python-pptx genuinely cannot do
  it; if you must, run `check.py` afterwards — PowerPoint refuses malformed XML
  with no useful message.
