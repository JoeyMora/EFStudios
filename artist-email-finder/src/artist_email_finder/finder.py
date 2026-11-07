"""Runs every enabled source for one artist and ranks what it finds."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from .config import Settings
from .http import HttpClient
from .models import Artist, ArtistResult, Finding
from .scoring import name_tokens, rank
from .sources.musicbrainz import MusicBrainz
from .sources.pages import PageCrawler, classify, host_of, is_artist_site_candidate, social_platform
from .sources.websearch import WebSearch
from .sources.youtube import YouTube

log = logging.getLogger(__name__)


def normalize_link(link: str) -> str | None:
    link = link.strip()
    if not link:
        return None
    if re.fullmatch(r"@[\w.]{1,30}", link):
        return f"https://www.instagram.com/{link[1:]}/"
    if not link.startswith(("http://", "https://")):
        if "." not in link or " " in link:
            return None
        link = "https://" + link
    return link


def _url_mentions_artist(url: str, artist_name: str) -> bool:
    folded = re.sub(r"[^a-z0-9]", "", (host_of(url) + urlparse(url).path).lower())
    return any(t in folded for t in name_tokens(artist_name))


class Finder:
    def __init__(self, settings: Settings, http: HttpClient | None = None, *,
                 use_musicbrainz: bool = True, use_youtube: bool = True,
                 use_web_search: bool = True, youtube_search: bool = True,
                 max_pages: int = 12):
        self.http = http or HttpClient()
        self.crawler = PageCrawler(self.http, max_pages=max_pages)
        self.musicbrainz = MusicBrainz(self.http) if use_musicbrainz else None
        self.youtube = (YouTube(self.http, settings.youtube_api_key, allow_search=youtube_search)
                        if use_youtube and settings.youtube_api_key else None)
        self.web_search = (WebSearch(self.http, settings.brave_api_key, settings.serpapi_api_key)
                           if use_web_search and settings.has_web_search else None)

    def enabled_sources(self) -> list[str]:
        names = ["pages"]
        if self.musicbrainz:
            names.append("musicbrainz")
        if self.youtube:
            names.append("youtube")
        if self.web_search:
            names.append("web_search")
        return names

    def lookup(self, artist: Artist) -> ArtistResult:
        result = ArtistResult(artist=artist)
        findings: list[Finding] = []
        links: list[str] = []

        def add_links(new):
            for link in new:
                link = normalize_link(link)
                if link and link not in links:
                    links.append(link)

        add_links(artist.links)

        if self.musicbrainz and artist.discovered_via != "musicbrainz":
            mb_links = self.musicbrainz.find_links(artist.name)
            if mb_links:
                result.notes.append(f"musicbrainz: {len(mb_links)} links")
            add_links(mb_links)

        if self.web_search:
            ws_findings, ws_urls = self.web_search.find(artist.name)
            findings.extend(ws_findings)
            # Only follow results that look like the artist's own pages.
            add_links(u for u in ws_urls
                      if _url_mentions_artist(u, artist.name)
                      and (classify(u) != "website" or is_artist_site_candidate(u)))

        crawl = [l for l in links
                 if social_platform(l) != "youtube"
                 and (classify(l) != "website" or is_artist_site_candidate(l))]
        page_findings, checked, discovered = self.crawler.crawl(crawl)
        findings.extend(page_findings)
        result.pages_checked.extend(checked)

        if self.youtube:
            yt_findings, yt_urls = self.youtube.find(artist.name, links + discovered)
            findings.extend(yt_findings)
            result.pages_checked.extend(yt_urls)

        site_domains = {host_of(l) for l in crawl if classify(l) == "website"}
        result.emails = rank(findings, artist.name, site_domains)
        if not links and not self.web_search and not self.youtube:
            result.notes.append("no links to check: add a website/social link, "
                                "or set YOUTUBE_API_KEY / BRAVE_API_KEY")
        return result
