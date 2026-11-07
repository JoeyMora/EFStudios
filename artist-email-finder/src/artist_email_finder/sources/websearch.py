"""Web search (Brave Search API or SerpAPI) for '<artist> booking email' etc."""

from __future__ import annotations

import logging
import re

from ..counts import followers_in
from ..extract import context_for, emails_from_text
from ..net import HttpClient
from ..models import Finding
from ..scoring import name_similarity
from .instagram import instagram_handle, tiktok_handle

log = logging.getLogger(__name__)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
SERPAPI_URL = "https://serpapi.com/search.json"


class WebSearch:
    def __init__(self, http: HttpClient, brave_api_key: str = "", serpapi_api_key: str = ""):
        self.http = http
        self.brave_api_key = brave_api_key
        self.serpapi_api_key = serpapi_api_key

    def _brave(self, query: str) -> list[dict]:
        data = self.http.get_json(
            BRAVE_URL, params={"q": query, "count": 10, "extra_snippets": "true"},
            headers={"X-Subscription-Token": self.brave_api_key, "Accept": "application/json"},
            delay=1.1,  # free tier: 1 query/second
        )
        results = []
        for r in ((data or {}).get("web") or {}).get("results") or []:
            snippet = " ".join([r.get("description", "")] + (r.get("extra_snippets") or []))
            results.append({"url": r.get("url", ""), "title": r.get("title", ""), "snippet": snippet})
        return results

    def _serpapi(self, query: str) -> list[dict]:
        data = self.http.get_json(SERPAPI_URL, params={
            "engine": "google", "q": query, "num": 10, "api_key": self.serpapi_api_key,
        })
        return [{"url": r.get("link", ""), "title": r.get("title", ""), "snippet": r.get("snippet", "")}
                for r in (data or {}).get("organic_results") or []]

    def search(self, query: str) -> list[dict]:
        if self.brave_api_key:
            return self._brave(query)
        if self.serpapi_api_key:
            return self._serpapi(query)
        return []

    def find(self, artist_name: str) -> tuple[list[Finding], list[str]]:
        """Returns (findings from snippets, result URLs worth crawling)."""
        query = f'"{artist_name}" (booking OR management OR contact OR inquiries) email'
        findings: list[Finding] = []
        urls: list[str] = []
        for r in self.search(query):
            text = f"{r['title']} {r['snippet']}"
            for email in emails_from_text(text):
                findings.append(Finding(email, "web_search", r["url"],
                                        context_for(email, text), on_artist_page=False))
            if r["url"]:
                urls.append(r["url"])
        return findings, urls

    def profile(self, platform: str, handle: str = "", name: str = "") -> dict | None:
        """Find an Instagram/TikTok profile in search results and read its snippet.

        Search engines index public profile pages, and their snippets usually
        include the follower count and part of the bio. With a handle this reads
        that profile. With only a name it looks for a profile titled with it.
        Returns {"handle", "url", "text", "followers"} or None.
        """
        site = {"instagram": "instagram.com", "tiktok": "tiktok.com"}[platform]
        parse = instagram_handle if platform == "instagram" else tiktok_handle
        query = f"site:{site} @{handle}" if handle else f'site:{site} "{name}"'
        for r in self.search(query):
            found = parse(r["url"])
            if not found:
                continue
            if handle:
                if found != handle.lower():
                    continue
            else:
                # Titles look like "Name (@handle) • Instagram photos and videos"
                # or "Name (@handle) | TikTok".
                title_name = re.split(r"\(@|[|•]", r["title"])[0].strip()
                if max(name_similarity(name, title_name), name_similarity(name, found)) < 0.85:
                    continue
            text = f"{r['title']} {r['snippet']}"
            return {"handle": found, "url": r["url"], "text": text,
                    "followers": followers_in(text)}
        return None
