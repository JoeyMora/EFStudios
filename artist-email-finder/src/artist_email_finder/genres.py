"""Sort tags into genres vs. descriptors, and merge genre sources."""

from __future__ import annotations

import re

GENRE_HINT = re.compile(
    r"rock|pop|hop|rap|soul|r&b|rnb|punk|metal|jazz|folk|house|techno|trap|drill|core\b|wave|gaze|"
    r"country|blues|funk|edm|electro|indie|emo|dance|reggae|afro|latin|gospel|lo-?fi|ambient|garage|"
    r"grime|phonk|disco|grunge|ska|swing|bossa|samba|cumbia|corrido|bachata|salsa|k-pop|j-pop|j-rock|"
    r"dubstep|drum and bass|dnb|breakbeat|trance|bluegrass|americana|singer-songwriter|shoegaze|club|"
    r"boom bap|neo soul|amapiano|dancehall|reggaeton|hyperpop|synth|psychedelic|experimental|alternative|"
    r"acoustic|orchestral|classical|opera|musical|worship|christian",
    re.IGNORECASE,
)
NOT_GENRE = re.compile(
    r"seen live|favou?rite|vocalist|female|male|fronted|\bband\b|singer$|under \d|\d{4}s?$|^\d|"
    r"american$|british$|japanese$|korean$|canadian$|australian$|mexican$|german$|french$|"
    r"awesome|love|beautiful|check out|spotify|my |albums? i own|usa$|uk$",
    re.IGNORECASE,
)


def is_genre(tag: str) -> bool:
    tag = tag.strip().lower()
    return bool(tag) and len(tag) <= 30 and bool(GENRE_HINT.search(tag)) and not NOT_GENRE.search(tag)


def split_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """(genres, descriptors), each de-duplicated, in input order."""
    genres, other = [], []
    for t in tags:
        t = t.strip().lower()
        if not t:
            continue
        (genres if is_genre(t) else other).append(t)
    return _dedupe(genres), _dedupe(other)


def merge(*lists: list[str], limit: int = 5) -> list[str]:
    return _dedupe([g.strip().lower() for lst in lists for g in (lst or []) if g and g.strip()])[:limit]


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        key = re.sub(r"[^a-z0-9&]", "", i)
        if key and key not in seen:
            seen.add(key)
            out.append(i)
    return out
