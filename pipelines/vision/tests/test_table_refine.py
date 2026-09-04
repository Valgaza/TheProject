"""Unit/integration tests for table_refine.py: the safe-DPI cap (pure math)
and the crop -> re-OCR -> remap flow (using FakeOcrEngine, so no real model
inference - see conftest.py).
"""
from __future__ import annotations

import pymupdf
import pytest

from nexus_vision.ocr import Block, CellResult, StructureResult, TableResult
from nexus_vision.table_refine import _MAX_SAFE_PIXELS, _merge_table_blocks, _safe_dpi, refine_table_block

from conftest import FakeOcrEngine


# --- _safe_dpi: pure math, no I/O -------------------------------------


def test_safe_dpi_returns_requested_dpi_when_under_budget():
    # A small region (say 100x100pt) at 300 DPI is well under the pixel cap.
    assert _safe_dpi(100, 100, 300) == 300


def test_safe_dpi_reduces_dpi_for_a_large_region():
    # A full A4 page's worth of points at 300 DPI is exactly the size that
    # crashed the pipeline in V1 (see pdf_io.render_page_to_image) - must be
    # capped, not passed through.
    reduced = _safe_dpi(595, 842, 300)
    assert reduced < 300
    px = (595 * reduced / 72) * (842 * reduced / 72)
    assert px <= _MAX_SAFE_PIXELS


def test_safe_dpi_has_a_sane_floor():
    # Even an enormous region shouldn't be reduced to something unusable.
    assert _safe_dpi(5000, 5000, 300) >= 72


def test_safe_dpi_handles_degenerate_zero_size_region():
    assert _safe_dpi(0, 0, 300) == 300


# --- _merge_table_blocks: combining split table detections ----------------


def _table_result(bbox, cell_boxes, confidence=0.9):
    cells = [CellResult(bbox=c, text=f"t{i}", confidence=confidence) for i, c in enumerate(cell_boxes)]
    return TableResult(bbox=bbox, html=f"<table><!-- {len(cell_boxes)} cells --></table>", cell_boxes=cell_boxes, confidence=confidence, rows=[cells])


def test_merge_table_blocks_unions_bboxes_and_cells():
    left = Block(type="table", text=None, bbox=(0, 0, 100, 50), reading_order=0, confidence=0.9,
                 table=_table_result((0, 0, 100, 50), [(0, 0, 50, 50), (50, 0, 100, 50)], confidence=0.9))
    right = Block(type="table", text=None, bbox=(100, 0, 200, 50), reading_order=1, confidence=0.8,
                  table=_table_result((100, 0, 200, 50), [(100, 0, 150, 50), (150, 0, 200, 50)], confidence=0.8))

    merged = _merge_table_blocks([left, right])

    assert merged.bbox == (0, 0, 200, 50)  # union of both sub-table bboxes
    assert len(merged.cell_boxes) == 4  # all cells from both sub-tables kept
    assert sum(len(row) for row in merged.rows) == 4
    assert merged.confidence == pytest.approx((0.9 + 0.8) / 2)


def test_merge_table_blocks_picks_html_from_the_largest_sub_table():
    small = Block(type="table", text=None, bbox=(0, 0, 50, 50), reading_order=0, confidence=0.9,
                  table=_table_result((0, 0, 50, 50), [(0, 0, 50, 50)]))
    big = Block(type="table", text=None, bbox=(50, 0, 250, 50), reading_order=1, confidence=0.9,
                table=_table_result((50, 0, 250, 50), [(50, 0, 100, 50), (100, 0, 150, 50), (150, 0, 250, 50)]))

    merged = _merge_table_blocks([small, big])

    assert "3 cells" in merged.html  # the sub-table with more cells


def test_merge_table_blocks_single_table_is_a_passthrough():
    only = Block(type="table", text=None, bbox=(0, 0, 100, 50), reading_order=0, confidence=0.9,
                 table=_table_result((0, 0, 100, 50), [(0, 0, 100, 50)]))

    merged = _merge_table_blocks([only])

    assert merged.bbox == only.table.bbox
    assert merged.cell_boxes == only.table.cell_boxes


# --- refine_table_block: crop -> re-OCR -> remap ------------------------


def _one_page_doc() -> pymupdf.Document:
    doc = pymupdf.open()
    doc.new_page()  # default A4-ish size
    return doc


def _table_block(bbox=(60.0, 240.0, 660.0, 480.0)) -> Block:
    table = TableResult(bbox=bbox, html="<table></table>", cell_boxes=[], confidence=0.8)
    return Block(type="table", text=None, bbox=bbox, reading_order=0, confidence=0.8, table=table)


def test_refine_table_block_remaps_correctly_when_table_touches_the_page_edge():
    # Regression test for a real bug found running this against an actual
    # PDF: a table whose bbox starts at/near x=0 gets a *negative* padded
    # crop rect (x0 - _PADDING_POINTS < 0). render_region_to_image clips
    # that to the page internally, but the remap must clip identically
    # first - otherwise the remap origin doesn't match what was actually
    # rendered, and every coordinate in the refined table comes back
    # shifted by the padding amount. Debug-visualized to find this: cells
    # rendered far outside the table's own bounding box.
    doc = _one_page_doc()
    base_dpi = 150
    # bbox starts at x=0, y=0 - the padded rect would be (-6, -6, ...) in
    # points before clipping.
    original_block = _table_block(bbox=(0.0, 0.0, 300.0, 200.0))

    refined_table = TableResult(
        bbox=(0.0, 0.0, 20.0, 20.0),
        html="<table><tr><td>x</td></tr></table>",
        cell_boxes=[(0.0, 0.0, 20.0, 20.0)],
        confidence=0.9,
        rows=[[CellResult(bbox=(0.0, 0.0, 20.0, 20.0), text="x", confidence=0.9)]],
    )
    refined_result = StructureResult(
        blocks=[Block(type="table", text=None, bbox=(0.0, 0.0, 20.0, 20.0), reading_order=0, confidence=0.9, table=refined_table)]
    )
    engine = FakeOcrEngine(refined_result)

    result = refine_table_block(doc, 0, original_block, base_dpi=base_dpi, table_dpi=300, ocr_engine=engine)

    # The refined cell's crop-local origin (0,0) must map back to
    # approximately the *page's* origin (0,0), not to a negative coordinate.
    assert result.table.bbox[0] >= 0.0
    assert result.table.bbox[1] >= 0.0
    assert result.table.rows[0][0].bbox[0] >= 0.0
    doc.close()


def test_refine_table_block_returns_original_unchanged_when_block_has_no_table():
    doc = _one_page_doc()
    block = Block(type="text", text="hi", bbox=(0, 0, 10, 10), reading_order=0, confidence=0.9)
    engine = FakeOcrEngine()

    result = refine_table_block(doc, 0, block, base_dpi=150, table_dpi=300, ocr_engine=engine)

    assert result is block
    assert engine.calls == []  # never even tried
    doc.close()


def test_refine_table_block_replaces_table_with_remapped_refined_result():
    doc = _one_page_doc()
    base_dpi = 150
    original_block = _table_block(bbox=(60.0, 240.0, 660.0, 480.0))

    # Simulate the refinement pass finding one cell near the crop's origin.
    refined_table = TableResult(
        bbox=(0.0, 0.0, 50.0, 50.0),
        html="<table><tr><td>x</td></tr></table>",
        cell_boxes=[(2.0, 2.0, 48.0, 48.0)],
        confidence=0.99,
        rows=[[CellResult(bbox=(2.0, 2.0, 48.0, 48.0), text="x", confidence=0.99)]],
    )
    refined_result = StructureResult(
        blocks=[Block(type="table", text=None, bbox=(0.0, 0.0, 50.0, 50.0), reading_order=0, confidence=0.99, table=refined_table)]
    )
    engine = FakeOcrEngine(refined_result)

    result = refine_table_block(doc, 0, original_block, base_dpi=base_dpi, table_dpi=300, ocr_engine=engine)

    assert len(engine.calls) == 1  # the refinement pass actually ran
    assert result is not original_block
    assert result.table.confidence == 0.99
    # The refined table's origin was (0,0) in crop-local space; remapped
    # back, it should land close to the original block's own top-left corner
    # in base-dpi pixel space (within the small fixed padding
    # refine_table_block adds around the crop before rendering it) - not at
    # the crop-local (0,0), which is what a broken remap would produce.
    assert result.table.bbox[0] == pytest.approx(original_block.bbox[0], abs=15.0)
    assert result.table.bbox[1] == pytest.approx(original_block.bbox[1], abs=15.0)
    assert len(result.table.rows) == 1
    assert result.table.rows[0][0].text == "x"
    doc.close()


def test_refine_table_block_merges_multiple_table_detections_from_one_crop():
    # Regression test for a real bug found running this against an actual
    # PDF: cropping tightly to a wide table and re-running layout detection
    # on just that crop (no page context) split it into two "table" blocks
    # instead of one, and taking only the first one silently dropped half
    # the table. Both must be merged, not just the first kept.
    doc = _one_page_doc()
    original_block = _table_block(bbox=(60.0, 240.0, 660.0, 480.0))

    left = TableResult(bbox=(0.0, 0.0, 25.0, 20.0), html="<table>left</table>", cell_boxes=[(2.0, 2.0, 23.0, 18.0)], confidence=0.9,
                        rows=[[CellResult(bbox=(2.0, 2.0, 23.0, 18.0), text="left", confidence=0.9)]])
    right = TableResult(bbox=(25.0, 0.0, 50.0, 20.0), html="<table>right</table>", cell_boxes=[(27.0, 2.0, 48.0, 18.0)], confidence=0.9,
                         rows=[[CellResult(bbox=(27.0, 2.0, 48.0, 18.0), text="right", confidence=0.9)]])
    split_result = StructureResult(
        blocks=[
            Block(type="table", text=None, bbox=(0.0, 0.0, 25.0, 20.0), reading_order=0, confidence=0.9, table=left),
            Block(type="table", text=None, bbox=(25.0, 0.0, 50.0, 20.0), reading_order=1, confidence=0.9, table=right),
        ]
    )
    engine = FakeOcrEngine(split_result)

    result = refine_table_block(doc, 0, original_block, base_dpi=150, table_dpi=300, ocr_engine=engine)

    assert len(result.table.cell_boxes) == 2  # both sub-tables' cells present
    all_text = [cell.text for row in result.table.rows for cell in row]
    assert set(all_text) == {"left", "right"}
    doc.close()


def test_refine_table_block_falls_back_when_the_engine_raises():
    doc = _one_page_doc()
    block = _table_block()
    engine = FakeOcrEngine(raises=RuntimeError("simulated crop OCR failure"))

    result = refine_table_block(doc, 0, block, base_dpi=150, table_dpi=300, ocr_engine=engine)

    assert result is block  # unchanged, not propagated as a failure
    doc.close()


def test_refine_table_block_falls_back_when_no_table_found_in_the_crop():
    doc = _one_page_doc()
    block = _table_block()
    # The crop pass finds text but no table - a plausible outcome if the
    # crop was slightly off or the table was misdetected the first time.
    no_table_result = StructureResult(
        blocks=[Block(type="text", text="not a table", bbox=(0, 0, 10, 10), reading_order=0, confidence=0.9)]
    )
    engine = FakeOcrEngine(no_table_result)

    result = refine_table_block(doc, 0, block, base_dpi=150, table_dpi=300, ocr_engine=engine)

    assert result is block
    doc.close()
