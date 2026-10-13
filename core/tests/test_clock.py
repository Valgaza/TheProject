"""The date and time every turn carries, and the counting done for the models."""

from __future__ import annotations

from datetime import date, datetime, timezone

from nexus_core.clock import calendar_note, dates_in, now_note

AT = datetime(2026, 10, 12, 11, 35, tzinfo=timezone.utc)
TODAY = date(2026, 10, 12)


def test_the_note_names_the_real_present():
    note = now_note(AT)
    assert "12 October 2026" in note and "real present" in note


def test_written_dates_are_found_in_their_usual_forms():
    text = "Report due 21 November 2026, viva Nov 3rd, review on the 2nd of December, launch 2027-01-15."
    assert dates_in(text, TODAY) == [date(2026, 11, 21), date(2026, 12, 2), date(2026, 11, 3), date(2027, 1, 15)]


def test_a_date_without_a_year_is_the_next_one():
    assert dates_in("my birthday is 5 March", TODAY) == [date(2027, 3, 5)]
    assert dates_in("see you 20 October", TODAY) == [date(2026, 10, 20)]


def test_ambiguous_or_impossible_dates_are_left_alone():
    assert dates_in("on 03/11 and 31 February 2026", TODAY) == []


def test_the_counting_is_done_for_the_model():
    note = calendar_note(["[Image attached: notice.jpg. Final report due: 21 November 2026]"], AT)
    assert "Saturday 21 November 2026 is 40 days from today" in note
    # For a date only in an image: 21 November is 21 + 19 = 40.
    assert "in November 2026, the day plus 19" in note and "in October 2026, the day minus 12" in note
