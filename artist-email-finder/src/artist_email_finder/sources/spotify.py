"""Spotify Web API: discover artists by genre (needs a free developer app).

Spotify doesn't expose an artist's location, so --area is applied through
MusicBrainz instead. Follower counts let you target smaller, independent artists.
Some fields (followers, genres) may be absent depending on your app's access
level; filters are skipped for artists that don't have them.
"""

from __future__ import annotations

import logging
import time

from ..http import HttpClient
from ..models import Artist

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

    def discover(self, genre: str, limit: int = 50, min_followers: int | None = None,
                 max_followers: int | None = None, market: str = "US",
                 extra_query: str = "") -> list[Artist]:
        query = f'genre:"{genre}"' + (f" {extra_query}" if extra_query else "")
        artists: list[Artist] = []
        offset, page_size = 0, 50
        while len(artists) < limit and offset < 1000:
            params = {"q": query, "type": "artist", "limit": page_size,
                      "offset": offset, "market": market}
            data = self.http.get_json(f"{API}/search", params=params,
                                      headers=self._auth_header(), delay=0.2)
            if data is None and page_size > 10:
                page_size = 10  # some app tiers cap search page size
                continue
            items = ((data or {}).get("artists") or {}).get("items") or []
            if not items:
                break
            for item in items:
                followers = (item.get("followers") or {}).get("total")
                if followers is not None:
                    if min_followers is not None and followers < min_followers:
                        continue
                    if max_followers is not None and followers > max_followers:
                        continue
                artists.append(Artist(
                    name=item["name"],
                    genres=item.get("genres") or [genre],
                    followers=followers,
                    spotify_url=(item.get("external_urls") or {}).get("spotify", ""),
                    discovered_via="spotify",
                ))
                if len(artists) >= limit:
                    break
            offset += len(items)
        return artists
