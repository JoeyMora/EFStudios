"""MusicBrainz (free, no key): artist links, and discovery by genre tag + area.

API terms ask for at most 1 request per second and a descriptive User-Agent.
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..net import HttpClient
from ..models import Artist
from ..scoring import name_similarity

log = logging.getLogger(__name__)

API = "https://musicbrainz.org/ws/2"
DELAY = 1.1


def _lucene_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class MusicBrainz:
    def __init__(self, http: HttpClient):
        self.http = http

    def _get(self, path: str, params: dict) -> dict | None:
        params = {**params, "fmt": "json"}
        return self.http.get_json(f"{API}/{path}", params=params, delay=DELAY)

    def urls_for(self, mbid: str) -> list[str]:
        data = self._get(f"artist/{mbid}", {"inc": "url-rels"})
        if not data:
            return []
        urls = []
        for rel in data.get("relations", []):
            resource = (rel.get("url") or {}).get("resource")
            if resource and not rel.get("ended") and resource not in urls:
                urls.append(resource)
        return urls

    def find_links(self, name: str) -> list[str]:
        """Links (website, socials, YouTube...) for the best-matching artist name."""
        data = self._get("artist", {"query": f'artist:"{_lucene_escape(name)}"', "limit": 5})
        if not data:
            return []
        for cand in data.get("artists", []):
            if int(cand.get("score", 0)) >= 90 and name_similarity(name, cand.get("name", "")) >= 0.9:
                return self.urls_for(cand["id"])
        return []

    def discover(self, genre: str = "", area: str = "") -> Iterator[Artist]:
        clauses = []
        if genre:
            clauses.append(f'tag:"{_lucene_escape(genre)}"')
        if area:
            clauses.append(f'(area:"{_lucene_escape(area)}" OR beginarea:"{_lucene_escape(area)}")')
        if not clauses:
            raise ValueError("MusicBrainz discovery needs a genre and/or an area")
        query = " AND ".join(clauses)
        offset = 0
        while offset < 2000:
            data = self._get("artist", {"query": query, "limit": 100, "offset": offset})
            batch = (data or {}).get("artists", [])
            if not batch:
                return
            for a in batch:
                tags = [t["name"] for t in sorted(a.get("tags", []),
                                                  key=lambda t: -t.get("count", 0))][:5]
                location = (a.get("begin-area") or a.get("area") or {}).get("name", "")
                artist = Artist(name=a["name"], genres=tags, location=location,
                                discovered_via="musicbrainz")
                artist.links = self.urls_for(a["id"])
                yield artist
            offset += len(batch)
