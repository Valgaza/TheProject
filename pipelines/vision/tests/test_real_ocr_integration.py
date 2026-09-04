"""The one test in this suite that runs the real PaddleOCR/PP-StructureV3
pipeline, end to end, on CPU.

Skipped by default - matches the root repo's ``RUN_SCENARIOS=1`` convention
for expensive, real-infrastructure tests (see ../../tests/conftest.py at the
repo root). PP-StructureV3 loads several deep learning models and, on this
machine, was measured taking on the order of ten-plus minutes for a single
page with MKL-DNN disabled (see ocr.py's ``enable_mkldnn=False`` comment for
why it's off) - and this test's page has a table, so the default
``refine_tables=True`` adds a second OCR pass on top of that (see
table_refine.py). That's fine for an opt-in confidence check; it's not fine
as part of the default suite.

Run it explicitly with:

    RUN_OCR_TESTS=1 uv run pytest tests/test_real_ocr_integration.py -v
"""
from __future__ import annotations

import os

import pytest

from nexus_vision.pipeline import process_pdf

from fixtures import make_mixed_pdf

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OCR_TESTS") != "1",
    reason="Real PaddleOCR inference is slow on CPU; set RUN_OCR_TESTS=1 to run it.",
)


def test_real_pipeline_on_a_mixed_digital_and_scanned_document(tmp_path):
    path = make_mixed_pdf(tmp_path / "mixed.pdf")

    result = process_pdf(str(path), device="cpu")

    assert len(result["pages"]) == 3
    assert result["pages"][0]["processing_mode"] == "digital"
    assert result["pages"][1]["processing_mode"] == "scanned"
    assert result["pages"][2]["processing_mode"] == "digital"

    scanned_page = result["pages"][1]
    assert scanned_page["error"] is None
    texts = " ".join(el["text"] or "" for el in scanned_page["elements"])
    assert "Scanned Page" in texts

    tables = [el for el in scanned_page["elements"] if el["type"] == "table"]
    assert len(tables) == 1
    table = tables[0]["table"]
    assert "North" in table["html"]
    assert "120000" in table["html"]

    # table -> rows -> cells structure (see ocr.py::_build_table_rows),
    # produced by the default refine_tables=True path (a second, higher-DPI
    # OCR pass on just this table's cropped region - see table_refine.py).
    assert len(table["rows"]) >= 2  # header row + at least one data row
    all_cell_text = " ".join(cell["text"] for row in table["rows"] for cell in row)
    assert "North" in all_cell_text
    assert "120000" in all_cell_text
    for row in table["rows"]:
        for cell in row:
            assert len(cell["bbox"]) == 4
