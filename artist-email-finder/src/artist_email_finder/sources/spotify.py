"""Spotify Web API: discover artists by genre (needs a free developer app).

Spotify doesn't expose an artist's location, so --area is applied through
MusicBrainz instead. Follower counts are used for level matching.
Some fields (followers, genres) may be absent depending on your app's access
level; filters are skipped for artists that don't have them.
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

from ..net import HttpClient
from ..models import Artist
from ..scoring import name_similarity

log = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"


class Spotify:
    def __init__(self, http: HttpClient, client_id: str, client_secret: str):
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = ""
        self._token_expiry = 0.0

    def _auth_header(self) -> dict:
        if not self._token or time.time() > self._token_expiry - 60:
            data = self.http.get_json(TOKEN_URL, method="POST",
                                      data={"grant_type": "client_credentials"},
                                      auth=(self.client_id, self.client_secret))
            if not data or "access_token" not in data:
                raise RuntimeError("Spotify login failed: check SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET")
            self._token = data["access_token"]
            self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _search(self, query: str, offset: int, page_size: int, market: str) -> list[dict] | None:
        params = {"q": query, "type": "artist", "limit": page_size, "offset": offset, "market": market}
        data = self.http.get_json(f"{API}/search", params=params,
                                  headers=self._auth_header(), delay=0.2)
        if data is None:
            return None
        return ((data.get("artists") or {}).get("items")) or []

    @staticmethod
    def _to_artist(item: dict, default_genre: str = "") -> Artist:
        followers = (item.get("followers") or {}).get("total")
        images = item.get("images") or []  # largest first
        image = next((i["url"] for i in reversed(images) if (i.get("width") or 0) >= 150),
                     images[0]["url"] if images else "")
        return Artist(
            image_url=image,
            name=item["name"],
            genres=item.get("genres") or ([default_genre] if default_genre else []),
            metrics={"spotify_followers": followers} if followers is not None else {},
            spotify_url=(item.get("external_urls") or {}).get("spotify", ""),
            discovered_via="spotify",
        )

    def artist(self, artist_id: str) -> Artist | None:
        data = self.http.get_json(f"{API}/artists/{artist_id}", headers=self._auth_header())
        return self._to_artist(data) if data and data.get("name") else None

    def lookup(self, name: str, market: str = "US") -> Artist | None:
        """Exact-name match, for filling in follower counts."""
        items = self._search(f'artist:"{name}"', 0, 10, market) or []
        for item in items:
            if name_similarity(name, item["name"]) >= 0.95:
                return self._to_artist(item)
        return None

    def discover(self, genre: str, market: str = "US", extra_query: str = "") -> Iterator[Artist]:
        """Artists in a genre, most popular first (Spotify allows paging to 1,000)."""
        query = f'genre:"{genre}"' + (f" {extra_query}" if extra_query else "")
        offset, page_size = 0, 50
        while offset < 1000:
            items = self._search(query, offset, page_size, market)
            if items is None and page_size > 10:
                page_size = 10  # some app tiers cap the search page size
                continue
            if not items:
                return
            for item in items:
                yield self._to_artist(item, genre)
            offset += len(items)
