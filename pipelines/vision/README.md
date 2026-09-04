# nexus-vision

Local PDF vision pipeline, V1: turns a PDF into structured JSON, page by
page, choosing per page between direct text extraction and OCR.

Part of Nexus's planned vision pipeline (see the repo root README's
"pipelines/, shared/" note). This is the first slice only: printed text,
tables, and layout from PDFs. Not handwriting, not a VLM, not wired into the
orchestrator yet - see "Intentionally not implemented" below.

## What it does

```
PDF
 └─ for each page:
      has a usable embedded text layer?
        yes → PyMuPDF direct extraction  ─────────────────────┐
        no  → render → preprocess → PP-StructureV3 OCR/layout/tables
                          └─ for each detected table:                │
                               crop table region + re-render at      │
                               higher DPI → re-run OCR → replace     │
                               with the refined table structure ─────┤
                                                                      ▼
                                                       one JSON page shape
```

Two processing paths, chosen independently for every page in the document -
a PDF with digital page 1, a scanned page 2, and digital page 3 processes
each page with the right one:

- **Digital path**: PyMuPDF (`pymupdf.Page.get_text("dict")`) pulls out real
  text blocks with their bounding boxes directly from the PDF's own content
  stream. No image is rendered, no OCR runs.
- **Scanned path**: PyMuPDF renders the page to an image → OpenCV/Pillow
  preprocessing (grayscale, denoise, contrast) → PaddleOCR's PP-StructureV3
  pipeline extracts text, reading order, layout blocks, and table structure
  (as HTML + per-cell boxes) in one pass. Every detected table is then
  **refined**: its own region is cropped from the page and re-rendered at a
  higher DPI than the full page used, and re-OCR'd on its own - small print
  in a table benefits from more pixels than a whole-page render can safely
  provide (see `table_refine.py` and "Known limitations"). On by default;
  `refine_tables=False` / `--no-refine-tables` skips it.

Both paths normalize to the same page/element JSON shape (see below), so a
consumer of this pipeline's output never needs to know which path ran -
except via the `processing_mode` field itself.

## Module layout

```
nexus_vision/
  pdf_io.py      PyMuPDF only: open, inspect, extract text, render pages
  classify.py    "does this page have a usable text layer?" - a handful of
                 documented thresholds (length, text quality, image
                 coverage), not a trained classifier
  preprocess.py  OpenCV/Pillow steps for scanned pages, individually
                 toggleable, none of them mutate their input
  ocr.py         the only module that imports paddleocr; wraps
                 PP-StructureV3 behind a small `SupportsOcr` protocol so a
                 different local OCR engine could be swapped in later. Also
                 builds table -> rows -> cells from PP-StructureV3's raw
                 cell/OCR output (_build_table_rows, _cluster_into_rows)
  table_refine.py  crops a detected table's region, re-renders it at a
                 higher (auto-capped) DPI, re-runs the same OCR engine on
                 just that crop, and remaps the result back into the page's
                 coordinate space
  debug_viz.py   draws detected table/cell bounding boxes onto a page image,
                 for visually checking extraction against the source PDF
  normalize.py   both processing paths -> one page/document JSON shape
  pipeline.py    orchestration: routes each page, isolates per-page
                 failures, logs progress
  __main__.py    `python -m nexus_vision file.pdf` CLI
```

Each module owns exactly one of those responsibilities; `pipeline.py` is the
only one that calls into more than one of the others.

## Why this lives in its own environment

`pipelines/vision/` is **not** a member of the root `uv` workspace
(`../../pyproject.toml`). `paddlepaddle` (PP-StructureV3's underlying
framework) currently ships wheels for Python 3.9-3.13 only; the workspace's
shared `.venv` resolves against whatever newest interpreter satisfies its own
`requires-python` (3.14 at the time this was built). Joining the workspace
would force `core` and `memory`'s dependency resolution to solve around
paddlepaddle's Python ceiling for no benefit to either of them.

Instead, this directory is a self-contained `uv` project: its own
`pyproject.toml`, its own `.venv`, pinned to Python 3.12 via `.python-version`
(3.12.4 was already available locally via `uv python list` - no download
needed on this machine; `uv sync` will fetch a 3.12 interpreter automatically
if one isn't). Nothing in the rest of the repo changed to make this work.

## Setup

```bash
cd pipelines/vision
uv sync
```

First run of the pipeline against a scanned page will additionally download
PP-StructureV3's model weights (a few hundred MB, several models: layout
detection, document orientation/unwarping, text detection/recognition, table
classification/structure/cell-detection) to `~/.paddlex/official_models/`.
That's a one-time download, cached after; the task's "local-only" requirement
is about *runtime inference*, not first-run setup, which this matches -
nothing here calls a cloud OCR/vision API at any point.

## Running it

```bash
# CLI: JSON to stdout, logs to stderr
uv run python -m nexus_vision path/to/file.pdf > output.json

# with options
uv run python -m nexus_vision file.pdf --device cpu --dpi 150

# with table refinement's higher DPI raised, and a debug overlay written out
uv run python -m nexus_vision file.pdf --table-refine-dpi 300 --debug-dir ./debug

# skip the table refinement pass entirely (faster, lower table accuracy)
uv run python -m nexus_vision file.pdf --no-refine-tables
```

As a library:

```python
from nexus_vision import process_pdf

document = process_pdf("file.pdf", device="cpu")  # a plain dict, JSON-serializable
```

## Example output shape

Real output from a 2-page test document (page 1 digital text, page 2 a
scanned page with a table), trimmed for length - `width`/`height` are the
PDF page's own point dimensions (from PyMuPDF), the same regardless of which
path processed the page, not the pixel size of any rendered image:

```json
{
  "filename": "mixed.pdf",
  "document_type": "pdf",
  "pages": [
    {
      "page_number": 1,
      "width": 595.0,
      "height": 842.0,
      "processing_mode": "digital",
      "elements": [
        {"type": "text", "text": "Digital Page", "bbox": [72.0, 60.17, 131.3, 75.29], "confidence": null},
        {"type": "text", "text": "This page has a real embedded text layer.", "bbox": [72.0, 88.18, 277.44, 103.29], "confidence": null}
      ],
      "error": null
    },
    {
      "page_number": 2,
      "width": 595.0,
      "height": 842.0,
      "processing_mode": "scanned",
      "elements": [
        {"type": "paragraph_title", "text": "Scanned Page", "bbox": [7.0, 50.0, 274.0, 92.0], "confidence": 0.9627},
        {
          "type": "table",
          "text": null,
          "bbox": [0.0, 226.0, 614.0, 416.0],
          "confidence": 0.9996,
          "table": {
            "bbox": [0.0, 226.0, 614.0, 416.0],
            "html": "<html><body><table><tr><td>Region</td><td>Revenue</td></tr><tr><td>North</td><td>120000</td></tr><tr><td>South</td><td>98000</td></tr></table></body></html>",
            "cell_boxes": [[5.16, 226.57, 312.9, 294.36], [311.92, 232.08, 612.33, 294.38], "... 4 more cells ..."],
            "confidence": 0.9996,
            "rows": [
              [
                {"bbox": [5.16, 226.57, 312.9, 294.36], "text": "Region", "confidence": 0.999},
                {"bbox": [311.92, 232.08, 612.33, 294.38], "text": "Revenue", "confidence": 0.999}
              ],
              [
                {"bbox": [3.2, 294.4, 310.1, 357.5], "text": "North", "confidence": 0.9996},
                {"bbox": [309.8, 293.3, 613.2, 357.4], "text": "120000", "confidence": 0.9996}
              ],
              "... 1 more row ..."
            ]
          }
        }
      ],
      "error": null
    }
  ]
}
```

Notes on the schema, since it's derived from what the underlying libraries
actually return rather than invented:

- `confidence` is `null` for digital-path elements - PyMuPDF's direct
  extraction isn't a recognition process, so there is no confidence score to
  report, and hardcoding one (e.g. `1.0`) would misrepresent it as measured.
- `type` is passed through verbatim from PaddleOCR's own layout labels
  (`"text"`, `"paragraph_title"`, `"table"`, `"image"`, ...) rather than
  mapped onto a fixed enum, so a future label (e.g. a model that starts
  distinguishing handwriting) shows up automatically instead of silently
  falling through unrecognized.
- `bbox` is always `[x1, y1, x2, y2]` (top-left, bottom-right), consistently
  across digital and scanned pages, top-level elements, and table cells.
- A table gets **two** structural representations, deliberately kept
  side by side rather than one replacing the other:
  - `table.html` is PP-StructureV3's own output - the authoritative
    structure, since it natively encodes headers, rows, and
    merged/spanning cells (via `colspan`/`rowspan`) exactly as the model
    detected them.
  - `table.rows` (table -> rows -> cells) is *derived* from
    `table.cell_boxes` by this pipeline (see `ocr.py::_build_table_rows`),
    for callers that want per-cell bounding boxes, text, and confidence
    without parsing HTML. It's a geometric reconstruction (row-clustering
    by vertical position - see "Known limitations"), not something
    PP-StructureV3 returns directly, so treat `html` as authoritative for
    logical structure (merges especially) and `rows` as authoritative for
    per-cell geometry.
- A page that failed processing keeps its place in `pages` with `error` set
  and empty `elements`, rather than being dropped or failing the whole
  document.

## CPU/GPU configuration

`device` is threaded through from `process_pdf(..., device=...)` /
`--device` straight to PP-StructureV3 - `"cpu"` (default), `"gpu"`,
`"gpu:0"`, etc. Nothing in the pipeline hardcodes a device. `table_refine_dpi` /
`--table-refine-dpi` controls the table-refinement pass's DPI target
(default 300) independently of the full-page `dpi` (default 150) - see
`table_refine.py` for how a large table's effective DPI is automatically
reduced to stay within the same pixel budget that keeps the full-page
render safe (see "Known limitations").

## Known limitations

- **Table refinement can only refine the region the base pass detected as a
  table - it can't recover a table boundary the base layout model got wrong
  in the first place.** Running this against the real sample document
  (`sample_docs/Comp MSE TT 26-27(Jul-Nov).PDF`, a scanned exam timetable
  with the table content rotated 90 degrees relative to the page) with
  `--debug-dir` on showed both pages' table boxes covering only *part* of
  the visibly continuous table - roughly the top half on page 1, missing an
  entire "TY B.Tech SEM V" row entirely; a portion cut off on page 2, with
  the box additionally bleeding into non-table footer/signature text below
  it. Refinement re-OCR'd exactly what was inside those (incomplete)
  boxes correctly - the cells present were accurate - but had no way to
  recover the content outside them, since it only ever sees the crop the
  base pass already decided was "the table." This is a base-model
  layout-detection accuracy limitation on this particular
  rotated/complex-layout document, not a bug in this pipeline's own
  cropping/remapping/merging logic - and it's exactly the kind of thing
  `--debug-dir`'s visual verification exists to surface rather than hide.
  Not something this pipeline attempts to work around (e.g. by heuristically
  expanding a table's box) - that would mean second-guessing the layout
  model with a heuristic tuned to this one document, which cuts against
  staying generic.
- **Table refinement found a wide table split into two separate "table"
  blocks on an earlier run against the same document**, when cropping to a
  wide table and re-running layout detection on just that crop (no
  surrounding page for context) produced two narrower table detections
  instead of one. Fixed by merging all table blocks the refinement pass
  finds rather than keeping only the first (see
  `table_refine.py::_merge_table_blocks` and
  `tests/test_table_refine.py::test_refine_table_block_merges_multiple_table_detections_from_one_crop`).
  Cells/rows merge cleanly (geometry-only reconstruction, same as a single
  table); `table.html` keeps the larger sub-table's HTML as a best-effort
  representative, since two separate HTML fragments can't merge into one
  coherent document.
- **A table near a page edge previously came back with every coordinate
  shifted**, also found via `--debug-dir` against the same real document: the
  small padding added around a table's crop region pushed the padded
  rect's origin to a negative coordinate for a table starting near x=0, and
  the remap used that unclipped (wrong) origin while the actual render had
  clipped it to the page boundary - a mismatch between what was rendered and
  what the remap assumed. Fixed by clipping the crop rect once and using
  *that* rect consistently for both the render and the remap (see
  `table_refine.py::refine_table_block` and
  `tests/test_table_refine.py::test_refine_table_block_remaps_correctly_when_table_touches_the_page_edge`).
- **Default render DPI is 150, not the more commonly recommended 300.** A
  full page rendered at 300 DPI (~2480x3508px for A4) reproducibly segfaulted
  inside Paddle's native inference code on this development machine; the same
  page at 150 DPI (~1240x1754px) processed correctly. Isolated by holding
  page content and preprocessing constant and varying only render size -
  confirmed the crash tracks image size, not preprocessing or page content.
  This is a real accuracy tradeoff (smaller print is harder to OCR at lower
  DPI), not an arbitrary default - see `pdf_io.render_page_to_image`'s
  docstring. Worth re-testing at 300 DPI against a different
  paddlepaddle version or a GPU device before trusting it in production.
- **CPU inference is slow, and table refinement doubles it for table-heavy
  pages.** PP-StructureV3 runs several models per scanned page (layout
  detection, orientation/unwarping, table classification and structure, text
  detection and recognition). On this development machine (no dedicated
  GPU), a single page took several minutes even at the reduced 150 DPI -
  and `refine_tables=True` (the default) runs the *same* full pipeline a
  second time per detected table, on its cropped region. `refine_tables=False`
  / `--no-refine-tables` trades that accuracy back for speed. A GPU changes
  the underlying cost substantially either way; `device="gpu"` is there for
  exactly that.
- **Table refinement's higher DPI is capped by a fixed pixel budget, not a
  fixed DPI.** `table_refine.py::_safe_dpi` never renders a table crop larger
  than the same pixel count proven safe for a full page at 150 DPI (see the
  point above) - so a large table silently gets a lower effective DPI than
  requested rather than risking the same crash. Chosen conservatively rather
  than tuned per-table-size; a small table gets the full requested DPI (300
  by default), a page-spanning table gets meaningfully less.
- **MKL-DNN is disabled** (`enable_mkldnn=False` in `ocr.py`). The installed
  paddlepaddle 3.3.1's CPU/oneDNN execution path raised a native
  `NotImplementedError` inside the layout detection model with it on,
  reproduced locally. This is a real accuracy/speed tradeoff, not a style
  choice - MKL-DNN is paddlepaddle's primary CPU acceleration path, so
  turning it off costs real throughput. Revisit when paddlepaddle ships a fix.
- **Table matching is positional.** A "table" block in the reading-order list
  and its detailed structure (HTML, cell boxes) live in two separate lists
  PP-StructureV3 returns, with no shared id between them; they're matched by
  assuming both lists share document order (see `ocr.py::_match_tables`).
  Verified against a single-table page; a page with multiple tables hasn't
  been tested against this assumption.
- **`table.rows` handles merged cells approximately, not exactly.** Row
  grouping is geometric (cluster detected cell boxes by vertical position -
  see `ocr.py::_cluster_into_rows`), which has no concept of "this cell
  spans two rows." A merged cell's larger bbox lands in whichever row its
  center is closest to and appears once there, rather than being represented
  as spanning multiple rows/columns. `table.html` (via PP-StructureV3's own
  `colspan`/`rowspan`) is unaffected and remains the authoritative structure
  for tables with real merges - see the schema notes above.
- **The digital/scanned decision is still a handful of thresholds, not a
  classifier** - now three of them (text length, text quality, image
  coverage) rather than one. Originally just a length check, until running
  V1 against a real scanned exam timetable PDF (`Comp MSE TT
  26-27(Jul-Nov).PDF`) showed it misrouting: the page had >20 characters of
  embedded text, but that text decoded to mojibake through a broken font
  encoding, and PathA silently "succeeded" while extracting garbage. See
  `classify.py`'s module docstring for the real numbers this was calibrated
  against, and `tests/test_classify.py` /
  `tests/test_pipeline.py::test_garbled_text_with_full_page_image_routes_through_ocr`
  for the regression coverage built from that exact case. Both new checks
  are threshold-based and can still be fooled - e.g. a legitimate page of
  mostly numeric data (a phone list, a pure-digit table) would score low on
  the word-likeness check and get routed to OCR unnecessarily. Not observed
  in testing, but worth knowing about.
- **No password-protected PDF support.** Encrypted PDFs raise `PdfOpenError`
  rather than prompting for a password.

## Intentionally not implemented (out of scope for V1)

Per the task this pipeline was built against: handwriting recognition, VLMs,
RAG, Neo4j/memory integration, agents, LLM-based OCR correction, cloud vision
APIs, vector databases. `ocr.py`'s `Block.type` is a plain string rather than
a closed enum specifically so that a future model producing a `"handwriting"`
or `"signature"` label would flow through unchanged - but no such model is
wired in here.

## Testing

```bash
uv run pytest                      # fast suite: ~64 tests, well under a minute, no real OCR
RUN_OCR_TESTS=1 uv run pytest tests/test_real_ocr_integration.py -v   # real PP-StructureV3, slow
```

Most tests inject a fake OCR engine (`tests/conftest.py::FakeOcrEngine`)
satisfying the same `SupportsOcr` contract the real one does, so routing,
preprocessing, error isolation, and the JSON schema are all tested for real
without paying for model inference on every run - the real engine was
already exercised directly (see `ocr.py`'s schema notes) to derive the
normalization logic in the first place. Exactly one test
(`test_real_ocr_integration.py`) runs the real pipeline end to end, and is
skipped by default because it's slow on CPU (see "Known limitations") -
matches the repo root's `RUN_SCENARIOS=1` convention for expensive,
real-infrastructure tests.

Test fixtures (`tests/fixtures.py`) build synthetic PDFs/images on the fly
with PyMuPDF and Pillow rather than committing binary PDF files to the repo.
