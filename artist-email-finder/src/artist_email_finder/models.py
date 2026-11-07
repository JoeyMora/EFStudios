"""Plain data types shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Artist:
    name: str
    links: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    # Size metrics: spotify_followers, instagram_followers, tiktok_followers,
    # lastfm_listeners, youtube_subscribers.
    metrics: dict[str, int] = field(default_factory=dict)
    location: str = ""
    spotify_url: str = ""
    discovered_via: str = ""
    image_url: str = ""
    gender: str = ""          # female | male | nonbinary (as published, e.g. on MusicBrainz)
    artist_type: str = ""     # solo | group
    country: str = ""         # ISO 3166-1 alpha-2 country of origin
    tags: list[str] = field(default_factory=list)   # descriptor tags (not genres)
    profiled: bool = False    # MusicBrainz profile already merged in (skip re-fetching)


@dataclass
class Finding:
    """One sighting of an email address in one place."""

    email: str
    source: str        # e.g. "website", "link_page", "social_bio", "youtube", "web_search"
    url: str
    context: str = ""
    on_artist_page: bool = True   # False for web-search snippets and third-party pages


@dataclass
class EmailResult:
    email: str
    score: int
    confidence: str
    sources: list[str]
    urls: list[str]
    context: str = ""


@dataclass
class ArtistResult:
    artist: Artist
    emails: list[EmailResult] = field(default_factory=list)
    pages_checked: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    instagram: str = ""     # handle
    tiktok: str = ""        # handle
    website: str = ""
    image_url: str = ""
    links: list[str] = field(default_factory=list)   # every artist link seen
    metrics: dict[str, int] = field(default_factory=dict)
