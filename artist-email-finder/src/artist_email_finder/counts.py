"""Parse follower counts like '12.5K Followers' out of profile text."""

from __future__ import annotations

import re

_NUM = r"(\d{1,3}(?:[,.]\d{3})+|\d+(?:[.,]\d+)?)\s*([KkMmBb])?"
_MULT = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_count(text: str) -> int | None:
    m = re.fullmatch(r"\s*" + _NUM + r"\s*", text or "")
    return _to_int(m.group(1), m.group(2)) if m else None


def _to_int(number: str, suffix: str | None) -> int:
    if suffix:
        return int(round(float(number.replace(",", ".")) * _MULT[suffix.lower()]))
    if re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", number):
        return int(re.sub(r"[,.]", "", number))
    return int(float(number.replace(",", ".")))


def followers_in(text: str) -> int | None:
    """First 'N followers' figure in text (Instagram/TikTok/SoundCloud style)."""
    m = re.search(_NUM + r"\s+followers\b", text or "", re.IGNORECASE)
    if m:
        return _to_int(m.group(1), m.group(2))
    m = re.search(r"\bfollowers\W{0,3}" + _NUM, text or "", re.IGNORECASE)
    return _to_int(m.group(1), m.group(2)) if m else None
