"""Image preprocessing for scanned pages, ahead of OCR.

Every function here takes a numpy array and returns a *new* one - none of
them mutate the input in place, so the caller always still has the original
render if a later step needs it or a caller wants to compare/debug.

Images are RGB uint8 throughout this module (that's what PyMuPDF's
``render_page_to_image`` produces). The OCR module converts to whatever
channel order PaddleOCR expects right before calling it - that conversion
does not belong here.

Kept intentionally small: each step is a plain function so any one of them
can be enabled, disabled, or reordered from ``PreprocessConfig`` without
touching the others. This is not meant to grow into a general image
processing framework.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


def to_grayscale(image: np.ndarray) -> np.ndarray:
    """RGB -> single-channel grayscale."""
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def denoise(image: np.ndarray) -> np.ndarray:
    """Remove scanner/camera speckle noise.

    Uses OpenCV's Non-local Means denoiser. Handles both grayscale (2D) and
    RGB (3D) arrays since a caller may run this before or after
    ``to_grayscale``.
    """
    if image.ndim == 2:
        return cv2.fastNlMeansDenoising(image, h=10)
    return cv2.fastNlMeansDenoisingColored(image, h=10, hColor=10)


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    """CLAHE (adaptive histogram equalization) - helps faded or unevenly-lit scans.

    Applied on the grayscale version regardless of input, since contrast is a
    luminance concept; a 3-channel input is converted, enhanced, and returned
    as grayscale. Plain global equalization was avoided because it tends to
    blow out already-legible regions on scans that are only partially faded.
    """
    gray = image if image.ndim == 2 else to_grayscale(image)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def deskew(image: np.ndarray) -> np.ndarray:
    """Rotate a page to correct small skew from crooked scanning/photographing.

    Off by default in the pipeline (see PreprocessConfig) - angle estimation
    from a full page's text mask is reliable for a page that is mostly one
    block of text, but can misfire on complex multi-column or table-heavy
    layouts, where it would actively hurt OCR rather than help it. Safe to
    enable per-document once that tradeoff has been checked against the
    actual input.
    """
    gray = image if image.ndim == 2 else to_grayscale(image)
    # Text pixels are dark; invert + threshold so text becomes the foreground
    # for minAreaRect to trace around.
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.size == 0:
        return image  # blank page, nothing to deskew
    angle = cv2.minAreaRect(coords)[-1]
    # cv2.minAreaRect returns an angle in (-90, 0]; normalize to a small
    # rotation rather than accidentally flipping the page 90 degrees.
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.1:
        return image  # not worth the resample
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def adaptive_threshold(image: np.ndarray) -> np.ndarray:
    """Binarize to black/white.

    Off by default in the pipeline. PP-StructureV3's models are trained on
    ordinary photographic/scanned images, not pre-binarized masks, and hard
    thresholding can discard information (faint text, gray table rules) the
    model would otherwise use. Kept available for genuinely poor-quality
    scans where a caller has verified it helps.
    """
    gray = image if image.ndim == 2 else to_grayscale(image)
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )


@dataclass(frozen=True)
class PreprocessConfig:
    """Which steps to run, and in this order. Grayscale/denoise/contrast are
    safe defaults; deskew and thresholding are opt-in (see their docstrings).
    """

    grayscale: bool = True
    denoise: bool = True
    contrast: bool = True
    deskew: bool = False
    threshold: bool = False


def preprocess(image: np.ndarray, config: PreprocessConfig = PreprocessConfig()) -> np.ndarray:
    """Run the configured steps in a fixed, sensible order.

    Order matters: denoising before contrast enhancement avoids amplifying
    noise, and both happen before the optional geometric (deskew) and binary
    (threshold) steps, which are the most likely to compound errors if run on
    a noisy image.
    """
    result = image
    if config.grayscale:
        result = to_grayscale(result)
    if config.denoise:
        result = denoise(result)
    if config.contrast:
        result = enhance_contrast(result)
    if config.deskew:
        result = deskew(result)
    if config.threshold:
        result = adaptive_threshold(result)
    return result
