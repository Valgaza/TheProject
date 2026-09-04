"""Improve a detected table by re-running OCR on just that table's region,
at a higher DPI than the full page was rendered at.

Why this exists: a full page has to stay at a fairly conservative DPI (see
``pdf_io.render_page_to_image``'s docstring - 150 DPI, because larger full
pages reproducibly crashed the local Paddle build). That's a real ceiling on
how small a table's own text can be before OCR struggles with it. A crop of
just the table region, at a much higher DPI, is a far smaller image than a
full page at that DPI would be - which is what makes it safe to raise the
DPI at all - and gives the table structure/text recognition models more
pixels to work with specifically where it matters.

This module only orchestrates: convert the already-detected table's bbox to
a page region, render that region, hand it to the *same* OCR engine instance
used for the full page (no separate pipeline, no extra models to load - see
its own reasoning in ocr.py/README), and remap the result's coordinates back
into the original page's coordinate space. All of the actual table-structure
logic (cells, rows, confidence) lives in ocr.py and is reused unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import replace

import pymupdf

from .ocr import Block, SupportsOcr, TableResult, cluster_into_rows
from .pdf_io import render_region_to_image

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]

# The largest full-page render already proven not to crash this project's
# Paddle build (150 DPI, A4 - see pdf_io.render_page_to_image). Table crops
# are capped to this same pixel budget regardless of the requested DPI, so a
# large table can't reintroduce that crash just because a caller asked for
# 300 DPI - the effective DPI is silently reduced instead.
_MAX_SAFE_PIXELS = 1240 * 1754

# Small margin around a detected table's bbox before cropping, so cell
# borders/text right at the edge of the detection aren't clipped.
_PADDING_POINTS = 6.0


def _safe_dpi(width_points: float, height_points: float, requested_dpi: int) -> int:
    if width_points <= 0 or height_points <= 0:
        return requested_dpi
    total_px = (width_points * requested_dpi / 72) * (height_points * requested_dpi / 72)
    if total_px <= _MAX_SAFE_PIXELS:
        return requested_dpi
    scale = (_MAX_SAFE_PIXELS / total_px) ** 0.5
    return max(72, int(requested_dpi * scale))


def _remap_bbox(bbox: BBox, *, origin_px: tuple[float, float], scale: float) -> BBox:
    x1, y1, x2, y2 = bbox
    ox, oy = origin_px
    return (ox + x1 * scale, oy + y1 * scale, ox + x2 * scale, oy + y2 * scale)


def _union_bbox(boxes: list[BBox]) -> BBox:
    xs1, ys1, xs2, ys2 = zip(*boxes)
    return (min(xs1), min(ys1), max(xs2), max(ys2))


def _merge_table_blocks(tables: list[Block]) -> TableResult:
    """Combine multiple table sub-detections from one crop into one table.

    Cropping tightly to a table and re-running full layout detection on just
    that crop (no surrounding page context) can split what was one table
    into several "table" blocks instead of one - observed running this
    against a real timetable PDF, where a wide table came back as two
    narrower ones. Cells and rows merge cleanly (re-cluster the union of all
    detected cells - the same geometry-only logic ocr.py already uses, so a
    split table reconstructs the same way a single one would). HTML doesn't
    merge as cleanly - two separate `<table>` fragments aren't one coherent
    document - so the sub-table with the most cells is kept as a
    best-effort representative rather than concatenated.
    """
    all_tables = [b.table for b in tables]
    all_cells = [cell for t in all_tables for row in t.rows for cell in row]
    largest = max(all_tables, key=lambda t: len(t.cell_boxes))
    confidences = [t.confidence for t in all_tables if t.confidence is not None]
    return TableResult(
        bbox=_union_bbox([t.bbox for t in all_tables]),
        html=largest.html,
        cell_boxes=[c for t in all_tables for c in t.cell_boxes],
        confidence=(sum(confidences) / len(confidences)) if confidences else None,
        rows=cluster_into_rows(all_cells),
    )


def refine_table_block(
    doc: pymupdf.Document,
    page_index: int,
    block: Block,
    *,
    base_dpi: int,
    table_dpi: int,
    ocr_engine: SupportsOcr,
) -> Block:
    """Re-OCR just this table's region at a higher DPI.

    Returns a replacement ``Block`` with refined table structure, or the
    original ``block`` unchanged if refinement fails, crashes with an
    ordinary exception, or simply doesn't find a table in the crop - a
    table page should end up no worse than the un-refined extraction, never
    worse than that because refinement had a bad day.
    """
    if block.table is None:
        return block

    # block.bbox is in pixel space of the base_dpi full-page render (see
    # ocr.py) - convert to PDF points to crop the actual page, independent
    # of whatever base_dpi happened to be.
    x1, y1, x2, y2 = block.bbox
    to_points = 72 / base_dpi
    padded_rect = (
        x1 * to_points - _PADDING_POINTS,
        y1 * to_points - _PADDING_POINTS,
        x2 * to_points + _PADDING_POINTS,
        y2 * to_points + _PADDING_POINTS,
    )
    # Clip to the page here, once, rather than leaving it to
    # render_region_to_image alone: a table near a page edge can have its
    # padded rect extend past the page boundary (even negative), and the
    # remap below needs to use the *same* origin that was actually rendered,
    # not the pre-clip one - otherwise every coordinate coming back from the
    # refined crop is off by however far the padding overshot the page.
    rect_points = tuple(pymupdf.Rect(padded_rect) & doc[page_index].rect)
    width_points = rect_points[2] - rect_points[0]
    height_points = rect_points[3] - rect_points[1]
    effective_dpi = _safe_dpi(width_points, height_points, table_dpi)

    try:
        crop = render_region_to_image(doc, page_index, rect_points, effective_dpi)
        result = ocr_engine.run(crop)
    except Exception:
        logger.exception(
            "Table refinement failed on page %d; keeping the original extraction for this table",
            page_index + 1,
        )
        return block

    refined_tables = [b for b in result.blocks if b.type == "table" and b.table is not None]
    if not refined_tables:
        logger.warning(
            "Table refinement on page %d found no table in the cropped region; keeping the original extraction",
            page_index + 1,
        )
        return block
    if len(refined_tables) > 1:
        # See _merge_table_blocks: cropping tightly can split one table into
        # several detections when layout detection re-runs without the rest
        # of the page for context.
        logger.info(
            "Table refinement on page %d found %d table regions in the crop; merging them",
            page_index + 1,
            len(refined_tables),
        )
    refined_table = _merge_table_blocks(refined_tables)

    # The crop was rendered starting at rect_points' top-left, at
    # effective_dpi - remap everything back into the original page's pixel
    # space (at base_dpi) so it's consistent with every other element on
    # this page, which is what actually makes this a drop-in replacement.
    origin_px = (rect_points[0] * base_dpi / 72, rect_points[1] * base_dpi / 72)
    scale = base_dpi / effective_dpi

    def remap(bbox: BBox) -> BBox:
        return _remap_bbox(bbox, origin_px=origin_px, scale=scale)

    remapped_table = replace(
        refined_table,
        bbox=remap(refined_table.bbox),
        cell_boxes=[remap(c) for c in refined_table.cell_boxes],
        rows=[[replace(cell, bbox=remap(cell.bbox)) for cell in row] for row in refined_table.rows],
    )
    return replace(block, bbox=remapped_table.bbox, table=remapped_table, confidence=remapped_table.confidence)
