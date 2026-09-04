from nexus_vision.classify import classify_page
from nexus_vision.pdf_io import PageInfo

# Lifted verbatim from a real-world failure case: a scanned exam timetable
# PDF whose text layer decodes to mojibake through a broken font encoding,
# sitting on top of a full-page image. Long enough (>20 chars) to have
# passed the old character-count-only classifier as "digital".
REAL_GARBLED_TEXT = (
    "\\o\nGI\n6tiE el\na tr -l\nrp \\l\niEe\nue >l\n-\\o -l\nH ?1 €l\nb 0i \n.=l"
)

GOOD_TEXT = "This document summarizes revenue for the quarter and lists the regional totals below."


def _info(text: str, *, image_area_ratio: float = 0.0) -> PageInfo:
    return PageInfo(page_number=1, width=612, height=792, embedded_text=text, image_area_ratio=image_area_ratio)


def test_page_with_real_text_and_no_image_is_digital():
    assert classify_page(_info(GOOD_TEXT)) == "digital"


def test_blank_page_is_scanned():
    assert classify_page(_info("")) == "scanned"


def test_whitespace_only_page_is_scanned():
    assert classify_page(_info("   \n\n\t  ")) == "scanned"


def test_a_few_stray_characters_is_scanned():
    # A page number or watermark shouldn't count as a usable text layer.
    assert classify_page(_info("12")) == "scanned"


def test_min_chars_threshold_is_configurable():
    assert classify_page(_info("short"), min_chars=3) == "digital"


# --- the real failure case ---------------------------------------------


def test_garbled_text_with_full_page_image_is_scanned():
    # The exact scenario observed against Comp MSE TT 26-27(Jul-Nov).PDF:
    # >20 chars of embedded text, but it's mojibake, and there's a
    # full-page image underneath it. Must route to OCR, not direct extraction.
    info = _info(REAL_GARBLED_TEXT, image_area_ratio=1.0)
    assert len(info.embedded_text.strip()) >= 20  # would have passed the old classifier
    assert classify_page(info) == "scanned"


def test_garbled_text_without_a_large_image_is_still_scanned():
    # Text quality alone is enough to trigger OCR - an image isn't required.
    info = _info(REAL_GARBLED_TEXT, image_area_ratio=0.0)
    assert classify_page(info) == "scanned"


# --- the full-page-image signal, independent of text quality ------------


def test_good_text_with_full_page_image_is_scanned():
    # Even clean, legible embedded text doesn't save a page that's
    # dominated by one page-sized image - see the module docstring for why
    # this is treated as "fundamentally a scan" regardless of the text layer.
    info = _info(GOOD_TEXT, image_area_ratio=0.95)
    assert classify_page(info) == "scanned"


def test_good_text_with_a_small_image_stays_digital():
    # A partial image (a letterhead, an embedded chart) alongside real text
    # is normal in genuinely digital PDFs and must not force OCR.
    info = _info(GOOD_TEXT, image_area_ratio=0.2)
    assert classify_page(info) == "digital"


def test_large_image_threshold_is_configurable():
    info = _info(GOOD_TEXT, image_area_ratio=0.5)
    assert classify_page(info, large_image_ratio=0.4) == "scanned"


# --- the text-quality signal in isolation --------------------------------


def test_quality_thresholds_are_configurable():
    # With sufficiently relaxed thresholds, even the garbled real-world text
    # passes - demonstrating the check is a real, tunable measurement and
    # not a hardcoded pass/fail on that specific string.
    info = _info(REAL_GARBLED_TEXT)
    assert classify_page(info, min_alpha_ratio=0.0, min_word_ratio=0.0) == "digital"
