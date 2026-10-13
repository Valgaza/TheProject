"""The one sentence that tells a model what time it is.

A model takes its training cutoff for the present unless told otherwise: "the
latest" race is one from years ago, and a 2026 deadline in a photo reads as a
hypothetical. Every prompt that can meet a date carries this line. It goes in
the latest message, never the system prompt, so the cached prefix doesn't
change every minute.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta


def describe_now(at: datetime | None = None) -> str:
    """"Monday, 12 October 2026, 14:05 IST (UTC+05:30)", in this machine's
    time zone. ``at`` is the moment to describe; now if not given."""
    moment = (at or datetime.now()).astimezone()
    offset = moment.strftime("%z")
    return (f"{moment.strftime('%A, %-d %B %Y, %H:%M')} {moment.strftime('%Z')} "
            f"(UTC{offset[:3]}:{offset[3:]})")


def now_note(at: datetime | None = None) -> str:
    return (f"Right now it is {describe_now(at)}. This is the real present: read every "
            "date against it, and treat dates near it as current, not hypothetical.")


# ── Counting days, which small models get wrong ──────────────────────────────
#
# Told today's date, Qwen3-8B and the 4B vision model still miscount: "20
# days" from 12 October to 21 November, and 21 November 2026 "a Monday"
# (2026-10-12, both models). So the counting is done here and handed to them
# as facts: every date the conversation mentions, with its weekday and its
# distance, and the first of the next few months as landmarks for a date the
# model only finds in an image.

_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
_MONTHS.update({name[:3]: n for name, n in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december|" \
         r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
_ORDINALS = {w: n for n, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
    "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split(), 1)}
_ORDINALS.update({f"twenty-{w}": 20 + n for w, n in list(_ORDINALS.items()) if n < 10})
_ORDINALS.update({f"twenty {w}": 20 + n for w, n in list(_ORDINALS.items()) if n < 10})
_ORDINALS.update({"thirtieth": 30, "thirty-first": 31, "thirty first": 31})
ORDINALS = _ORDINALS
_DAY = r"(\d{1,2}|" + "|".join(sorted((re.escape(w) for w in _ORDINALS), key=len, reverse=True)) + \
       r")(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(\d{4}))?"
_PATTERNS = [
    (re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}{_YEAR}\b", re.IGNORECASE), "dmy"),
    (re.compile(rf"\b{_MONTH}\s+{_DAY}{_YEAR}\b", re.IGNORECASE), "mdy"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
]
MAX_DATES = 6
MONTHS_AHEAD = 6


def dates_in(text: str, today: date) -> list[date]:
    """Calendar dates written out in ``text`` ("21 November 2026", "Nov 3",
    "2026-11-21"). A date with no year is taken as its next occurrence.
    Numeric forms like 03/11 are left alone: day-month or month-day can't be
    told apart."""
    found: list[date] = []
    for pattern, order in _PATTERNS:
        for m in pattern.finditer(text):
            try:
                if order == "iso":
                    d = date(int(m[1]), int(m[2]), int(m[3]))
                else:
                    day, month, year = (m[1], m[2], m[3]) if order == "dmy" else (m[2], m[1], m[3])
                    day = str(_ORDINALS.get(day.lower(), day))
                    month_n = _MONTHS[month.lower().rstrip(".")]
                    if year:
                        d = date(int(year), month_n, int(day))
                    else:
                        d = date(today.year, month_n, int(day))
                        if d < today:
                            d = date(today.year + 1, month_n, int(day))
            except (ValueError, KeyError):
                continue
            if d not in found:
                found.append(d)
    return found


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_RELATIVE = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|day after tomorrow|day before yesterday|"
    r"(?:this|next|last|coming|this coming)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week)|"
    r"on\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"in\s+(\d{1,3}|a|one|two|three|four|five|six|seven)\s+(days?|weeks?))\b", re.IGNORECASE)
_SMALL = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def relative_dates(text: str, today: date) -> list[tuple[str, date]]:
    """Relative dates in ``text``, resolved by fixed rules so the models never
    have to: "tomorrow", "in 3 days", "on Friday" / "this Friday" (the coming
    one), "next Friday" (Friday of next week, which is how it is usually meant
    when said early in a week; the note says so, so an answer can check)."""
    out: list[tuple[str, date]] = []
    for m in _RELATIVE.finditer(text):
        phrase = m[1].lower()
        words = phrase.split()
        d: date | None = None
        if phrase in ("today", "tonight"):
            d = today
        elif phrase == "tomorrow":
            d = today + timedelta(days=1)
        elif phrase == "yesterday":
            d = today - timedelta(days=1)
        elif phrase == "day after tomorrow":
            d = today + timedelta(days=2)
        elif phrase == "day before yesterday":
            d = today - timedelta(days=2)
        elif words[0] == "in":
            n = int(words[1]) if words[1].isdigit() else _SMALL[words[1]]
            d = today + timedelta(days=n * (7 if words[2].startswith("week") else 1))
        elif words[-1] == "week":
            monday = today - timedelta(days=today.weekday())
            d = monday + timedelta(days={"next": 7, "last": -7}.get(words[0], 0))
        else:
            target = _WEEKDAYS.index(words[-1])
            ahead = (target - today.weekday()) % 7 or 7  # the coming one, never today
            if words[0] == "next":
                monday = today - timedelta(days=today.weekday())
                d = monday + timedelta(days=7 + target)
            elif words[0] == "last":
                d = today - timedelta(days=(today.weekday() - target) % 7 or 7)
            else:
                d = today + timedelta(days=ahead)
        if d is not None and all(p != phrase for p, _ in out):
            out.append((phrase, d))
    return out


def _distance(d: date, today: date) -> str:
    days = (d - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    return f"{days} days from today" if days > 0 else f"{-days} days ago"


def _named(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B %Y')}"


def calendar_note(texts: list[str], at: datetime | None = None) -> str:
    """The dates in ``texts`` worked out, plus month landmarks."""
    today = (at or datetime.now()).astimezone().date()
    mentioned: list[date] = []
    for text in texts:
        for d in dates_in(text, today):
            if d not in mentioned:
                mentioned.append(d)
    lines = ["Date arithmetic, worked out for you. Use it as fact and never mention these notes."]
    relative = relative_dates(texts[-1], today) if texts else []
    if relative:
        lines.append("In the latest message: " + "; ".join(
            f"\"{p}\" is {_named(d)} ({_distance(d, today)})"
            + (f" ({d.strftime('%A')} of next week; if they meant this coming one, say which you took)"
               if p.startswith("next ") and not p.endswith("week") else "")
            for p, d in relative) + ".")
    if mentioned:
        lines.append("Dates in this conversation, with their weekday and distance: " + "; ".join(
            f"{_named(d)} is {_distance(d, today)}" for d in mentioned[-MAX_DATES:]) + ".")
    # For a date only the model can see (in an image): one addition per
    # month, which a small model gets right where counting across months it
    # doesn't (the 4B said 49 and 50 days for a 40-day wait).
    rules = [f"in {today.strftime('%B %Y')}, the day minus {today.day}"]
    first = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    for _ in range(MONTHS_AHEAD):
        rules.append(f"in {first.strftime('%B %Y')}, the day plus {(first - today).days - 1}")
        first = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    lines.append("Days from today to any other date: " + "; ".join(rules) + ".")
    return "\n".join(lines)
