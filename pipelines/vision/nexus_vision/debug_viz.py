"""Draw detected table/cell bounding boxes onto a page image, for visually
verifying table extraction against the source document.

Opt-in only (see ``pipeline.py``'s ``debug_dir`` parameter) - this is a
verification aid, not something a normal run produces.
"""
from __future__ import annotations

import cv2
import numpy as np

from .ocr import Block

# Images in this pipeline are RGB throughout (see preprocess.py/pdf_io.py's
# module docstrings) - these are RGB tuples to match, not OpenCV's usual BGR.
# The caller (pipeline.py) converts to BGR once, immediately before
# cv2.imwrite, not before.
_TABLE_COLOR_RGB = (0, 80, 255)  # blue
_CELL_COLOR_RGB = (0, 180, 0)  # green
_LABEL_COLOR_RGB = (255, 0, 0)  # red


def draw_debug_overlay(image: np.ndarray, blocks: list[Block]) -> np.ndarray:
    """Return a copy of ``image`` with every table's and its cells'
    bounding boxes drawn on top, plus per-cell confidence labels.

    Draws nothing else - non-table blocks aren't this function's concern.
    Does not mutate ``image``.
    """
    canvas = image.copy()
    if canvas.ndim == 2:
        canvas = cv2.cvtColor(canvas, cv2.COLOR_GRAY2RGB)

    for block in blocks:
        if block.table is None:
            continue
        _draw_box(canvas, block.table.bbox, _TABLE_COLOR_RGB, thickness=3)
        for row in block.table.rows:
            for cell in row:
                _draw_box(canvas, cell.bbox, _CELL_COLOR_RGB, thickness=1)
                if cell.confidence is not None:
                    x1, y1, _, _ = (int(v) for v in cell.bbox)
                    cv2.putText(
                        canvas,
                        f"{cell.confidence:.2f}",
                        (x1 + 2, y1 + 12),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.35,
                        _LABEL_COLOR_RGB,
                        1,
                        cv2.LINE_AA,
                    )
    return canvas


def _draw_box(canvas: np.ndarray, bbox: tuple[float, float, float, float], color_rgb, *, thickness: int) -> None:
    x1, y1, x2, y2 = (int(v) for v in bbox)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), color_rgb, thickness)
