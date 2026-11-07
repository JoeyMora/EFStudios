"""Momentum ("pull"): how much an artist's content travels and how fast it's growing.

Two signals, each scored 0-1:
  reach   typical views per reel / video divided by followers on that platform.
          Content that reaches far beyond the follower base means the algorithm
          is pushing it. 0.01x of followers scores 0; 3x or more scores 1.
  growth  change in engagement between checks, as a weekly rate (needs two
          snapshots at least 3 days apart). Flat scores 0.5; doubling every
          week scores 1; halving scores 0.

Momentum = 100 x the average of whichever signals exist (None if neither).
"""

from __future__ import annotations

import math

from .level import ENGAGEMENT

REACH_PAIRS = [("instagram_reel_views", "instagram_followers", "per reel"),
               ("tiktok_views", "tiktok_followers", "per TikTok")]
MIN_DAYS = 3


def reach(metrics: dict) -> tuple[float, str] | None:
    """(best views/followers ratio, label) or None."""
    best = None
    for views, followers, label in REACH_PAIRS:
        v, f = metrics.get(views), metrics.get(followers)
        if v is not None and f:
            r = v / f
            if best is None or r > best[0]:
                best = (r, label)
    return best


def growth(snapshots: list[tuple[float, dict]]) -> float | None:
    """Weekly growth factor of engagement between the oldest and newest snapshot."""
    if len(snapshots) < 2:
        return None
    (t0, m0), (t1, m1) = snapshots[0], snapshots[-1]
    days = (t1 - t0) / 86400
    if days < MIN_DAYS:
        return None
    logs = [math.log(max(m1[k], 1) / max(m0[k], 1)) for k in ENGAGEMENT
            if m0.get(k) is not None and m1.get(k) is not None]
    if not logs:
        return None
    return math.exp(sum(logs) / len(logs) * 7 / days)


def score(metrics: dict, snapshots: list[tuple[float, dict]] | None = None) -> tuple[float | None, str]:
    """(momentum 0-100 or None, short human explanation)."""
    parts, notes = [], []
    r = reach(metrics)
    if r:
        parts.append(min(1.0, max(0.0, (math.log10(max(r[0], 1e-6)) + 2) / (math.log10(3) + 2))))
        notes.append(f"views {r[0]:.1f}× their followers {r[1]}" if r[0] >= .1 else
                     f"views {r[0] * 100:.0f}% of their followers {r[1]}")
    g = growth(snapshots or [])
    if g is not None:
        parts.append(min(1.0, max(0.0, .5 + math.log2(g) / 2)))
        pct = (g - 1) * 100
        notes.append(f"engagement {'up' if pct >= 0 else 'down'} {abs(pct):.0f}% a week")
    if not parts:
        return None, ""
    return round(100 * sum(parts) / len(parts), 1), " · ".join(notes)
