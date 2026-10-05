"""Cutting a streamed answer into things to say.

Text-to-speech wants whole sentences: it reads a sentence's shape to pace and
pitch it, and a fragment sounds wrong. But waiting for the whole answer would
waste the point of streaming. So the answer is spoken a sentence at a time,
each one sent to be synthesised the moment it is complete, while the model is
still writing the next.

Markdown that means nothing aloud (asterisks, backticks, heading marks, link
targets) is removed before a sentence is spoken.
"""

from __future__ import annotations

import re

# A sentence ends at . ! ? (optionally followed by a closing quote/bracket)
# and then whitespace -- but not inside "e.g." or after a lone initial or a
# number like "3.5".
_END = re.compile(r'(?<!\b[A-Z])(?<!\be\.g)(?<!\bi\.e)(?<!\d)([.!?]["\')\]]?)\s+')
_LINE = re.compile(r"\n\s*\n|\n(?=\s*[-*\d]+[.)]?\s)")
MIN_CHARS = 12  # do not speak "Sure." on its own if more follows quickly
MAX_CHARS = 280  # cut a run-on at a comma rather than wait forever
# The first piece is what the person waits for in silence, so a long opening
# sentence is spoken from its first clause break past this length.
FIRST_CLAUSE_CHARS = 40
_CLAUSE = re.compile(r"[,;:]\s+")


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)      # [label](url) -> label
    text = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    text = re.sub(r"\[\d+\]", "", text)                          # citation markers
    text = re.sub(r"[\[(]\s*(?:source|sources)\s*:[^\])]*[\])]", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


class SentenceSplitter:
    def __init__(self) -> None:
        self.buffer = ""
        self.first = True

    def feed(self, text: str) -> list[str]:
        """Add streamed text; return any sentences now complete."""
        self.buffer += text
        out: list[str] = []
        while True:
            cut = None
            for pattern in (_LINE, _END):
                m = pattern.search(self.buffer)
                if m and (cut is None or m.end() < cut):
                    cut = m.end()
            if cut is None and self.first:
                m = next((m for m in _CLAUSE.finditer(self.buffer) if m.start() >= FIRST_CLAUSE_CHARS), None)
                cut = m.end() if m else None
            if cut is None and len(self.buffer) > MAX_CHARS:
                comma = self.buffer.rfind(", ", 0, MAX_CHARS)
                cut = comma + 2 if comma > MIN_CHARS else MAX_CHARS
            if cut is None:
                break
            piece, rest = self.buffer[:cut], self.buffer[cut:]
            if len(clean_for_speech(piece)) < MIN_CHARS and rest.strip() == "":
                break  # too short to say alone; wait for more
            self.buffer = rest
            spoken = clean_for_speech(piece)
            if spoken:
                out.append(spoken)
                self.first = False
        return out

    def flush(self) -> list[str]:
        spoken = clean_for_speech(self.buffer)
        self.buffer = ""
        return [spoken] if spoken else []
