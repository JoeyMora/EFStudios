"""Plain data types shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Artist:
    name: str
    links: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    followers: int | None = None
    location: str = ""
    spotify_url: str = ""
    discovered_via: str = ""


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
