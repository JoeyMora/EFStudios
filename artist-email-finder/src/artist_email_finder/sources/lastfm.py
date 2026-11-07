"""Last.fm API (free key): discovery by genre tag or "similar to" a seed artist,
plus listener counts used for level matching.
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..net import HttpClient
from ..models import Artist

log = logging.getLogger(__name__)

API = "https://ws.audioscrobbler.com/2.0/"


class LastFm:
    def __init__(self, http: HttpClient, api_key: str):
        self.http = http
        self.api_key = api_key

    def _get(self, method: str, **params) -> dict:
        data = self.http.get_json(API, params={"method": method, "api_key": self.api_key,
                                               "format": "json", **params}, delay=0.25)
        if not data or "error" in data:
            return {}
        return data

    def info(self, name: str) -> dict:
        """{"listeners": int, "tags": [...], "url": str} or {}."""
        artist = self._get("artist.getinfo", artist=name, autocorrect=1).get("artist") or {}
        if not artist:
            return {}
        tags = [t["name"] for t in ((artist.get("tags") or {}).get("tag") or [])]
        listeners = (artist.get("stats") or {}).get("listeners")
        return {"listeners": int(listeners) if listeners else None, "tags": tags,
                "url": artist.get("url", "")}

    def tag_artists(self, tag: str, max_pages: int = 40) -> Iterator[Artist]:
        """Artists for a genre tag, most-listened first. Smaller ones are on later pages."""
        for page in range(1, max_pages + 1):
            data = self._get("tag.gettopartists", tag=tag, limit=50, page=page)
            items = (data.get("topartists") or {}).get("artist") or []
            if not items:
                return
            for a in items:
                yield Artist(name=a["name"], genres=[tag], discovered_via="lastfm")

    def similar(self, seed: str, limit: int = 100) -> Iterator[Artist]:
        data = self._get("artist.getsimilar", artist=seed, limit=limit, autocorrect=1)
        for a in (data.get("similarartists") or {}).get("artist") or []:
            yield Artist(name=a["name"], discovered_via=f"lastfm: similar to {seed}")

    def similar_scored(self, seed: str, limit: int = 40) -> list[tuple[str, float]]:
        """[(name, match 0-1)] for artists similar to seed."""
        data = self._get("artist.getsimilar", artist=seed, limit=limit, autocorrect=1)
        out = []
        for a in (data.get("similarartists") or {}).get("artist") or []:
            try:
                out.append((a["name"], float(a.get("match") or 0)))
            except (KeyError, ValueError):
                continue
        return out

    def top_tags(self, name: str) -> list[str]:
        """Listener tags for an artist, most-used first (genres and descriptors mixed)."""
        data = self._get("artist.gettoptags", artist=name, autocorrect=1)
        tags = (data.get("toptags") or {}).get("tag") or []
        return [t["name"].lower() for t in tags if int(t.get("count") or 0) >= 5][:15]
