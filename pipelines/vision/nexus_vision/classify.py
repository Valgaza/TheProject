"""Decide whether a page needs OCR at all.

Still deliberately simple - a handful of thresholds, not a trained model -
but "does this page have enough embedded text" turned out not to be enough
on its own. Two more signals feed the decision, both found necessary against
a real PDF (a scanned exam timetable saved with a broken text layer on top):

1. Text quality. A page can clear the character-count bar while its text is
   still garbage: some scan/export tools embed a text layer through a
   broken or non-standard font encoding, which PyMuPDF still extracts as a
   string of the right *length*, just not real language. The real timetable
   PDF's two pages scored alpha_ratio ~0.41-0.46 and word_ratio ~0.31-0.42;
   ordinary prose (see tests/test_classify.py) scores ~0.8+ and ~0.9+ on the
   same two measures. ``MIN_ALPHA_RATIO``/``MIN_WORD_RATIO`` sit in the gap
   between those two clusters.
2. A near-full-page image. A digital PDF occasionally carries a decorative
   image (a letterhead, an embedded chart) next to real text, but those are
   essentially never sized to cover the *entire* page - an image whose bbox
   covers ~90%+ of the page area is a strong, low-false-positive signal that
   the page is fundamentally a scan, regardless of what its text layer says.
   The real timetable PDF's image bbox was exactly the page's own rect.

Either signal alone is enough to route a page to OCR; a page only takes the
direct-extraction path when it has enough text, that text looks like real
language, *and* it isn't dominated by one page-sized image.
"""
from __future__ import annotations

import re
from typing import Literal

from .pdf_io import PageInfo

ProcessingMode = Literal["digital", "scanned"]

# A handful of stray characters (a stamp, a page number OCR'd into the layer
# by a prior tool, whitespace-only "text") should not count as a usable text
# layer.
MIN_MEANINGFUL_CHARS = 20

# See the module docstring for where these numbers come from.
MIN_ALPHA_RATIO = 0.6
MIN_WORD_RATIO = 0.6
LARGE_IMAGE_AREA_RATIO = 0.9

_NON_LETTERS = re.compile(r"[^A-Za-z]")


def _looks_like_word(token: str) -> bool:
    """A crude, deliberately simple 'is this a real word' check.

    At least 2 letters, and letters make up most of the token's characters -
    filters out stray symbols/numbers without needing a dictionary.
    """
    letters_only = _NON_LETTERS.sub("", token)
    return len(letters_only) >= 2 and len(letters_only) >= 0.6 * len(token)


def _text_quality_ok(text: str, *, min_alpha_ratio: float, min_word_ratio: float) -> bool:
    """Does this (non-empty, already-stripped) text look like real language?

    Two cheap signals, both required - see the module docstring for the
    real-world numbers this was calibrated against.
    """
    alpha_ratio = sum(c.isalpha() for c in text) / len(text)
    tokens = text.split()
    if not tokens:
        return False
    word_ratio = sum(1 for t in tokens if _looks_like_word(t)) / len(tokens)
    return alpha_ratio >= min_alpha_ratio and word_ratio >= min_word_ratio


def classify_page(
    info: PageInfo,
    *,
    min_chars: int = MIN_MEANINGFUL_CHARS,
    min_alpha_ratio: float = MIN_ALPHA_RATIO,
    min_word_ratio: float = MIN_WORD_RATIO,
    large_image_ratio: float = LARGE_IMAGE_AREA_RATIO,
) -> ProcessingMode:
    """"digital" only if the page has enough text, that text looks like real
    language, and the page isn't dominated by one page-sized image.
    Otherwise "scanned" - see the module docstring for why each check exists.
    """
    stripped = info.embedded_text.strip()
    if len(stripped) < min_chars:
        return "scanned"
    if info.image_area_ratio >= large_image_ratio:
        return "scanned"
    if not _text_quality_ok(stripped, min_alpha_ratio=min_alpha_ratio, min_word_ratio=min_word_ratio):
        return "scanned"
    return "digital"
