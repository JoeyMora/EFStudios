"""'Is this artist around my level?' — compares engagement, not followers.

Engagement is how many people actually interact with typical content:
  instagram_reel_views       median views per reel (Reels tab, via the bookmarklet)
  instagram_reel_engagement  median likes + comments per reel (last 12 reels, API)
  tiktok_views               median views per video (pinned videos skipped)
  youtube_views              median views per video (last 10 uploads)
Medians keep one viral post from skewing the number. Each metric is compared
only with the same metric of yours; metrics are averaged within a platform,
then platforms are averaged, so Instagram doesn't count double.
"""

from __future__ import annotations

import math
import statistics

ENGAGEMENT = {
    "instagram_reel_views": "IG views / reel",
    "instagram_reel_engagement": "IG likes+comments / reel",
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
PLATFORMS = {
    "instagram": ["instagram_reel_views", "instagram_reel_engagement"],
    "tiktok": ["tiktok_views"],
    "youtube": ["youtube_views"],
}


def typical(values) -> int | None:
    values = [v for v in values if v is not None]
    return int(statistics.median(values)) if values else None


def platform_ratios(theirs: dict, mine: dict) -> dict[str, float]:
    """Per-platform ratio of their engagement to mine (geometric mean of the
    platform's metrics that both sides have)."""
    out = {}
    for platform, keys in PLATFORMS.items():
        logs = [math.log(max(theirs[k], 1) / mine[k]) for k in keys
                if theirs.get(k) is not None and mine.get(k)]
        if logs:
            out[platform] = math.exp(sum(logs) / len(logs))
    return out


def ratio(theirs: dict, mine: dict) -> float | None:
    """Their engagement relative to mine (1.0 = same), averaged over platforms
    (geometric mean). None if nothing can be compared."""
    per = platform_ratios(theirs, mine)
    if not per:
        return None
    return math.exp(sum(math.log(r) for r in per.values()) / len(per))


def engagement_rate(metrics: dict) -> float | None:
    """Reel likes+comments as a % of followers (very low = possibly inflated followers)."""
    e, f = metrics.get("instagram_reel_engagement"), metrics.get("instagram_followers")
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
