"""YouTube Data API v3: business emails in channel and video descriptions.

YouTube's "View email address" button is behind a CAPTCHA and isn't in the API,
so this reads only what artists put in their descriptions. Quota: a channel
search costs 100 units of the free 10,000/day; looking up a known channel
link costs about 2.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from ..extract import context_for, emails_from_text
from ..net import HttpClient
from ..models import Finding
from ..scoring import name_similarity

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"


class YouTube:
    def __init__(self, http: HttpClient, api_key: str, videos_to_scan: int = 5,
                 allow_search: bool = True):
        self.http = http
        self.api_key = api_key
        self.videos_to_scan = videos_to_scan
        self.allow_search = allow_search

    def _get(self, path: str, **params) -> dict | None:
        return self.http.get_json(f"{API}/{path}", params={**params, "key": self.api_key})

    def channel_from_url(self, url: str) -> dict | None:
        parsed = urlparse(url)
        if "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
            return None
        path = parsed.path.rstrip("/")
        params: dict = {"part": "snippet,contentDetails,statistics"}
        if m := re.match(r"^/channel/(UC[\w-]+)", path):
            params["id"] = m.group(1)
        elif m := re.match(r"^/@([\w.\-]+)", path):
            params["forHandle"] = "@" + m.group(1)
        elif m := re.match(r"^/(?:user|c)/([\w.\-]+)", path):
            params["forUsername"] = m.group(1)
        else:
            return None
        data = self._get("channels", **params)
        items = (data or {}).get("items") or []
        if not items and "forUsername" in params:  # /c/ custom URLs are often handles now
            data = self._get("channels", part="snippet,contentDetails,statistics",
                             forHandle="@" + params["forUsername"])
            items = (data or {}).get("items") or []
        return items[0] if items else None

    def search_channel(self, artist_name: str) -> dict | None:
        data = self._get("search", part="snippet", type="channel", q=artist_name, maxResults=5)
        best, best_sim = None, 0.0
        for item in (data or {}).get("items") or []:
            title = item["snippet"]["channelTitle"]
            title = re.sub(r"\s*(-\s*topic|vevo|official)\s*$", "", title, flags=re.IGNORECASE)
            sim = name_similarity(artist_name, title)
            if sim > best_sim:
                best, best_sim = item, sim
        if not best or best_sim < 0.85:
            return None
        data = self._get("channels", part="snippet,contentDetails,statistics",
                         id=best["snippet"]["channelId"])
        items = (data or {}).get("items") or []
        return items[0] if items else None

    def find(self, artist_name: str, links: list[str]) -> tuple[list[Finding], dict]:
        """(findings, info) where info has "url" and "subscribers"."""
        channel = None
        for link in links:
            channel = self.channel_from_url(link)
            if channel:
                break
        if channel is None and self.allow_search:
            channel = self.search_channel(artist_name)
        if channel is None:
            return [], {}

        channel_url = f"https://www.youtube.com/channel/{channel['id']}"
        findings: list[Finding] = []
        description = channel["snippet"].get("description", "")
        for email in emails_from_text(description):
            findings.append(Finding(email, "youtube", channel_url, context_for(email, description)))

        uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if uploads and self.videos_to_scan:
            data = self._get("playlistItems", part="snippet", playlistId=uploads,
                             maxResults=self.videos_to_scan)
            for item in (data or {}).get("items") or []:
                snippet = item["snippet"]
                video_id = (snippet.get("resourceId") or {}).get("videoId", "")
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                text = snippet.get("description", "")
                for email in emails_from_text(text):
                    findings.append(Finding(email, "youtube", video_url, context_for(email, text)))
        stats = channel.get("statistics") or {}
        subs = None if stats.get("hiddenSubscriberCount") else stats.get("subscriberCount")
        return findings, {"url": channel_url, "subscribers": int(subs) if subs else None}
