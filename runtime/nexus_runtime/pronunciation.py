"""How Kokoro pronounces words its dictionary does not know.

Kokoro turns text into phonemes with misaki, which falls back to espeak-ng for
out-of-vocabulary words -- names, product names, jargon ("Qdrant",
"Bhoumish"). The espeak-ng build that misaki installs (espeakng-loader 0.2.x)
is broken on macOS: it ignores the data path it is given and looks for its
files at the CI machine's path, then *exits the process* when they are not
there. (Verified 2026-10-04 on every 0.2.x release, inside and outside the
sandbox.) A crashing fallback would take the whole runtime down the first
time someone says a name.

So before Kokoro loads, misaki's espeak fallback is replaced:

- with a working espeak-ng, if Homebrew's is installed (``brew install
  espeak-ng``) -- proper pronunciation of unknown words;
- otherwise with a fallback that spells an unknown word out letter by letter
  ("Q D R A N T") -- understandable, and never fatal.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

HOMEBREW_LIB = "/opt/homebrew/lib/libespeak-ng.dylib"
HOMEBREW_DATA = "/opt/homebrew/share/espeak-ng-data"


class SpellingFallback:
    """misaki fallback: an unknown word becomes its letters, spoken."""

    def __init__(self, british: bool = False) -> None:
        from misaki import en

        self.g2p = en.G2P(trf=False, british=british, fallback=None, unk="")

    def __call__(self, token):
        letters = " ".join(ch.upper() for ch in token.text if ch.isalnum())
        phonemes, _ = self.g2p(letters) if letters else ("", None)
        return (phonemes or None, 1)


def install() -> str:
    """Make misaki safe to use. Returns which fallback is in effect."""
    import espeakng_loader

    if os.path.exists(HOMEBREW_LIB) and os.path.isdir(HOMEBREW_DATA):
        # Point misaki's loader at a build that honours its data path.
        espeakng_loader.get_library_path = lambda: HOMEBREW_LIB
        espeakng_loader.get_data_path = lambda: HOMEBREW_DATA
        logger.info("pronunciation: espeak-ng from Homebrew")
        return "espeak-ng"

    import misaki.espeak

    misaki.espeak.EspeakFallback = SpellingFallback
    logger.info("pronunciation: unknown words spelled out (brew install espeak-ng to pronounce them)")
    return "spelling"
