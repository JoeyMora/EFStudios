"""Matching artists against a prompt's search plan, and building "For you" picks."""

from __future__ import annotations

from itertools import zip_longest
from typing import Iterable, Iterator

from .prompt import SearchPlan
from .models import Artist

COUNTRY_NAMES = {
    "JP": "Japan", "KR": "South Korea", "CN": "China", "TW": "Taiwan", "HK": "Hong Kong", "PH": "Philippines",
    "VN": "Vietnam", "TH": "Thailand", "ID": "Indonesia", "MY": "Malaysia", "SG": "Singapore", "IN": "India",
    "PK": "Pakistan", "BD": "Bangladesh", "MN": "Mongolia", "US": "USA", "GB": "UK", "CA": "Canada",
    "AU": "Australia", "MX": "Mexico", "PR": "Puerto Rico", "CO": "Colombia", "AR": "Argentina", "CL": "Chile",
    "PE": "Peru", "VE": "Venezuela", "DO": "Dominican Republic", "CU": "Cuba", "BR": "Brazil", "NG": "Nigeria",
    "GH": "Ghana", "ZA": "South Africa", "KE": "Kenya", "DE": "Germany", "FR": "France", "SE": "Sweden",
    "IE": "Ireland", "NZ": "New Zealand", "ES": "Spain", "IT": "Italy", "NL": "Netherlands",
}
FEMALE_TAGS = ("female vocalist", "female fronted", "female-fronted", "women in music", "girl group", "riot grrrl")


def _overlaps(wanted: Iterable[str], have: Iterable[str]) -> list[str]:
    have = [h.lower() for h in have]
    out = []
    for w in wanted:
        w = w.lower()
        if any(w == h or w in h or (len(h) > 3 and h in w) for h in have):
            out.append(w)
    return out


def match(artist: Artist, plan: SearchPlan, trusted_style: bool = False) -> tuple[bool, float, list[str]]:
    """Does this artist fit the plan? Returns (keep, score, reason chips).

    Hard misses (a published gender/type/country that contradicts the request,
    or known genres with no overlap) are rejected. Unknown facts are allowed
    through with a lower score, so results aren't limited to artists with
    complete database entries."""
    score, reasons = 1.0, []
    tags = [t.lower() for t in artist.tags]

    if plan.gender != "any":
        if artist.gender:
            if artist.gender != plan.gender:
                return False, 0, []
            score += .5
        elif plan.gender == "female" and any(any(f in t for f in FEMALE_TAGS) for t in tags):
            score += .4
        else:
            score -= .3
        reasons.append({"female": "Female", "male": "Male", "nonbinary": "Non-binary"}[plan.gender])

    if plan.artist_type != "any":
        if artist.artist_type:
            if artist.artist_type != plan.artist_type:
                return False, 0, []
            score += .3
        else:
            score -= .2
        reasons.append("Band" if plan.artist_type == "group" else "Solo")

    if plan.countries or plan.identity_tags:
        id_hit = _overlaps(plan.identity_tags, tags)
        if artist.country in plan.countries:
            score += .5
            reasons.append(COUNTRY_NAMES.get(artist.country, artist.country))
        elif id_hit:
            score += .5
            reasons.append(id_hit[0])
        elif artist.country:
            return False, 0, []
        else:
            score -= .3

    if plan.areas:
        if any(a.lower() in (artist.location or "").lower() for a in plan.areas):
            score += .4
            reasons.append(artist.location)
        elif artist.location:
            score -= .4

    wanted = plan.genres + plan.tags
    if wanted:
        hits = _overlaps(wanted, artist.genres + artist.tags)
        if hits:
            score += .4 * min(len(hits), 3)
            reasons += hits[:2]
        elif artist.genres and not trusted_style:
            return False, 0, []
        elif not artist.genres:
            score -= .2
    return True, round(score, 2), reasons


def interleave(*sources: Iterable) -> Iterator:
    """Round-robin over several (possibly slow) generators."""
    iters = [iter(s) for s in sources]
    while iters:
        alive = []
        for it in iters:
            try:
                yield next(it)
                alive.append(it)
            except StopIteration:
                pass
        iters = alive


def genre_profile(liked: list[dict], passed: list[dict]) -> list[str]:
    """Genres that show up in saved artists more than in passed ones, most telling first."""
    counts: dict[str, float] = {}
    for lead in liked:
        for g in lead.get("genre_list", [])[:3]:
            counts[g] = counts.get(g, 0) + 1
    for lead in passed:
        for g in lead.get("genre_list", [])[:3]:
            counts[g] = counts.get(g, 0) - .7
    return [g for g, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n > 0]
