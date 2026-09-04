"""Convert either processing path's output into one consistent page/document
JSON shape.

This is the one place that knows both PathA's ``TextBlock`` (from pdf_io) and
PathB's ``Block``/``StructureResult`` (from ocr). Nothing downstream needs to
know which path produced a given page - a page's JSON looks the same either
way, aside from the ``processing_mode`` field itself and the fact that only
scanned pages ever populate ``confidence``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .pdf_io import PageInfo, TextBlock
from .ocr import StructureResult


@dataclass
class ElementOut:
    type: str
    text: str | None
    bbox: list[float] | None
    confidence: float | None
    table: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "text": self.text,
            "bbox": self.bbox,
            "confidence": self.confidence,
        }
        if self.table is not None:
            d["table"] = self.table
        return d


@dataclass
class PageOut:
    page_number: int
    width: float
    height: float
    processing_mode: str  # "digital" | "scanned"
    elements: list[ElementOut] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "width": self.width,
            "height": self.height,
            "processing_mode": self.processing_mode,
            "elements": [e.to_dict() for e in self.elements],
            "error": self.error,
        }


@dataclass
class DocumentOut:
    filename: str
    document_type: str = "pdf"
    pages: list[PageOut] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "document_type": self.document_type,
            "pages": [p.to_dict() for p in self.pages],
        }


def page_from_digital_text(info: PageInfo, blocks: list[TextBlock]) -> PageOut:
    """PathA: direct PyMuPDF extraction. No OCR ran, so confidence is always
    None here - there is no recognition score to report for text that was
    read directly out of the PDF's own content stream, and inventing one
    (e.g. hardcoding 1.0) would misrepresent it as a measured value.
    """
    elements = [
        ElementOut(type=b.block_type, text=b.text, bbox=list(b.bbox), confidence=None)
        for b in blocks
    ]
    return PageOut(
        page_number=info.page_number,
        width=info.width,
        height=info.height,
        processing_mode="digital",
        elements=elements,
    )


def page_from_ocr_result(info: PageInfo, result: StructureResult) -> PageOut:
    """PathB: PP-StructureV3 output, already shaped close to the final schema
    by ocr.py - this mainly flattens dataclasses into plain dicts.
    """
    elements = []
    for b in result.blocks:
        table_dict = None
        if b.table is not None:
            table_dict = {
                "bbox": list(b.table.bbox),
                "html": b.table.html,
                "cell_boxes": [list(c) for c in b.table.cell_boxes],
                "confidence": b.table.confidence,
                "rows": [
                    [
                        {"bbox": list(cell.bbox), "text": cell.text, "confidence": cell.confidence}
                        for cell in row
                    ]
                    for row in b.table.rows
                ],
            }
        elements.append(
            ElementOut(
                type=b.type,
                text=b.text,
                bbox=list(b.bbox),
                confidence=b.confidence,
                table=table_dict,
            )
        )
    return PageOut(
        page_number=info.page_number,
        width=info.width,
        height=info.height,
        processing_mode="scanned",
        elements=elements,
    )


def error_page(info: PageInfo, mode: str, message: str) -> PageOut:
    """A page that failed processing. Kept in the output, marked with
    ``error``, so one bad page doesn't erase the rest of the document.
    """
    return PageOut(
        page_number=info.page_number,
        width=info.width,
        height=info.height,
        processing_mode=mode,
        elements=[],
        error=message,
    )
