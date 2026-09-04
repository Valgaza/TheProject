import pytest

from nexus_vision.pdf_io import PdfOpenError, extract_text_blocks, get_page_info, open_pdf, render_page_to_image

from fixtures import make_corrupted_pdf, make_digital_pdf, make_garbled_text_with_full_page_image_pdf


def test_open_pdf_raises_clearly_on_corrupted_file(tmp_path):
    path = make_corrupted_pdf(tmp_path / "bad.pdf")
    with pytest.raises(PdfOpenError):
        open_pdf(str(path))


def test_open_pdf_raises_clearly_on_missing_file(tmp_path):
    with pytest.raises(PdfOpenError):
        open_pdf(str(tmp_path / "does_not_exist.pdf"))


def test_open_pdf_raises_on_empty_file(tmp_path):
    # A zero-byte file - e.g. a failed download or an interrupted write -
    # rather than a "0-page PDF": PyMuPDF itself refuses to save a document
    # with zero pages (raises ValueError), so a syntactically valid but
    # page-less PDF isn't something the format's own tooling can produce.
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(PdfOpenError):
        open_pdf(str(path))


def test_open_pdf_succeeds_on_a_normal_pdf(tmp_path):
    path = make_digital_pdf(tmp_path / "ok.pdf")
    doc = open_pdf(str(path))
    assert doc.page_count == 1
    doc.close()


def test_get_page_info_reports_embedded_text_and_dimensions(tmp_path):
    path = make_digital_pdf(tmp_path / "ok.pdf")
    doc = open_pdf(str(path))
    info = get_page_info(doc, 0)
    assert info.page_number == 1
    assert "Digital Page" in info.embedded_text
    assert info.width > 0 and info.height > 0
    doc.close()


def test_extract_text_blocks_returns_bbox_per_block(tmp_path):
    path = make_digital_pdf(tmp_path / "ok.pdf")
    doc = open_pdf(str(path))
    blocks = extract_text_blocks(doc, 0)
    assert len(blocks) >= 1
    assert all(b.block_type == "text" for b in blocks)
    assert all(len(b.bbox) == 4 for b in blocks)
    doc.close()


def test_get_page_info_image_area_ratio_is_zero_with_no_images(tmp_path):
    path = make_digital_pdf(tmp_path / "ok.pdf")
    doc = open_pdf(str(path))
    info = get_page_info(doc, 0)
    assert info.image_area_ratio == 0.0
    doc.close()


def test_get_page_info_image_area_ratio_is_near_one_for_a_full_page_image(tmp_path):
    path = make_garbled_text_with_full_page_image_pdf(tmp_path / "garbled.pdf")
    doc = open_pdf(str(path))
    info = get_page_info(doc, 0)
    # Not closer to 1.0: PyMuPDF's insert_image preserves the source image's
    # aspect ratio by default, so a source image shaped differently than the
    # page rect (as here) letterboxes rather than filling it exactly. What
    # matters is that it clears classify.py's own "large image" threshold.
    from nexus_vision.classify import LARGE_IMAGE_AREA_RATIO

    assert info.image_area_ratio > LARGE_IMAGE_AREA_RATIO
    doc.close()


def test_render_page_to_image_is_a_fresh_writable_array(tmp_path):
    path = make_digital_pdf(tmp_path / "ok.pdf")
    doc = open_pdf(str(path))
    img = render_page_to_image(doc, 0, dpi=100)
    assert img.ndim == 3 and img.shape[2] == 3
    assert img.dtype.name == "uint8"
    # Regression guard: this array must own its memory and be writable, or it
    # can crash native OCR code that expects a mutable buffer it owns.
    assert img.flags["OWNDATA"]
    assert img.flags["WRITEABLE"]
    doc.close()
