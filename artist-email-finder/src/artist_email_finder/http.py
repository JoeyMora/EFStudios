"""A polite HTTP client: honours robots.txt, rate-limits per host, caps page size."""

from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

from . import __version__

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    f"EFStudiosArtistEmailFinder/{__version__} (+https://github.com/JoeyMora/EFStudios)"
)
MAX_PAGE_BYTES = 3_000_000


class HttpClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        per_host_delay: float = 1.0,
        respect_robots: bool = True,
    ):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.8",
        })
        self.user_agent = user_agent
        self.timeout = timeout
        self.per_host_delay = per_host_delay
        self.respect_robots = respect_robots
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()

    # -- politeness -------------------------------------------------------
    def _wait_for_host(self, host: str, delay: float | None = None) -> None:
        delay = self.per_host_delay if delay is None else delay
        with self._lock:
            last = self._last_hit.get(host, 0.0)
            wait = last + delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                resp = self.session.get(base + "/robots.txt", timeout=self.timeout)
                if resp.status_code in (401, 403):
                    parser.disallow_all = True
                elif resp.status_code >= 400:
                    parser.allow_all = True
                else:
                    parser.parse(resp.text.splitlines())
                self._robots[base] = parser
            except requests.RequestException:
                self._robots[base] = None  # unreachable robots.txt: treat as allowed
        parser = self._robots[base]
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    # -- fetching ---------------------------------------------------------
    def get_html(self, url: str) -> tuple[str, str] | None:
        """Fetch a web page. Returns (final_url, html) or None."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if not self.allowed(url):
            log.info("robots.txt disallows %s", url)
            return None
        self._wait_for_host(urlparse(url).netloc)
        try:
            resp = self.session.get(url, timeout=self.timeout, stream=True)
            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code >= 400 or ("html" not in ctype and "text" not in ctype):
                log.info("skip %s (status %s, %s)", url, resp.status_code, ctype)
                resp.close()
                return None
            body = resp.raw.read(MAX_PAGE_BYTES, decode_content=True)
            resp.close()
            encoding = resp.encoding or "utf-8"
            return resp.url, body.decode(encoding, errors="replace")
        except requests.RequestException as exc:
            log.info("fetch failed %s: %s", url, exc)
            return None

    def get_json(self, url: str, *, params=None, headers=None, delay: float = 0.0,
                 auth=None, method: str = "GET", data=None) -> dict | None:
        """Call a JSON API (no robots.txt check: these are documented APIs)."""
        self._wait_for_host(urlparse(url).netloc, delay)
        try:
            resp = self.session.request(method, url, params=params, headers=headers,
                                        timeout=self.timeout, auth=auth, data=data)
            if resp.status_code >= 400:
                log.warning("%s %s -> %s: %s", method, url, resp.status_code, resp.text[:300])
                return None
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("API call failed %s: %s", url, exc)
            return None
