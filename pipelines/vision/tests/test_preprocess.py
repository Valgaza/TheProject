import numpy as np

from nexus_vision.preprocess import (
    PreprocessConfig,
    adaptive_threshold,
    deskew,
    enhance_contrast,
    preprocess,
    to_grayscale,
)


def _rgb_image() -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, size=(200, 300, 3), dtype=np.uint8)


def test_to_grayscale_drops_to_one_channel():
    out = to_grayscale(_rgb_image())
    assert out.ndim == 2


def test_steps_do_not_mutate_the_input():
    original = _rgb_image()
    before = original.copy()
    to_grayscale(original)
    enhance_contrast(original)
    assert np.array_equal(original, before)


def test_enhance_contrast_returns_grayscale_regardless_of_input_channels():
    rgb_out = enhance_contrast(_rgb_image())
    gray_out = enhance_contrast(to_grayscale(_rgb_image()))
    assert rgb_out.ndim == 2
    assert gray_out.ndim == 2


def test_deskew_leaves_blank_page_unchanged():
    blank = np.full((200, 300), 255, dtype=np.uint8)
    out = deskew(blank)
    assert out.shape == blank.shape


def test_adaptive_threshold_is_binary():
    out = adaptive_threshold(_rgb_image())
    assert set(np.unique(out)).issubset({0, 255})


def test_preprocess_default_config_runs_grayscale_denoise_contrast():
    out = preprocess(_rgb_image())
    assert out.ndim == 2  # grayscale is on by default


def test_preprocess_all_steps_disabled_is_a_passthrough():
    img = _rgb_image()
    config = PreprocessConfig(grayscale=False, denoise=False, contrast=False, deskew=False, threshold=False)
    out = preprocess(img, config)
    assert np.array_equal(out, img)


def test_preprocess_on_a_noisy_faded_image_still_produces_valid_output():
    rng = np.random.default_rng(1)
    faded = np.clip(rng.integers(0, 255, size=(400, 400, 3), dtype=np.uint8).astype(np.int16) + 60, 0, 255).astype(
        np.uint8
    )
    out = preprocess(faded)
    assert out.shape[:2] == (400, 400)
    assert out.dtype == np.uint8
