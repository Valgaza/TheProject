import numpy as np

from nexus_vision.debug_viz import draw_debug_overlay


def test_draw_debug_overlay_returns_same_shape_image(fake_table_with_rows_result):
    image = np.full((400, 700, 3), 255, dtype=np.uint8)
    overlay = draw_debug_overlay(image, fake_table_with_rows_result.blocks)
    assert overlay.shape == image.shape
    assert overlay.dtype == image.dtype


def test_draw_debug_overlay_does_not_mutate_the_input(fake_table_with_rows_result):
    image = np.full((400, 700, 3), 255, dtype=np.uint8)
    before = image.copy()
    draw_debug_overlay(image, fake_table_with_rows_result.blocks)
    assert np.array_equal(image, before)


def test_draw_debug_overlay_actually_draws_something(fake_table_with_rows_result):
    image = np.full((400, 700, 3), 255, dtype=np.uint8)
    overlay = draw_debug_overlay(image, fake_table_with_rows_result.blocks)
    assert not np.array_equal(overlay, image)  # some pixels changed


def test_draw_debug_overlay_ignores_non_table_blocks():
    from nexus_vision.ocr import Block

    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    blocks = [Block(type="text", text="hi", bbox=(0, 0, 10, 10), reading_order=0, confidence=0.9)]
    overlay = draw_debug_overlay(image, blocks)
    assert np.array_equal(overlay, image)  # nothing to draw, no table present


def test_draw_debug_overlay_handles_grayscale_input(fake_table_with_rows_result):
    image = np.full((400, 700), 255, dtype=np.uint8)
    overlay = draw_debug_overlay(image, fake_table_with_rows_result.blocks)
    assert overlay.ndim == 3 and overlay.shape[2] == 3
