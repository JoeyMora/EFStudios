"""MusicBrainz (free, no key): artist profiles (links, genres, gender, solo/group,
country), and filtered search by genre/tag, gender, type, country and area.

API terms ask for at most 1 request per second and a descriptive User-Agent.
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..models import Artist
from ..net import HttpClient
from ..scoring import name_similarity

log = logging.getLogger(__name__)

API = "https://musicbrainz.org/ws/2"
DELAY = 1.1


def _q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _type(t: str | None) -> str:
    t = (t or "").lower()
    return "group" if t in {"group", "orchestra", "choir"} else "solo" if t == "person" else ""


def _gender(g: str | None) -> str:
    g = (g or "").lower()
    return {"female": "female", "male": "male", "non-binary": "nonbinary"}.get(g, "")


class MusicBrainz:
    def __init__(self, http: HttpClient):
        self.http = http

    def _get(self, path: str, params: dict) -> dict | None:
        return self.http.get_json(f"{API}/{path}", params={**params, "fmt": "json"}, delay=DELAY)

    def profile_by_id(self, mbid: str) -> dict:
        data = self._get(f"artist/{mbid}", {"inc": "url-rels+genres+tags"})
        if not data:
            return {}
        urls = []
        for rel in data.get("relations", []):
            resource = (rel.get("url") or {}).get("resource")
            if resource and not rel.get("ended") and resource not in urls:
                urls.append(resource)
        by_count = lambda xs: [x["name"].lower() for x in sorted(xs or [], key=lambda x: -x.get("count", 0))]
        genres = by_count(data.get("genres"))
        return {
            "id": mbid,
            "name": data.get("name", ""),
            "gender": _gender(data.get("gender")),
            "artist_type": _type(data.get("type")),
            "country": data.get("country") or "",
            "location": ((data.get("begin-area") or data.get("area") or {}).get("name", "")),
            "genres": genres[:5],
            "tags": [t for t in by_count(data.get("tags")) if t not in genres][:10],
            "urls": urls,
            "disambiguation": data.get("disambiguation", ""),
        }

    def profile(self, name: str) -> dict:
        """Profile of the best exact-name match, or {}."""
        data = self._get("artist", {"query": f"artist:{_q(name)}", "limit": 5})
        for cand in (data or {}).get("artists", []):
            if int(cand.get("score", 0)) >= 90 and name_similarity(name, cand.get("name", "")) >= 0.9:
                return self.profile_by_id(cand["id"])
        return {}

    def find_links(self, name: str) -> list[str]:
        return self.profile(name).get("urls", [])

    @staticmethod
    def build_query(genres=(), tags=(), gender="any", artist_type="any", countries=(),
                    identity_tags=(), areas=()) -> str:
        clauses = []
        style = [*genres, *tags]
        if style:
            clauses.append("(" + " OR ".join(f"tag:{_q(t)}" for t in style) + ")")
        if gender in ("female", "male"):
            clauses.append(f"gender:{gender}")
        elif gender == "nonbinary":
            clauses.append('gender:"non-binary"')
        if artist_type == "group":
            clauses.append("type:group")
        elif artist_type == "solo":
            clauses.append("type:person")
        origin = [f"country:{c}" for c in countries] + [f"tag:{_q(t)}" for t in identity_tags]
        if origin:
            clauses.append("(" + " OR ".join(origin) + ")")
        if areas:
            clauses.append("(" + " OR ".join(f"area:{_q(a)} OR beginarea:{_q(a)}" for a in areas) + ")")
        return " AND ".join(clauses)

    def search(self, query: str, max_results: int = 1000) -> Iterator[Artist]:
        """Artists matching an advanced query, each with its full profile."""
        if not query:
            return
        offset = 0
        while offset < max_results:
            data = self._get("artist", {"query": query, "limit": 100, "offset": offset})
            batch = (data or {}).get("artists", [])
            if not batch:
                return
            for a in batch:
                prof = self.profile_by_id(a["id"])
                if prof:
                    yield artist_from_profile(prof, discovered_via="musicbrainz")
            offset += len(batch)

    def discover(self, genre: str = "", area: str = "") -> Iterator[Artist]:
        if not genre and not area:
            raise ValueError("MusicBrainz discovery needs a genre and/or an area")
        return self.search(self.build_query(genres=[genre] if genre else [], areas=[area] if area else []))


def artist_from_profile(prof: dict, discovered_via: str = "") -> Artist:
    return Artist(name=prof["name"], links=list(prof.get("urls", [])), genres=list(prof.get("genres", [])),
                  location=prof.get("location", ""), gender=prof.get("gender", ""),
                  artist_type=prof.get("artist_type", ""), country=prof.get("country", ""),
                  tags=list(prof.get("tags", [])), discovered_via=discovered_via, profiled=True)
