"""OCR + layout + table extraction for scanned pages, via PaddleOCR's
PP-StructureV3 pipeline.

Only this module imports ``paddleocr``. Everything downstream (normalize.py,
pipeline.py) works with the plain dataclasses defined here, so a future swap
to a different local OCR engine only means rewriting this file and keeping
the same ``OcrEngine.run() -> StructureResult`` contract.

Schema notes (verified empirically against paddleocr==3.7.0 / paddlex==3.7.2
by running the real pipeline against a synthetic page, since the hosted docs
lag the actual API):

- ``result.json`` is an in-memory dict, ``{"res": {...}}`` - no need to
  round-trip through ``save_to_json()`` and read a file back.
- ``res["parsing_res_list"]`` is a list of layout blocks *already in reading
  order* - that's the list position, not the ``block_order`` field, which is
  ``None`` for at least the "table" block type.
- ``res["table_res_list"]`` holds one entry per table block, with
  ``pred_html`` (the table as HTML), ``cell_box_list`` (per-cell boxes) and
  ``table_ocr_pred`` (that table's own rec_texts/rec_scores). There is no
  shared id between a "table" entry in parsing_res_list and its entry in
  table_res_list; both appear to follow the same document order, so tables
  are matched positionally (see ``_match_tables``). Confirmed on a
  single-table synthetic page; a page with multiple tables is not covered by
  that test, so this assumption is the one place a future ordering change in
  paddleocr would silently produce a mismatched table.
- All bounding boxes across the result (block_bbox, layout coordinate,
  cell_box_list, OCR rec_boxes) are ``[x1, y1, x2, y2]`` rectangles already,
  not four-point polygons, matching the schema this pipeline uses throughout.

``TableResult.rows`` (table -> rows -> cells, with per-cell bbox/text/
confidence) is *derived* from ``cell_box_list`` - see ``_build_table_rows``
and ``cluster_into_rows`` - not something PP-StructureV3 returns directly.
``table_refine.py`` additionally re-runs this same engine on a cropped,
higher-DPI render of just the table region, for tables where the full-page
DPI wasn't enough resolution for small print - see that module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class CellResult:
    """One table cell, geometrically detected rather than reconstructed
    from raw OCR text positions - see ``_build_table_rows``.
    """

    bbox: BBox
    text: str
    confidence: float | None


@dataclass(frozen=True)
class TableResult:
    bbox: BBox
    html: str
    cell_boxes: list[BBox]
    confidence: float | None
    # Cells grouped into rows, left-to-right within each row, top-to-bottom
    # across rows - see _build_table_rows. Empty when the table structure
    # model returned no cell boxes to group.
    rows: list[list[CellResult]] = field(default_factory=list)


@dataclass(frozen=True)
class Block:
    """One layout block, already in the page's reading order.

    ``type`` is PaddleOCR's block_label passed through verbatim (e.g.
    "text", "paragraph_title", "table", "image") rather than an enum, so a
    future model that adds a new label - "handwriting", say - shows up
    automatically instead of falling through a case statement someone forgot
    to update.
    """

    type: str
    text: str | None
    bbox: BBox
    reading_order: int
    confidence: float | None
    table: TableResult | None = None


@dataclass(frozen=True)
class StructureResult:
    blocks: list[Block]
    engine: str = "paddleocr-ppstructurev3"


def _prepare_for_inference(image: np.ndarray) -> np.ndarray:
    """3-channel, contiguous, and owning its own memory.

    A defensive copy: PyMuPDF's pixmap buffer (``np.frombuffer(pix.samples,
    ...)``) is a read-only view, and handing a native inference backend an
    array it doesn't own is a real class of bug even though it wasn't, in the
    end, the cause of the crash this pipeline actually hit (see
    ``pdf_io.render_page_to_image`` and the README's "Known limitations" -
    that one tracked to render size, at 300 DPI, not buffer ownership).
    Cheap enough to keep as a safeguard regardless.
    """
    import cv2

    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    return np.ascontiguousarray(image).copy()


def _bbox_center(box: BBox) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, (y1 + y2) / 2


def _contains(outer: BBox, point: tuple[float, float]) -> bool:
    x1, y1, x2, y2 = outer
    px, py = point
    return x1 <= px <= x2 and y1 <= py <= y2


def _match_ocr_lines_in_region(
    bbox: BBox,
    ocr_boxes: list[list[float]],
    ocr_scores: list[float],
    ocr_texts: list[str] | None = None,
) -> list[tuple[str, float]]:
    """OCR text lines whose center falls inside bbox, in reading order.

    This is how both a layout block's confidence and a table cell's text are
    derived: neither parsing_res_list blocks nor cell_box_list entries carry
    their own confidence or (in the cell case) matched text - PaddleOCR gives
    us page-wide (or table-wide) OCR line boxes/texts/scores separately, and
    a line "belongs" to a region if the line's center point falls inside it.
    Simple, and reliable for the non-overlapping boxes PP-StructureV3 produces.
    """
    texts = ocr_texts if ocr_texts is not None else [""] * len(ocr_boxes)
    matched = [
        (box, text, score)
        for box, text, score in zip(ocr_boxes, texts, ocr_scores)
        if _contains(bbox, _bbox_center(tuple(box[:4])))
    ]
    matched.sort(key=lambda m: (_bbox_center(tuple(m[0][:4]))[1], _bbox_center(tuple(m[0][:4]))[0]))
    return [(text, score) for _, text, score in matched]


def _mean_confidence_in_region(bbox: BBox, ocr_boxes: list[list[float]], ocr_scores: list[float]) -> float | None:
    scores = [score for _, score in _match_ocr_lines_in_region(bbox, ocr_boxes, ocr_scores)]
    return (sum(scores) / len(scores)) if scores else None


def cluster_into_rows(cells: list[CellResult]) -> list[list[CellResult]]:
    """Group detected cells into rows by vertical position, sort each row
    left-to-right.

    A generic, geometry-only reconstruction - no assumption about column
    count or table shape. The clustering tolerance is half the *median* cell
    height across this specific table's own detected cells, not a fixed
    number, so it self-calibrates to whatever font size/DPI produced these
    boxes rather than being tuned to one document.

    A cell spanning multiple rows or columns (a merged cell) has a larger
    bbox than its neighbors and lands in whichever row its vertical center is
    closest to - it appears once, in one row, rather than being duplicated
    across the rows/columns it visually spans. Good enough for a generic
    heuristic; not a full rowspan/colspan solver.
    """
    if not cells:
        return []

    heights = sorted(c.bbox[3] - c.bbox[1] for c in cells)
    median_height = heights[len(heights) // 2]
    tolerance = max(median_height / 2, 1.0)

    by_y = sorted(cells, key=lambda c: _bbox_center(c.bbox)[1])
    rows: list[list[CellResult]] = []
    current_row: list[CellResult] = []
    current_row_y = 0.0
    for cell in by_y:
        y_center = _bbox_center(cell.bbox)[1]
        if current_row and abs(y_center - current_row_y) > tolerance:
            rows.append(sorted(current_row, key=lambda c: _bbox_center(c.bbox)[0]))
            current_row = []
        current_row.append(cell)
        # Running average keeps the row's reference y stable as cells are added.
        current_row_y = sum(_bbox_center(c.bbox)[1] for c in current_row) / len(current_row)
    if current_row:
        rows.append(sorted(current_row, key=lambda c: _bbox_center(c.bbox)[0]))
    return rows


def _build_table_rows(table_entry: dict) -> list[list[CellResult]]:
    """Attach text/confidence to each detected cell box, then group into rows.

    ``cell_box_list`` (from PP-StructureV3's dedicated cell-detection model)
    is the authoritative cell geometry - this does not reconstruct cells from
    raw OCR text boxes, only enriches already-detected cells with the OCR
    text/confidence that falls inside each one.
    """
    cell_boxes = [tuple(float(v) for v in c[:4]) for c in table_entry.get("cell_box_list", [])]
    if not cell_boxes:
        return []

    ocr = table_entry.get("table_ocr_pred") or {}
    ocr_boxes = ocr.get("rec_boxes", [])
    ocr_texts = ocr.get("rec_texts", [])
    ocr_scores = ocr.get("rec_scores", [])

    cells = []
    for bbox in cell_boxes:
        matches = _match_ocr_lines_in_region(bbox, ocr_boxes, ocr_scores, ocr_texts)
        text = " ".join(t for t, _ in matches if t)
        confidence = (sum(s for _, s in matches) / len(matches)) if matches else None
        cells.append(CellResult(bbox=bbox, text=text, confidence=confidence))

    return cluster_into_rows(cells)


def _match_tables(parsing_res_list: list[dict], table_res_list: list[dict]) -> dict[int, dict]:
    """Map parsing_res_list index -> its table_res_list entry, positionally.

    See the module docstring for why this is positional rather than keyed.
    """
    table_indices = [i for i, b in enumerate(parsing_res_list) if b.get("block_label") == "table"]
    if len(table_indices) != len(table_res_list):
        logger.warning(
            "table count mismatch: %d table blocks vs %d table_res_list entries; "
            "matching as many as line up positionally and dropping the rest",
            len(table_indices),
            len(table_res_list),
        )
    return dict(zip(table_indices, table_res_list))


class SupportsOcr(Protocol):
    """What ``pipeline.py`` actually depends on.

    Tests inject a fake implementing just this (instant, no model weights)
    instead of the real ``OcrEngine`` (which loads and runs several deep
    learning models). A future alternative OCR engine only needs to satisfy
    this shape.
    """

    def run(self, image: np.ndarray) -> StructureResult: ...


class OcrEngine:
    """Thin adapter around PPStructureV3. Swap this class to change OCR engines."""

    def __init__(self, device: str = "cpu") -> None:
        # Imported lazily and only here: this pulls in paddle, which is a
        # heavy, slow import that a pure digital-PDF (PathA-only) run should
        # never have to pay for.
        from paddleocr import PPStructureV3

        self._pipeline = PPStructureV3(
            device=device,
            # Works around a paddlepaddle 3.3.1 CPU bug where its PIR
            # executor + oneDNN raises `NotImplementedError:
            # ConvertPirAttribute2RuntimeAttribute ... not support
            # [pir::ArrayAttribute<pir::DoubleAttribute>]` inside the layout
            # detection model. Confirmed locally; revisit once paddlepaddle
            # ships a fix. Has no effect on GPU device strings.
            enable_mkldnn=False,
            # Out of scope for V1 (printed text only - see README).
            use_formula_recognition=False,
            use_seal_recognition=False,
            use_chart_recognition=False,
        )

    def run(self, image: np.ndarray) -> StructureResult:
        image = _prepare_for_inference(image)
        outputs = list(self._pipeline.predict(image))
        if not outputs:
            return StructureResult(blocks=[])

        res = outputs[0].json["res"]
        parsing_res_list = res.get("parsing_res_list", [])
        table_res_list = res.get("table_res_list", [])
        overall_ocr = res.get("overall_ocr_res", {}) or {}
        ocr_boxes = overall_ocr.get("rec_boxes", [])
        ocr_scores = overall_ocr.get("rec_scores", [])

        table_matches = _match_tables(parsing_res_list, table_res_list)

        blocks: list[Block] = []
        for i, raw in enumerate(parsing_res_list):
            bbox = tuple(float(v) for v in raw["block_bbox"])
            label = raw.get("block_label", "unknown")
            content = raw.get("block_content")

            table: TableResult | None = None
            confidence: float | None
            if label == "table" and i in table_matches:
                t = table_matches[i]
                t_scores = (t.get("table_ocr_pred") or {}).get("rec_scores") or []
                table = TableResult(
                    bbox=bbox,
                    html=t.get("pred_html", content or ""),
                    cell_boxes=[tuple(float(v) for v in c[:4]) for c in t.get("cell_box_list", [])],
                    confidence=(sum(t_scores) / len(t_scores)) if t_scores else None,
                    rows=_build_table_rows(t),
                )
                confidence = table.confidence
                content = None  # the HTML lives on `table.html`; don't duplicate it as plain text
            else:
                confidence = _mean_confidence_in_region(bbox, ocr_boxes, ocr_scores)

            blocks.append(
                Block(
                    type=label,
                    text=content,
                    bbox=bbox,
                    reading_order=i,
                    confidence=confidence,
                    table=table,
                )
            )

        return StructureResult(blocks=blocks)
