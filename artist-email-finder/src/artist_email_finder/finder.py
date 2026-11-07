"""Runs every enabled source for one artist and ranks what it finds."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from . import ai
from .config import Settings
from .genres import merge as merge_genres, split_tags
from .extract import context_for, emails_from_text
from .models import Artist, ArtistResult, Finding
from .net import HttpClient
from .scoring import name_tokens, rank
from .sources.instagram import InstagramGraph, instagram_handle, tiktok_handle
from .sources.lastfm import LastFm
from .sources.musicbrainz import MusicBrainz
from .sources.pages import PageCrawler, classify, host_of, is_artist_site_candidate, social_platform
from .sources.spotify import Spotify
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


def _crawlable(link: str) -> bool:
    """Pages we fetch directly. Instagram/TikTok/YouTube are handled by their
    own steps (official APIs and search snippets), not by fetching the page."""
    if social_platform(link) in {"youtube", "instagram", "tiktok"}:
        return False
    return classify(link) != "website" or is_artist_site_candidate(link)


class Finder:
    def __init__(self, settings: Settings, http: HttpClient | None = None, *,
                 use_musicbrainz: bool = True, use_youtube: bool = True,
                 use_web_search: bool = True, youtube_search: bool = True,
                 use_instagram: bool = True, social_search: bool = True,
                 max_pages: int = 12):
        self.http = http or HttpClient()
        self.crawler = PageCrawler(self.http, max_pages=max_pages)
        self.musicbrainz = MusicBrainz(self.http) if use_musicbrainz else None
        self.youtube = (YouTube(self.http, settings.youtube_api_key, allow_search=youtube_search)
                        if use_youtube and settings.youtube_api_key else None)
        self.web_search = (WebSearch(self.http, settings.brave_api_key, settings.serpapi_api_key)
                           if use_web_search and settings.has_web_search else None)
        self.social_search = bool(self.web_search and social_search)
        self.instagram = (InstagramGraph(self.http, settings.meta_access_token,
                                         settings.ig_user_id, settings.graph_api_version)
                          if use_instagram and settings.has_instagram else None)
        self.spotify = (Spotify(self.http, settings.spotify_client_id, settings.spotify_client_secret)
                        if settings.has_spotify else None)
        self.lastfm = LastFm(self.http, settings.lastfm_api_key) if settings.lastfm_api_key else None

    def enabled_sources(self) -> list[str]:
        names = ["pages"]
        for attr in ("musicbrainz", "instagram", "youtube", "web_search", "spotify", "lastfm"):
            if getattr(self, attr):
                names.append(attr)
        if self.social_search:
            names.append("social_search")
        return names

    def lookup(self, artist: Artist) -> ArtistResult:
        result = ArtistResult(artist=artist, metrics=dict(artist.metrics))
        findings: list[Finding] = []
        links: list[str] = []
        crawled: set[str] = set()

        def add_links(new):
            for link in new:
                link = normalize_link(link)
                if link and link not in links:
                    links.append(link)

        def crawl_new():
            todo = [l for l in links if _crawlable(l) and l not in crawled]
            if not todo:
                return
            crawled.update(todo)
            page_findings, checked, discovered = self.crawler.crawl(todo)
            findings.extend(page_findings)
            result.pages_checked.extend(checked)
            add_links(discovered)

        add_links(artist.links)
        if artist.spotify_url:
            add_links([artist.spotify_url])

        texts: list[str] = []  # bios and snippets, for genre inference

        # 1. Public profile: links, genres, gender, solo/group, country (MusicBrainz).
        if self.musicbrainz and not artist.profiled and artist.discovered_via != "musicbrainz":
            prof = self.musicbrainz.profile(artist.name)
            if prof:
                result.notes.append(f"musicbrainz: {len(prof['urls'])} links")
                add_links(prof["urls"])
                artist.genres = merge_genres(artist.genres, prof["genres"])
                artist.tags = merge_genres(artist.tags, prof["tags"], limit=10)
                for key in ("gender", "artist_type", "country", "location"):
                    if prof.get(key) and not getattr(artist, key):
                        setattr(artist, key, prof[key])
                texts.append(prof.get("disambiguation", ""))

        # 2. General web search.
        if self.web_search:
            ws_findings, ws_urls = self.web_search.find(artist.name)
            findings.extend(ws_findings)
            texts.extend(getattr(self.web_search, "last_texts", []))
            add_links(u for u in ws_urls
                      if _url_mentions_artist(u, artist.name)
                      and (classify(u) != "website" or is_artist_site_candidate(u)
                           or social_platform(u) in {"instagram", "tiktok"}))

        # 3. Websites and link-in-bio pages (also reveals social handles).
        crawl_new()

        def keep_ig(info: dict) -> None:
            for key in ("instagram_followers", "instagram_reel_engagement"):
                if info.get(key) is not None:
                    result.metrics[key] = info[key]
            if info.get("image_url"):
                result.image_url = info["image_url"]
            texts.append(info.get("bio", ""))

        # 4. Instagram and TikTok.
        ig = next((h for h in map(instagram_handle, links) if h), "")
        tt = next((h for h in map(tiktok_handle, links) if h), "")

        ig_tried = ig
        if self.instagram and ig:
            ig_findings, info = self.instagram.find(ig)
            findings.extend(ig_findings)
            if info:
                keep_ig(info)
                if info.get("website"):
                    add_links([info["website"]])
                    crawl_new()  # their bio link: usually a Linktree with the email
            else:
                result.notes.append("instagram: not a business/creator account (or not found)")

        if self.social_search:
            for platform in ("instagram", "tiktok"):
                handle = ig if platform == "instagram" else tt
                if platform == "instagram" and "instagram_followers" in result.metrics:
                    continue  # already have it from the official API
                prof = self.web_search.profile(platform, handle=handle, name=artist.name)
                if not prof:
                    continue
                if platform == "instagram":
                    ig = ig or prof["handle"]
                else:
                    tt = tt or prof["handle"]
                if prof["followers"] is not None:
                    result.metrics[f"{platform}_followers"] = prof["followers"]
                texts.append(prof["text"])
                for email in emails_from_text(prof["text"]):
                    findings.append(Finding(email, "social_bio", prof["url"],
                                            context_for(email, prof["text"])))

        # Got a handle from search only after the official-API step? Try it now.
        if self.instagram and ig and ig != ig_tried:
            ig_findings, info = self.instagram.find(ig)
            findings.extend(ig_findings)
            keep_ig(info)
            if info.get("website"):
                add_links([info["website"]])
                crawl_new()

        result.instagram, result.tiktok = ig, tt
        if ig:
            add_links([f"https://www.instagram.com/{ig}/"])
        if tt:
            add_links([f"https://www.tiktok.com/@{tt}"])

        # 5. YouTube descriptions.
        if self.youtube:
            yt_findings, info = self.youtube.find(artist.name, links)
            findings.extend(yt_findings)
            if info:
                add_links([info["url"]])
                result.pages_checked.append(info["url"])
                if info.get("subscribers") is not None:
                    result.metrics["youtube_subscribers"] = info["subscribers"]
                if info.get("views") is not None:
                    result.metrics["youtube_views"] = info["views"]

        # 6. Audience size (context only; level uses engagement).
        if self.spotify and "spotify_followers" not in result.metrics:
            sp = self.spotify.lookup(artist.name)
            if sp:
                result.metrics.update(sp.metrics)
                if not artist.spotify_url:
                    artist.spotify_url = sp.spotify_url
                if not artist.image_url:
                    artist.image_url = sp.image_url
                if not artist.genres:
                    artist.genres = sp.genres
        if self.lastfm and "lastfm_listeners" not in result.metrics:
            info = self.lastfm.info(artist.name)
            if info.get("listeners") is not None:
                result.metrics["lastfm_listeners"] = info["listeners"]

        # 7. Genre: databases first, listener tags next, AI reading their bio last.
        if self.lastfm and len(artist.genres) < 2:
            g, other = split_tags(self.lastfm.top_tags(artist.name))
            artist.genres = merge_genres(artist.genres, g)
            artist.tags = merge_genres(artist.tags, other, limit=10)
        if not artist.genres:
            artist.genres = ai.infer_genres(artist.name, texts)
            if artist.genres:
                result.notes.append("genre inferred from bio")

        site_domains = {host_of(l) for l in crawled if classify(l) == "website"}
        result.website = (next((l for l in links if classify(l) == "website"
                                and is_artist_site_candidate(l)), "")
                          or next((l for l in links if classify(l) == "link_page"), ""))
        result.links = links
        result.emails = rank(findings, artist.name, site_domains)
        if not links and not self.web_search and not self.youtube:
            result.notes.append("no links to check: add a website/social link, "
                                "or set BRAVE_API_KEY / YOUTUBE_API_KEY")
        return result
