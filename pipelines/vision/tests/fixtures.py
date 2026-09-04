"""Synthetic PDFs/images for tests, built on the fly rather than committed as
binary files - deterministic, and nothing to keep in sync by hand.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont


def make_digital_pdf(path: Path, *, pages: int = 1) -> Path:
    """A PDF with a real, selectable text layer on every page."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Digital Page {i + 1}")
        page.insert_text((72, 100), "This page has a real embedded text layer with enough characters.")
    doc.save(str(path))
    doc.close()
    return path


def make_scanned_page_image(*, noisy: bool = False) -> np.ndarray:
    """A synthetic 'scanned' page: heading, paragraph, and a small table
    rendered as pixels only - no text layer, the way a real scan would be.
    """
    img = Image.new("RGB", (1240, 1600), "white")
    draw = ImageDraw.Draw(img)
    font_big = ImageFont.load_default(size=40)
    font = ImageFont.load_default(size=28)
    draw.text((60, 60), "Scanned Page", fill="black", font=font_big)
    draw.text((60, 140), "Printed body text for OCR.", fill="black", font=font)

    table_x, table_y, col_w, row_h = 60, 240, 300, 60
    for c, h in enumerate(["Region", "Revenue"]):
        draw.rectangle(
            [table_x + c * col_w, table_y, table_x + (c + 1) * col_w, table_y + row_h],
            outline="black", width=2,
        )
        draw.text((table_x + c * col_w + 10, table_y + 15), h, fill="black", font=font)
    for r, row in enumerate([["North", "120000"], ["South", "98000"]]):
        for c, val in enumerate(row):
            y0 = table_y + (r + 1) * row_h
            draw.rectangle(
                [table_x + c * col_w, y0, table_x + (c + 1) * col_w, y0 + row_h],
                outline="black", width=2,
            )
            draw.text((table_x + c * col_w + 10, y0 + 15), val, fill="black", font=font)

    arr = np.array(img)
    if noisy:
        rng = np.random.default_rng(0)
        speckle = rng.integers(0, 60, size=arr.shape, dtype=np.uint8)
        arr = np.clip(arr.astype(np.int16) - speckle, 0, 255).astype(np.uint8)
        # fade the whole page, as a low-toner/faded-scan would look
        arr = np.clip(arr.astype(np.int16) + 40, 0, 255).astype(np.uint8)
    return arr


def make_pdf_from_image(path: Path, image: np.ndarray) -> Path:
    """A one-page PDF that's just a raster image - no text layer at all,
    the same shape as a real scanned-and-saved-as-PDF document.
    """
    tmp_png = path.with_suffix(".src.png")
    Image.fromarray(image).save(tmp_png)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, filename=str(tmp_png))
    doc.save(str(path))
    doc.close()
    tmp_png.unlink()
    return path


def make_mixed_pdf(path: Path) -> Path:
    """Page 1 digital text, page 2 a scanned image with a table, page 3
    digital text again - the "mixed" scenario from the acceptance criteria.
    """
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Digital Page One")
    p1.insert_text((72, 100), "Real embedded text, no OCR should run here.")

    tmp_png = path.with_suffix(".src.png")
    Image.fromarray(make_scanned_page_image()).save(tmp_png)
    p2 = doc.new_page()
    p2.insert_image(p2.rect, filename=str(tmp_png))

    p3 = doc.new_page()
    p3.insert_text((72, 72), "Digital Page Three")
    p3.insert_text((72, 100), "Also real embedded text, also no OCR.")

    doc.save(str(path))
    doc.close()
    tmp_png.unlink()
    return path


def make_corrupted_pdf(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.7\nthis is not a real pdf body\n%%EOF")
    return path


# Verbatim from the real-world failure case this fixture reproduces: a
# scanned exam timetable PDF whose text layer decodes to mojibake through a
# broken font encoding. See classify.py's module docstring.
REAL_GARBLED_TEXT = "\\o\nGI\n6tiE el\na tr -l\nrp \\l\niEe\nue >l\n-\\o -l\nH ?1 €l\nb 0i \n.=l"


def make_garbled_text_with_full_page_image_pdf(path: Path) -> Path:
    """Reproduces the exact real-world failure case: a page with >20 chars
    of embedded text that isn't real language, sitting on top of an image
    that covers the whole page - the way a scan-with-broken-OCR-layer PDF
    looks. Should route to the OCR path despite having "enough" text by
    character count alone.
    """
    tmp_png = path.with_suffix(".src.png")
    Image.fromarray(make_scanned_page_image()).save(tmp_png)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, filename=str(tmp_png))
    page.insert_text((72, 72), REAL_GARBLED_TEXT)
    doc.save(str(path))
    doc.close()
    tmp_png.unlink()
    return path
