"""Orchestration: PDF in, structured JSON out.

    PDF -> per page -> classify (digital vs scanned)
                          digital -> PyMuPDF direct extraction
                          scanned -> render -> preprocess -> OCR/layout
                                     -> per table: crop + higher-DPI re-OCR
                     -> normalize both paths to one page shape
         -> assemble document JSON

This module wires the other modules together and owns the things that cut
across all of them: per-page error handling (one bad page must not fail the
whole document) and logging. It contains no PDF, image, or OCR logic itself.
"""
from __future__ import annotations

import logging
import os

from .classify import classify_page
from .normalize import DocumentOut, error_page, page_from_digital_text, page_from_ocr_result
from .ocr import OcrEngine, StructureResult, SupportsOcr
from .pdf_io import extract_text_blocks, get_page_info, open_pdf, render_page_to_image
from .preprocess import PreprocessConfig, preprocess
from .table_refine import refine_table_block

logger = logging.getLogger(__name__)


def process_pdf(
    path: str,
    *,
    device: str = "cpu",
    dpi: int = 150,
    preprocess_config: PreprocessConfig = PreprocessConfig(),
    ocr_engine: SupportsOcr | None = None,
    refine_tables: bool = True,
    table_refine_dpi: int = 300,
    debug_dir: str | None = None,
) -> dict:
    """Process one PDF end to end and return the document JSON as a dict.

    ``device`` is passed straight through to PaddleOCR ("cpu", "gpu",
    "gpu:0", ...) - this function never hardcodes hardware. The OCR engine
    itself is only constructed if at least one page actually needs it, so an
    all-digital PDF never pays for loading the OCR models.

    ``dpi`` defaults to 150, not the more common 300 - see
    ``pdf_io.render_page_to_image``'s docstring for why.

    ``ocr_engine`` lets a caller supply an already-built engine (avoids
    reloading models per document when processing many files) or a test
    double satisfying ``SupportsOcr``. Leave it ``None`` for normal use.

    ``refine_tables`` re-OCRs each detected table's own region at
    ``table_refine_dpi`` (see ``table_refine.py``) - on by default because
    it's what actually improves table extraction quality; set to ``False``
    to skip it (e.g. for speed, or to compare against the un-refined result).

    ``debug_dir``, if given, writes one PNG per scanned page showing the
    detected table/cell bounding boxes overlaid on the page image - a
    verification aid, not part of normal output (see ``debug_viz.py``).
    """
    filename = os.path.basename(path)
    doc = open_pdf(path)  # raises PdfOpenError on invalid/corrupt/empty/encrypted input

    logger.info("Processing %s", filename)
    logger.info("Detected %d pages", doc.page_count)

    if debug_dir is not None:
        os.makedirs(debug_dir, exist_ok=True)

    document = DocumentOut(filename=filename)

    try:
        for page_index in range(doc.page_count):
            info = get_page_info(doc, page_index)
            mode = classify_page(info)

            if mode == "digital":
                logger.info("Page %d: digital text", info.page_number)
                try:
                    blocks = extract_text_blocks(doc, page_index)
                    document.pages.append(page_from_digital_text(info, blocks))
                except Exception as exc:  # noqa: BLE001 - page-level isolation is the point
                    logger.exception("Page %d: digital extraction failed", info.page_number)
                    document.pages.append(error_page(info, "digital", str(exc)))
                continue

            logger.info("Page %d: scanned, OCR required", info.page_number)
            try:
                raw_image = render_page_to_image(doc, page_index, dpi=dpi)
                ocr_image = preprocess(raw_image, preprocess_config)
                if ocr_engine is None:
                    ocr_engine = OcrEngine(device=device)
                result = ocr_engine.run(ocr_image)

                if refine_tables:
                    result = _refine_tables(doc, page_index, result, base_dpi=dpi, table_dpi=table_refine_dpi, ocr_engine=ocr_engine)

                document.pages.append(page_from_ocr_result(info, result))

                if debug_dir is not None:
                    _save_debug_overlay(raw_image, result, debug_dir, filename, info.page_number)
            except Exception as exc:  # noqa: BLE001 - see above
                logger.exception("Page %d: OCR processing failed", info.page_number)
                document.pages.append(error_page(info, "scanned", str(exc)))
    finally:
        doc.close()

    logger.info("Processing complete")
    return document.to_dict()


def _refine_tables(doc, page_index, result: StructureResult, *, base_dpi, table_dpi, ocr_engine) -> StructureResult:
    """Replace each table block with a version re-OCR'd from a higher-DPI
    crop of just that table - see table_refine.py. Non-table blocks pass
    through untouched.
    """
    table_count = sum(1 for b in result.blocks if b.table is not None)
    if table_count == 0:
        return result

    logger.info("Page %d: refining %d table(s) at %d DPI", page_index + 1, table_count, table_dpi)
    refined_blocks = [
        refine_table_block(doc, page_index, b, base_dpi=base_dpi, table_dpi=table_dpi, ocr_engine=ocr_engine)
        if b.table is not None
        else b
        for b in result.blocks
    ]
    return StructureResult(blocks=refined_blocks, engine=result.engine)


def _save_debug_overlay(raw_image, result: StructureResult, debug_dir: str, filename: str, page_number: int) -> None:
    import cv2

    from .debug_viz import draw_debug_overlay

    overlay = draw_debug_overlay(raw_image, result.blocks)
    stem = os.path.splitext(filename)[0]
    out_path = os.path.join(debug_dir, f"{stem}_page{page_number}_debug.png")
    # draw_debug_overlay returns RGB (matching raw_image's convention);
    # cv2.imwrite expects BGR, hence the swap immediately before writing.
    cv2.imwrite(out_path, cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    logger.info("Page %d: wrote debug overlay to %s", page_number, out_path)
