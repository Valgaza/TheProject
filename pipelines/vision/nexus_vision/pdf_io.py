"""PDF handling: open, inspect, extract text, render pages as images.

PyMuPDF is the only thing in this module that touches the PDF file itself.
Everything else in the pipeline works with plain Python data (text, images,
numpy arrays), so PyMuPDF's own quirks stay contained here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pymupdf

logger = logging.getLogger(__name__)


class PdfOpenError(Exception):
    """The file could not be opened as a PDF at all (missing, corrupted, not a PDF)."""


@dataclass(frozen=True)
class PageInfo:
    """What we know about one page before deciding how to process it."""

    page_number: int  # 1-indexed, matches how humans refer to pages
    width: float
    height: float
    embedded_text: str
    # Largest single embedded image's bbox area, as a fraction of the page
    # area (0.0 = no images). Defaults to 0.0 so existing call sites/tests
    # that don't care about images don't need to specify it. See
    # classify.py for why this matters: a near-full-page image is a strong
    # signal the page is a scan, independent of what its text layer says.
    image_area_ratio: float = 0.0


def open_pdf(path: str) -> pymupdf.Document:
    """Open a PDF file, raising PdfOpenError with a clear message on failure.

    PyMuPDF raises different exception types (FileNotFoundError, RuntimeError
    for corrupt/encrypted files, etc.) depending on what went wrong. We
    normalize all of that to one exception type so callers only need to
    handle one thing.
    """
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 - intentionally broad, see docstring
        raise PdfOpenError(f"Could not open '{path}' as a PDF: {exc}") from exc

    if doc.is_encrypted:
        doc.close()
        raise PdfOpenError(f"'{path}' is password-protected; cannot process without a password.")

    if doc.page_count == 0:
        doc.close()
        raise PdfOpenError(f"'{path}' has no pages.")

    return doc


def _largest_image_area_ratio(page: pymupdf.Page) -> float:
    """Largest embedded image's on-page area, as a fraction of the page area."""
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return 0.0
    best = 0.0
    for img in page.get_image_info():
        # Intersect with the page rect: an image bbox can technically extend
        # past the page edge, which shouldn't count as "more than 100% of
        # the page".
        clipped = pymupdf.Rect(img["bbox"]) & page.rect
        best = max(best, (clipped.width * clipped.height) / page_area)
    return best


def get_page_info(doc: pymupdf.Document, page_index: int) -> PageInfo:
    """Inspect one page: its size, embedded text, and image coverage.

    ``page_index`` is 0-indexed (PyMuPDF convention); the returned
    ``page_number`` is 1-indexed (human/document convention). Getting these
    confused is a classic off-by-one, so the conversion happens in exactly
    one place.
    """
    page = doc[page_index]
    text = page.get_text("text")
    return PageInfo(
        page_number=page_index + 1,
        width=page.rect.width,
        height=page.rect.height,
        embedded_text=text,
        image_area_ratio=_largest_image_area_ratio(page),
    )


@dataclass(frozen=True)
class TextBlock:
    """One block from PyMuPDF's structured text extraction.

    ``block_type`` mirrors PyMuPDF's own block type: "text" (type 0) or
    "image" (type 1) - a digital PDF page can embed a raster image (e.g. a
    scanned signature) alongside real text, so this is not purely a text
    extractor.
    """

    block_type: str
    text: str | None
    bbox: tuple[float, float, float, float]


def extract_text_blocks(doc: pymupdf.Document, page_index: int) -> list[TextBlock]:
    """Block-level embedded text, with bounding boxes, for a digital page.

    Uses PyMuPDF's "dict" extraction mode rather than plain "text" so the
    digital path produces the same per-element granularity (type, text, bbox)
    as the OCR path - the two paths' outputs get normalized to one schema
    downstream, and that's only honest if both sides actually carry
    comparable structure rather than one flat string standing in for it.
    """
    page = doc[page_index]
    raw = page.get_text("dict")
    blocks: list[TextBlock] = []
    for b in raw.get("blocks", []):
        bbox = tuple(float(v) for v in b["bbox"])
        if b.get("type") == 1:
            blocks.append(TextBlock(block_type="image", text=None, bbox=bbox))
            continue
        lines = [
            "".join(span["text"] for span in line.get("spans", []))
            for line in b.get("lines", [])
        ]
        text = "\n".join(line for line in lines if line)
        if text.strip():
            blocks.append(TextBlock(block_type="text", text=text, bbox=bbox))
    return blocks


def render_page_to_image(doc: pymupdf.Document, page_index: int, dpi: int = 150):
    """Render a page to an RGB image as a numpy array (H, W, 3), uint8.

    150 DPI, not the more commonly recommended 300: a full page at 300 DPI
    (roughly 2480x3508px for A4) reproducibly segfaulted inside Paddle's
    native inference code on this project's development machine (CPU,
    MKL-DNN disabled - see ocr.py), while the same page at 150 DPI
    (~1240x1754px) processed correctly. Root-caused to image size specifically
    - confirmed by reproducing and then clearing the crash with everything
    else (preprocessing, page content) held constant. This is a real
    tradeoff against OCR accuracy on small print, not an arbitrary choice;
    raise it if your paddlepaddle/hardware combination doesn't hit this.
    """
    page = doc[page_index]
    return _pixmap_to_array(page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB))


def render_region_to_image(
    doc: pymupdf.Document, page_index: int, rect_points: tuple[float, float, float, float], dpi: int
):
    """Render just a sub-rectangle of a page (in PDF point coordinates), at
    its own DPI - independent of whatever DPI the full page was rendered at.

    Exists for table refinement (see ``table_refine.py``): a table often
    needs a higher DPI than a full page can safely use (see
    ``render_page_to_image``'s docstring), but a *crop* at that higher DPI is
    a much smaller image than the full page would be, which is what makes it
    safe. Returns the same (H, W, 3) uint8 array shape as
    ``render_page_to_image``.
    """
    page = doc[page_index]
    clip = pymupdf.Rect(rect_points) & page.rect
    return _pixmap_to_array(page.get_pixmap(clip=clip, dpi=dpi, colorspace=pymupdf.csRGB))


def _pixmap_to_array(pix: "pymupdf.Pixmap"):
    import numpy as np

    # `pix.samples` is a read-only buffer owned by the pixmap; `.copy()` gives
    # OpenCV/Paddle a writable array they actually own, rather than a view
    # into memory PyMuPDF can free or reuse.
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).copy()
