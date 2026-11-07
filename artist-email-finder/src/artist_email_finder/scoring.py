"""Turn raw sightings of emails into ranked, de-duplicated results."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .models import EmailResult, Finding

BASE_SCORE = {
    "social_bio": 75,   # the artist's own Instagram/TikTok/SoundCloud bio
    "youtube": 70,      # their channel or video descriptions
    "link_page": 70,    # their Linktree / Beacons / etc.
    "website": 65,      # their own website
    "web_search": 35,   # a search-result snippet that mentions them
    "web_page": 40,     # a third-party page found by web search
}

ROLE_HINT = re.compile(
    r"book|mgmt|manage|business|contact|inquir|enquir|info|press|collab|feature|team|music",
    re.IGNORECASE,
)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def name_similarity(a: str, b: str) -> float:
    a, b = _fold(a), _fold(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def name_tokens(name: str) -> list[str]:
    tokens = [_fold(t) for t in re.split(r"[\s._\-&,/]+", name)]
    tokens = [t for t in tokens if len(t) >= 3 and t not in {"the", "and", "official", "music"}]
    joined = _fold(name)
    if len(joined) >= 3 and joined not in tokens:
        tokens.append(joined)
    return tokens


def email_mentions_name(email: str, artist_name: str) -> bool:
    folded = _fold(email)
    return any(t in folded for t in name_tokens(artist_name))


def label(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def rank(findings: list[Finding], artist_name: str, site_domains: set[str]) -> list[EmailResult]:
    by_email: dict[str, list[Finding]] = {}
    for f in findings:
        by_email.setdefault(f.email, []).append(f)

    results = []
    for email, hits in by_email.items():
        local, domain = email.split("@")
        sources = sorted({h.source for h in hits})
        score = max(BASE_SCORE.get(s, 40) for s in sources)
        if ROLE_HINT.search(local):
            score += 10
        if domain in site_domains:
            score += 15
        if email_mentions_name(email, artist_name):
            score += 10
        # Seen in more than one independent place.
        score += min(20, 10 * (len(sources) - 1))
        # Third-party pages only count if the email looks tied to the artist.
        if all(not h.on_artist_page for h in hits) and not (
            email_mentions_name(email, artist_name) or domain in site_domains
        ):
            score -= 15
        score = max(0, min(100, score))
        urls = []
        for h in hits:
            if h.url not in urls:
                urls.append(h.url)
        context = next((h.context for h in hits if h.context), "")
        results.append(EmailResult(email=email, score=score, confidence=label(score),
                                   sources=sources, urls=urls, context=context))
    results.sort(key=lambda r: (-r.score, r.email))
    return results
