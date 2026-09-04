import numpy as np
import pytest

from nexus_vision.pdf_io import PdfOpenError
from nexus_vision.pipeline import process_pdf

from conftest import FakeOcrEngine, PoisonOcrEngine
from fixtures import (
    make_corrupted_pdf,
    make_digital_pdf,
    make_garbled_text_with_full_page_image_pdf,
    make_mixed_pdf,
    make_pdf_from_image,
    make_scanned_page_image,
)


# --- scenario 1: digital PDF, direct extraction, OCR never invoked --------


def test_digital_pdf_does_not_invoke_ocr(tmp_path):
    path = make_digital_pdf(tmp_path / "digital.pdf", pages=2)
    result = process_pdf(str(path), ocr_engine=PoisonOcrEngine())

    assert result["filename"] == "digital.pdf"
    assert len(result["pages"]) == 2
    for page in result["pages"]:
        assert page["processing_mode"] == "digital"
        assert page["error"] is None
        assert any(el["text"] for el in page["elements"])
        assert all(el["confidence"] is None for el in page["elements"])  # no OCR ran


# --- the real failure case: text that passes the char-count bar but isn't --
# --- usable, on a full-page image - must route to OCR, not direct extract -


def test_garbled_text_with_full_page_image_routes_through_ocr(tmp_path, fake_text_result):
    # End-to-end reproduction of the real Comp MSE TT 26-27(Jul-Nov).PDF
    # failure: >20 chars of embedded text, but it's mojibake, on top of an
    # image covering the whole page. Exercises the real pdf_io -> classify
    # -> pipeline chain, not just classify_page in isolation (see
    # test_classify.py for the unit-level version of this same case).
    path = make_garbled_text_with_full_page_image_pdf(tmp_path / "garbled.pdf")
    engine = FakeOcrEngine(fake_text_result)

    result = process_pdf(str(path), ocr_engine=engine)

    assert len(engine.calls) == 1  # OCR actually ran, instead of being skipped
    page = result["pages"][0]
    assert page["processing_mode"] == "scanned"
    assert page["error"] is None


# --- scenario 2: scanned PDF, printed text -> OCR path ---------------------


def test_scanned_pdf_uses_ocr_path_and_normalizes_output(tmp_path, fake_text_result):
    image = make_scanned_page_image()
    path = make_pdf_from_image(tmp_path / "scanned.pdf", image)
    engine = FakeOcrEngine(fake_text_result)

    result = process_pdf(str(path), ocr_engine=engine)

    assert len(engine.calls) == 1  # OCR ran exactly once, for the one page
    page = result["pages"][0]
    assert page["processing_mode"] == "scanned"
    assert page["error"] is None
    texts = [el["text"] for el in page["elements"]]
    assert "Scanned Page" in texts
    assert all(el["confidence"] is not None for el in page["elements"])


# --- scenario 3: mixed digital + scanned, page-level routing --------------


def test_mixed_pdf_routes_each_page_independently(tmp_path, fake_text_result):
    path = make_mixed_pdf(tmp_path / "mixed.pdf")
    engine = FakeOcrEngine(fake_text_result)

    result = process_pdf(str(path), ocr_engine=engine)

    modes = [p["processing_mode"] for p in result["pages"]]
    assert modes == ["digital", "scanned", "digital"]
    assert len(engine.calls) == 1  # only the one scanned page reached OCR


# --- scenario 4: table on a scanned page -----------------------------------


def test_table_structure_is_preserved_in_json(tmp_path, fake_table_result):
    image = make_scanned_page_image()
    path = make_pdf_from_image(tmp_path / "table.pdf", image)
    engine = FakeOcrEngine(fake_table_result)

    result = process_pdf(str(path), ocr_engine=engine)

    table_elements = [el for el in result["pages"][0]["elements"] if el["type"] == "table"]
    assert len(table_elements) == 1
    table = table_elements[0]["table"]
    assert "<table>" in table["html"]
    assert len(table["cell_boxes"]) == 2
    assert table["confidence"] == pytest.approx(0.97)


def test_table_rows_and_cells_are_serialized_into_json(tmp_path, fake_table_with_rows_result):
    image = make_scanned_page_image()
    path = make_pdf_from_image(tmp_path / "table.pdf", image)
    engine = FakeOcrEngine(fake_table_with_rows_result)

    result = process_pdf(str(path), ocr_engine=engine)

    table = next(el for el in result["pages"][0]["elements"] if el["type"] == "table")["table"]
    assert len(table["rows"]) == 2
    assert [c["text"] for c in table["rows"][0]] == ["Region", "Revenue"]
    assert [c["text"] for c in table["rows"][1]] == ["North", "120000"]
    for row in table["rows"]:
        for cell in row:
            assert len(cell["bbox"]) == 4
            assert cell["confidence"] is not None


# --- table refinement: crop + higher-DPI second pass -----------------------


def test_refine_tables_runs_a_second_ocr_pass_on_detected_tables(tmp_path, fake_table_result):
    image = make_scanned_page_image()
    path = make_pdf_from_image(tmp_path / "table.pdf", image)
    engine = FakeOcrEngine(fake_table_result)  # same canned result for both passes

    process_pdf(str(path), ocr_engine=engine, refine_tables=True)

    # base extraction + one refinement pass for the one detected table
    assert len(engine.calls) == 2


def test_refine_tables_false_skips_the_second_pass(tmp_path, fake_table_result):
    image = make_scanned_page_image()
    path = make_pdf_from_image(tmp_path / "table.pdf", image)
    engine = FakeOcrEngine(fake_table_result)

    process_pdf(str(path), ocr_engine=engine, refine_tables=False)

    assert len(engine.calls) == 1


def test_refine_tables_has_no_effect_when_no_table_is_present(tmp_path, fake_text_result):
    image = make_scanned_page_image()
    path = make_pdf_from_image(tmp_path / "text_only.pdf", image)
    engine = FakeOcrEngine(fake_text_result)

    process_pdf(str(path), ocr_engine=engine, refine_tables=True)

    assert len(engine.calls) == 1  # nothing to refine, no extra pass


def test_debug_dir_writes_an_overlay_image_per_scanned_page(tmp_path, fake_table_with_rows_result):
    import os

    image = make_scanned_page_image()
    path = make_pdf_from_image(tmp_path / "table.pdf", image)
    debug_dir = tmp_path / "debug"
    engine = FakeOcrEngine(fake_table_with_rows_result)

    process_pdf(str(path), ocr_engine=engine, debug_dir=str(debug_dir))

    files = os.listdir(debug_dir)
    assert len(files) == 1
    assert files[0].endswith("_page1_debug.png")


# --- scenario 5: poor-quality scan -----------------------------------------


def test_poor_quality_scan_is_preprocessed_before_ocr(tmp_path, fake_text_result):
    image = make_scanned_page_image(noisy=True)
    path = make_pdf_from_image(tmp_path / "poor.pdf", image)
    engine = FakeOcrEngine(fake_text_result)

    result = process_pdf(str(path), ocr_engine=engine)

    assert result["pages"][0]["error"] is None
    assert len(engine.calls) == 1
    received = engine.calls[0]
    # Default preprocessing grayscales the page; the pipeline hands the OCR
    # engine that grayscale array as-is (3-channel expansion is the real
    # OcrEngine's own concern, not the pipeline's - see ocr.py). A 2D array
    # here confirms preprocessing actually ran before the image reached OCR,
    # not just that OCR was called.
    assert received.ndim == 2


# --- scenario 6: invalid / corrupted PDF ------------------------------------


def test_corrupted_pdf_raises_a_clear_error(tmp_path):
    path = make_corrupted_pdf(tmp_path / "bad.pdf")
    with pytest.raises(PdfOpenError):
        process_pdf(str(path))


# --- resilience: one bad page must not fail the whole document ------------


def test_ocr_failure_on_one_page_is_recorded_and_does_not_raise(tmp_path):
    image = make_scanned_page_image()
    path = make_pdf_from_image(tmp_path / "scanned.pdf", image)
    engine = FakeOcrEngine(raises=RuntimeError("simulated OCR crash"))

    result = process_pdf(str(path), ocr_engine=engine)  # must not raise

    page = result["pages"][0]
    assert page["processing_mode"] == "scanned"
    assert page["error"] is not None
    assert "simulated OCR crash" in page["error"]
    assert page["elements"] == []
