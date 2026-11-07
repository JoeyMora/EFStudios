"""Crawl an artist's own pages: websites, link-in-bio pages, and social profiles."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..extract import context_for, emails_from_html
from ..http import HttpClient
from ..models import Finding

log = logging.getLogger(__name__)

LINK_PAGE_HOSTS = {
    "linktr.ee", "beacons.ai", "lnk.bio", "hoo.be", "solo.to", "bio.link",
    "komi.io", "stan.store", "carrd.co", "linkin.bio", "allmylinks.com",
    "msha.ke", "campsite.bio", "tap.bio", "snipfeed.co", "linkfly.to",
    "fanlink.to", "fanlink.tv", "ffm.to", "ffm.bio", "hypeddit.com",
    "toneden.io", "song.link", "odesli.co", "lnk.to", "push.fm", "smarturl.it",
    "feature.fm", "koji.to", "withkoji.com", "direct.me", "link.me",
}

SOCIAL_HOSTS = {
    "instagram.com": "instagram", "tiktok.com": "tiktok",
    "soundcloud.com": "soundcloud", "twitter.com": "x", "x.com": "x",
    "facebook.com": "facebook", "bandcamp.com": "bandcamp",
    "audiomack.com": "audiomack", "threads.net": "threads",
    "youtube.com": "youtube", "youtu.be": "youtube",
}

# Big sites that are never the artist's own website.
NOT_ARTIST_SITES = set(SOCIAL_HOSTS) | {
    "spotify.com", "open.spotify.com", "music.apple.com", "apple.com",
    "itunes.apple.com", "music.amazon.com", "amazon.com", "deezer.com",
    "tidal.com", "pandora.com", "genius.com", "wikipedia.org", "discogs.com",
    "allmusic.com", "last.fm", "musicbrainz.org", "google.com", "bit.ly",
    "patreon.com", "twitch.tv", "snapchat.com", "pinterest.com", "reddit.com",
    "linkedin.com", "shazam.com", "songkick.com", "bandsintown.com",
    "ticketmaster.com", "eventbrite.com", "venmo.com", "paypal.me",
    "cash.app", "discord.gg", "discord.com", "t.me", "wa.me", "imdb.com",
} | LINK_PAGE_HOSTS

CONTACT_HINT = re.compile(
    r"contact|booking|book|management|mgmt|about|press|epk|inquir|enquir|business",
    re.IGNORECASE,
)


def host_of(url: str) -> str:
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    host = host.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domains) -> str | None:
    for d in domains:
        if host == d or host.endswith("." + d):
            return d
    return None


def classify(url: str) -> str:
    """'link_page', 'social_bio', or 'website'."""
    host = host_of(url)
    if _matches(host, LINK_PAGE_HOSTS):
        return "link_page"
    if _matches(host, SOCIAL_HOSTS):
        return "social_bio"
    return "website"


def social_platform(url: str) -> str | None:
    match = _matches(host_of(url), SOCIAL_HOSTS)
    return SOCIAL_HOSTS[match] if match else None


def is_artist_site_candidate(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    return _matches(host_of(url), NOT_ARTIST_SITES) is None


def _links(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        if href.startswith(("http://", "https://")):
            out.append((href.split("#", 1)[0], a.get_text(" ", strip=True)))
    return out


class PageCrawler:
    def __init__(self, http: HttpClient, max_pages: int = 12):
        self.http = http
        self.max_pages = max_pages

    def crawl(self, start_urls: list[str]) -> tuple[list[Finding], list[str], list[str]]:
        """Crawl artist-owned URLs.

        Returns (findings, pages_checked, discovered_links) where discovered_links
        are social/YouTube links found along the way (useful to other sources).
        """
        findings: list[Finding] = []
        checked: list[str] = []
        discovered: list[str] = []
        queue: list[tuple[str, int]] = [(u, 0) for u in start_urls]
        seen: set[str] = set()
        contact_pages: dict[str, int] = {}

        while queue and len(checked) < self.max_pages:
            url, depth = queue.pop(0)
            key = url.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)

            got = self.http.get_html(url)
            checked.append(url)
            if not got:
                continue
            final_url, html = got
            kind = classify(final_url)

            text = BeautifulSoup(html, "html.parser").get_text(" ")
            for email in emails_from_html(html):
                findings.append(Finding(email=email, source=kind, url=final_url,
                                        context=context_for(email, text)))

            # Depth 0: what we were given. Depth 1: a site linked from a link page,
            # or a contact page. Depth 2: a contact page on a linked site.
            if depth >= 2:
                continue
            for href, label in _links(html, final_url):
                if social_platform(href) and href not in discovered:
                    discovered.append(href)
                if depth > 0 and kind != "website":
                    continue
                if kind == "link_page" and is_artist_site_candidate(href):
                    # Artist's own site linked from their link-in-bio page.
                    queue.append((href, depth + 1))
                elif kind == "link_page" and classify(href) == "link_page" \
                        and host_of(href) != host_of(final_url):
                    queue.append((href, depth + 1))
                elif kind == "website" and host_of(href) == host_of(final_url) \
                        and contact_pages.get(host_of(href), 0) < 4 \
                        and (CONTACT_HINT.search(urlparse(href).path) or CONTACT_HINT.search(label)):
                    # Contact / booking / about pages on the same site go to the front.
                    contact_pages[host_of(href)] = contact_pages.get(host_of(href), 0) + 1
                    queue.insert(0, (href, depth + 1))
        return findings, checked, discovered
