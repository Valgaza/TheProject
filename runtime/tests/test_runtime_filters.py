"""Text filters on the generated stream: no reasoning, nothing past a stop."""

from __future__ import annotations

from nexus_runtime.mlx_backend import StopFilter, ThinkFilter


def feed_all(f, pieces):
    return "".join(f.feed(p) for p in pieces) + f.flush()


def test_think_spans_are_dropped_even_when_split_across_tokens():
    pieces = ["<th", "ink>plan the ", "answer</thi", "nk>The answer", " is 4."]
    assert feed_all(ThinkFilter(), pieces) == "The answer is 4."


def test_text_without_think_tags_passes_through_unchanged():
    pieces = ["Use <b>bold</b> ", "if you ", "like."]
    assert feed_all(ThinkFilter(), pieces) == "Use <b>bold</b> if you like."


def test_an_unclosed_think_block_never_leaks():
    assert feed_all(ThinkFilter(), ["<think>still thinking about it"]) == ""


def test_stop_string_cuts_the_text_even_when_split():
    f = StopFilter(["\nUser:"])
    assert feed_all(f, ["Sure, here", " it is.\nUs", "er: next"]) == "Sure, here it is."
    assert f.hit


def test_no_stop_strings_means_no_buffering():
    f = StopFilter([])
    assert f.feed("abc") == "abc"
