"""Unit tests for the pure table-structure logic in ocr.py - row clustering
and OCR-line-to-cell matching. Deliberately reaches into "private" (leading
underscore) functions: this is real, non-trivial logic worth covering
directly, rather than only indirectly through OcrEngine.run(), which needs
real model weights and minutes of CPU time per call.
"""
from __future__ import annotations

from nexus_vision.ocr import (
    CellResult,
    _build_table_rows,
    cluster_into_rows,
    _match_ocr_lines_in_region,
)


def test_cluster_into_rows_groups_a_clean_grid():
    # A 2x2 grid: two rows, two columns, all cells the same size.
    cells = [
        CellResult(bbox=(0, 0, 100, 40), text="a", confidence=0.9),
        CellResult(bbox=(100, 0, 200, 40), text="b", confidence=0.9),
        CellResult(bbox=(0, 40, 100, 80), text="c", confidence=0.9),
        CellResult(bbox=(100, 40, 200, 80), text="d", confidence=0.9),
    ]
    rows = cluster_into_rows(cells)
    assert len(rows) == 2
    assert [c.text for c in rows[0]] == ["a", "b"]
    assert [c.text for c in rows[1]] == ["c", "d"]


def test_cluster_into_rows_sorts_left_to_right_regardless_of_input_order():
    cells = [
        CellResult(bbox=(100, 0, 200, 40), text="second", confidence=0.9),
        CellResult(bbox=(0, 0, 100, 40), text="first", confidence=0.9),
    ]
    rows = cluster_into_rows(cells)
    assert len(rows) == 1
    assert [c.text for c in rows[0]] == ["first", "second"]


def test_cluster_into_rows_handles_a_single_row():
    cells = [
        CellResult(bbox=(0, 0, 50, 30), text="x", confidence=0.9),
        CellResult(bbox=(50, 2, 100, 28), text="y", confidence=0.9),
    ]
    rows = cluster_into_rows(cells)
    assert len(rows) == 1
    assert len(rows[0]) == 2


def test_cluster_into_rows_empty_input():
    assert cluster_into_rows([]) == []


def test_cluster_into_rows_self_calibrates_to_varying_cell_heights():
    # Row heights differ (40 vs 80) - a fixed tolerance would either merge
    # the tall row into a neighbor or split a short row in two. The
    # tolerance is derived from *this table's own* median cell height, so it
    # should still separate three rows correctly regardless of their
    # absolute size, without any per-document tuning.
    cells = [
        CellResult(bbox=(0, 0, 100, 40), text="r1", confidence=0.9),
        CellResult(bbox=(0, 40, 100, 120), text="r2-tall", confidence=0.9),  # taller row
        CellResult(bbox=(0, 120, 100, 160), text="r3", confidence=0.9),
    ]
    rows = cluster_into_rows(cells)
    assert [ [c.text for c in row] for row in rows ] == [["r1"], ["r2-tall"], ["r3"]]


def test_match_ocr_lines_in_region_returns_only_lines_inside_bbox():
    bbox = (0.0, 0.0, 100.0, 100.0)
    ocr_boxes = [[10, 10, 50, 30], [500, 500, 600, 550]]  # second is far outside
    ocr_scores = [0.9, 0.5]
    ocr_texts = ["inside", "outside"]
    matches = _match_ocr_lines_in_region(bbox, ocr_boxes, ocr_scores, ocr_texts)
    assert matches == [("inside", 0.9)]


def test_match_ocr_lines_in_region_sorts_top_to_bottom_left_to_right():
    bbox = (0.0, 0.0, 200.0, 200.0)
    ocr_boxes = [[100, 100, 150, 120], [0, 0, 50, 20], [60, 0, 110, 20]]
    ocr_scores = [0.9, 0.9, 0.9]
    ocr_texts = ["third", "first", "second"]
    matches = _match_ocr_lines_in_region(bbox, ocr_boxes, ocr_scores, ocr_texts)
    assert [text for text, _ in matches] == ["first", "second", "third"]


def test_build_table_rows_attaches_text_and_confidence_to_the_right_cells():
    table_entry = {
        "cell_box_list": [[0, 0, 100, 40], [100, 0, 200, 40]],
        "table_ocr_pred": {
            "rec_boxes": [[10, 10, 90, 30], [110, 10, 190, 30]],
            "rec_texts": ["Region", "Revenue"],
            "rec_scores": [0.95, 0.9],
        },
    }
    rows = _build_table_rows(table_entry)
    assert len(rows) == 1
    assert [c.text for c in rows[0]] == ["Region", "Revenue"]
    assert rows[0][0].confidence == 0.95
    assert rows[0][1].confidence == 0.9


def test_build_table_rows_cell_with_no_matching_ocr_line_gets_empty_text_and_no_confidence():
    table_entry = {
        "cell_box_list": [[0, 0, 100, 40]],
        "table_ocr_pred": {"rec_boxes": [], "rec_texts": [], "rec_scores": []},
    }
    rows = _build_table_rows(table_entry)
    assert len(rows) == 1
    assert rows[0][0].text == ""
    assert rows[0][0].confidence is None


def test_build_table_rows_no_cell_boxes_returns_no_rows():
    assert _build_table_rows({"cell_box_list": []}) == []
    assert _build_table_rows({}) == []
