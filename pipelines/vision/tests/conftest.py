"""Shared test fixtures.

Most tests inject ``FakeOcrEngine`` instead of the real PaddleOCR-backed
``OcrEngine``: the real one loads several deep learning models and can take
minutes per page on CPU, which is fine for the one opt-in real-engine test
(see test_real_ocr_integration.py) but not for a suite meant to run on every
change. The fake exercises the exact same ``SupportsOcr`` contract
(``run(image) -> StructureResult``), so everything around it - routing,
preprocessing, error isolation, JSON shape - is tested for real.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pytest

from nexus_vision.ocr import Block, CellResult, StructureResult, TableResult


class FakeOcrEngine:
    """Returns a scripted result (or raises) instead of running PaddleOCR.

    Records every image it was called with, so tests can assert on what the
    pipeline actually handed it (e.g. that preprocessing ran first, or that
    a table refinement pass received a cropped region rather than the full
    page). Pass ``results`` (plural) to script a different return value per
    call, in order - needed to test the two-pass table refinement flow
    (base extraction, then a separate re-OCR of the cropped table region).
    The last result repeats if ``run`` is called more times than there are
    scripted results.
    """

    def __init__(
        self,
        result: StructureResult | None = None,
        *,
        results: Sequence[StructureResult] | None = None,
        raises: Exception | None = None,
    ) -> None:
        if results is not None:
            self._results = list(results)
        elif result is not None:
            self._results = [result]
        else:
            self._results = [StructureResult(blocks=[])]
        self._raises = raises
        self.calls: list[np.ndarray] = []

    def run(self, image: np.ndarray) -> StructureResult:
        self.calls.append(image)
        if self._raises is not None:
            raise self._raises
        index = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[index]


class PoisonOcrEngine:
    """Fails the test if OCR is invoked at all - for asserting a digital-only
    PDF never reaches the OCR path.
    """

    def run(self, image: np.ndarray) -> StructureResult:
        raise AssertionError("OCR should not have been invoked for this input")


@pytest.fixture
def fake_text_result() -> StructureResult:
    return StructureResult(
        blocks=[
            Block(type="paragraph_title", text="Scanned Page", bbox=(6, 6, 300, 46), reading_order=0, confidence=0.98),
            Block(type="text", text="Printed body text for OCR.", bbox=(3, 80, 600, 114), reading_order=1, confidence=0.95),
        ]
    )


@pytest.fixture
def fake_table_result() -> StructureResult:
    table = TableResult(
        bbox=(0, 172, 607, 360),
        html="<html><body><table><tr><td>Region</td><td>Revenue</td></tr></table></body></html>",
        cell_boxes=[(3, 172, 307, 238), (307, 178, 602, 241)],
        confidence=0.97,
    )
    return StructureResult(
        blocks=[
            Block(type="paragraph_title", text="Scanned Page", bbox=(6, 6, 300, 46), reading_order=0, confidence=0.98),
            Block(type="table", text=None, bbox=(0, 172, 607, 360), reading_order=1, confidence=0.97, table=table),
        ]
    )


@pytest.fixture
def fake_table_with_rows_result() -> StructureResult:
    """A table result with rows/cells already populated - for tests of
    row/cell serialization and debug visualization, rather than the
    row-building logic itself (see test_ocr.py for that).
    """
    rows = [
        [
            CellResult(bbox=(3, 172, 307, 238), text="Region", confidence=0.95),
            CellResult(bbox=(307, 178, 602, 241), text="Revenue", confidence=0.93),
        ],
        [
            CellResult(bbox=(3, 238, 307, 300), text="North", confidence=0.97),
            CellResult(bbox=(307, 241, 602, 300), text="120000", confidence=0.96),
        ],
    ]
    table = TableResult(
        bbox=(0, 172, 607, 360),
        html="<html><body><table><tr><td>Region</td><td>Revenue</td></tr><tr><td>North</td><td>120000</td></tr></table></body></html>",
        cell_boxes=[cell.bbox for row in rows for cell in row],
        confidence=0.95,
        rows=rows,
    )
    return StructureResult(
        blocks=[Block(type="table", text=None, bbox=(0, 172, 607, 360), reading_order=0, confidence=0.95, table=table)]
    )
