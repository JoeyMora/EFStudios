"""'Is this artist around my level?' — compares audience size on shared platforms."""

from __future__ import annotations

import math

METRICS = {
    "instagram_followers": "Instagram followers",
    "tiktok_followers": "TikTok followers",
    "spotify_followers": "Spotify followers",
    "lastfm_listeners": "Last.fm listeners",
    "youtube_subscribers": "YouTube subscribers",
}


def ratio(theirs: dict, mine: dict) -> float | None:
    """Their size relative to mine (1.0 = same), averaged over the platforms
    where both numbers are known (geometric mean). None if nothing to compare."""
    logs = []
    for key in METRICS:
        a, b = theirs.get(key), mine.get(key)
        if a is not None and b:  # my count must be > 0
            logs.append(math.log(max(a, 1) / b))
    if not logs:
        return None
    return math.exp(sum(logs) / len(logs))


def bucket(r: float | None, low: float, high: float) -> str:
    """'unknown', 'smaller', 'match' or 'bigger'."""
    if r is None:
        return "unknown"
    if r < low:
        return "smaller"
    if r > high:
        return "bigger"
    return "match"


def describe(r: float | None) -> str:
    if r is None:
        return "?"
    if r >= 10:
        return f"{r:.0f}×"
    if r >= 1:
        return f"{r:.1f}×"
    return f"{r:.2f}×"
