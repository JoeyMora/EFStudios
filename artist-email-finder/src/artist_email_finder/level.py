"""'Is this artist around my level?' — compares engagement, not followers.

Engagement is how many people actually interact with a typical post:
  instagram_engagement  median likes + comments per post (last 12 posts)
  tiktok_views          median views per video (recent, pinned videos skipped)
  youtube_views         median views per video (last 10 uploads)
Medians keep one viral post from skewing the number. Each platform is compared
only with the same platform, then the ratios are averaged.
"""

from __future__ import annotations

import math
import statistics

ENGAGEMENT = {
    "instagram_engagement": "IG likes+comments / post",
    "tiktok_views": "TikTok views / video",
    "youtube_views": "YouTube views / video",
}
# Shown for context only; not used for level.
AUDIENCE = {
    "instagram_followers": "Instagram followers",
    "tiktok_followers": "TikTok followers",
    "spotify_followers": "Spotify followers",
    "lastfm_listeners": "Last.fm listeners",
    "youtube_subscribers": "YouTube subscribers",
}
METRICS = {**ENGAGEMENT, **AUDIENCE}


def typical(values) -> int | None:
    values = [v for v in values if v is not None]
    return int(statistics.median(values)) if values else None


def ratio(theirs: dict, mine: dict) -> float | None:
    """Their engagement relative to mine (1.0 = same), as the geometric mean
    over platforms where both numbers are known. None if nothing to compare."""
    logs = []
    for key in ENGAGEMENT:
        a, b = theirs.get(key), mine.get(key)
        if a is not None and b:  # my number must be > 0
            logs.append(math.log(max(a, 1) / b))
    if not logs:
        return None
    return math.exp(sum(logs) / len(logs))


def engagement_rate(metrics: dict) -> float | None:
    """Instagram engagement as a % of followers (context: very low = likely inflated)."""
    e, f = metrics.get("instagram_engagement"), metrics.get("instagram_followers")
    if e is None or not f:
        return None
    return 100.0 * e / f


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
