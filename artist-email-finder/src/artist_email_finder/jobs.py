"""Background jobs for the web app: discover artists, look up emails, re-check leads.

Jobs run one at a time on a single worker thread, which keeps request rates
polite and SQLite writes simple.
"""

from __future__ import annotations

import logging
import queue
import threading
import traceback
from typing import Iterator

from .config import Settings
from .finder import Finder
from .level import ratio
from .models import Artist
from .net import HttpClient
from .sources.lastfm import LastFm
from .sources.musicbrainz import MusicBrainz
from .sources.spotify import Spotify
from .store import Store

log = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


class JobRunner:
    def __init__(self, store: Store, settings: Settings, finder_factory=None):
        self.store = store
        self.settings = settings
        self.finder_factory = finder_factory or (lambda http: Finder(settings, http))
        self.queue: queue.Queue[int] = queue.Queue()
        self.cancelled: set[int] = set()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="jobs")

    def start(self) -> None:
        self.store.fail_stale_jobs()
        self.thread.start()

    def submit(self, kind: str, params: dict) -> int:
        job_id = self.store.create_job(kind, params)
        self.queue.put(job_id)
        return job_id

    def cancel(self, job_id: int) -> None:
        self.cancelled.add(job_id)
        job = self.store.job(job_id)
        if job and job["status"] == "queued":
            self.store.update_job(job_id, status="cancelled", message="cancelled")

    # -- worker ---------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            job_id = self.queue.get()
            self.run(job_id)

    def run(self, job_id: int) -> None:
        job = self.store.job(job_id)
        if not job or job["status"] != "queued":
            return
        self.store.update_job(job_id, status="running", message="starting...")
        http = HttpClient()
        try:
            handler = {"discover": self._discover, "lookup": self._lookup,
                       "recheck": self._recheck}[job["kind"]]
            message = handler(job_id, job["params"], http)
            self.store.update_job(job_id, status="done", message=message)
        except Cancelled:
            self.store.update_job(job_id, status="cancelled", message="cancelled")
        except Exception as exc:  # report, don't kill the worker
            log.error("job %s failed\n%s", job_id, traceback.format_exc())
            self.store.update_job(job_id, status="failed", message=f"{type(exc).__name__}: {exc}")

    def _check(self, job_id: int) -> None:
        if job_id in self.cancelled:
            raise Cancelled()

    # -- job kinds ------------------------------------------------------------
    def _lookup(self, job_id: int, params: dict, http: HttpClient) -> str:
        from .cli import parse_line
        artists = [a for a in (parse_line(l) for l in params.get("lines", [])) if a]
        finder = self.finder_factory(http)
        found = 0
        self.store.update_job(job_id, total=len(artists))
        for i, artist in enumerate(artists, 1):
            self._check(job_id)
            self.store.update_job(job_id, progress=i - 1, message=f"checking {artist.name}")
            result = finder.lookup(artist)
            self.store.save_artist(artist, result)
            found += bool(result.emails)
            self.store.update_job(job_id, progress=i)
        return f"emails for {found} of {len(artists)} artists"

    def _recheck(self, job_id: int, params: dict, http: HttpClient) -> str:
        finder = self.finder_factory(http)
        ids = params.get("lead_ids", [])
        self.store.update_job(job_id, total=len(ids))
        found = 0
        for i, lead_id in enumerate(ids, 1):
            self._check(job_id)
            lead = self.store.lead(lead_id)
            if not lead:
                continue
            self.store.update_job(job_id, progress=i - 1, message=f"checking {lead['name']}")
            links = list(lead["links"])
            if lead["instagram"]:
                links.append(f"https://www.instagram.com/{lead['instagram']}/")
            if lead["tiktok"]:
                links.append(f"https://www.tiktok.com/@{lead['tiktok']}")
            if lead["website"]:
                links.append(lead["website"])
            artist = Artist(name=lead["name"], links=links,
                            genres=[g for g in lead["genres"].split("; ") if g],
                            metrics=lead["metrics"], location=lead["location"],
                            spotify_url=lead["spotify_url"],
                            discovered_via=lead["discovered_via"])
            result = finder.lookup(artist)
            self.store.save_artist(artist, result)
            found += bool(result.emails)
            self.store.update_job(job_id, progress=i)
        return f"emails for {found} of {len(ids)} artists"

    def _candidates(self, params: dict, http: HttpClient) -> Iterator[Artist]:
        source, genre = params.get("source"), (params.get("genre") or "").strip()
        area, seed = (params.get("area") or "").strip(), (params.get("seed") or "").strip()
        s = self.settings
        if source == "spotify":
            if not s.has_spotify:
                raise RuntimeError("Spotify keys are not set (SPOTIFY_CLIENT_ID / SECRET)")
            sp = Spotify(http, s.spotify_client_id, s.spotify_client_secret)
            # With my Spotify followers set, Spotify results already carry the
            # number needed for level filtering.
            return sp.discover(genre, market=params.get("market") or "US")
        if source == "lastfm":
            if not s.lastfm_api_key:
                raise RuntimeError("LASTFM_API_KEY is not set")
            return LastFm(http, s.lastfm_api_key).tag_artists(genre)
        if source == "similar":
            if not s.lastfm_api_key:
                raise RuntimeError("LASTFM_API_KEY is not set")
            return LastFm(http, s.lastfm_api_key).similar(seed)
        if source == "musicbrainz":
            return MusicBrainz(http).discover(genre=genre, area=area)
        raise ValueError(f"unknown source {source!r}")

    def _quick_metrics(self, artist: Artist, http: HttpClient) -> None:
        """Cheap size numbers (one API call) so we can skip far-off artists
        before spending a full email lookup on them."""
        mine = self.store.my_metrics()
        if ratio(artist.metrics, mine) is not None:
            return
        s = self.settings
        if "lastfm_listeners" in mine and s.lastfm_api_key:
            info = LastFm(http, s.lastfm_api_key).info(artist.name)
            if info.get("listeners") is not None:
                artist.metrics["lastfm_listeners"] = info["listeners"]
                if not artist.genres:
                    artist.genres = info["tags"][:5]
        if "spotify_followers" in mine and s.has_spotify and ratio(artist.metrics, mine) is None:
            sp = Spotify(http, s.spotify_client_id, s.spotify_client_secret).lookup(artist.name)
            if sp:
                artist.metrics.update(sp.metrics)
                artist.spotify_url = artist.spotify_url or sp.spotify_url

    def _discover(self, job_id: int, params: dict, http: HttpClient) -> str:
        limit = int(params.get("limit") or 25)
        only_level = bool(params.get("only_my_level"))
        find_emails = bool(params.get("find_emails", True))
        max_scan = int(params.get("max_scan") or max(200, limit * 20))
        finder = self.finder_factory(http) if find_emails else None

        kept = scanned = skipped_known = off_level = 0
        self.store.update_job(job_id, total=limit, message="searching...")
        for artist in self._candidates(params, http):
            self._check(job_id)
            if scanned >= max_scan:
                break
            if self.store.has_lead(artist.name):
                skipped_known += 1
                continue
            scanned += 1
            self.store.update_job(job_id, message=f"checking {artist.name} "
                                                  f"({scanned} looked at, {off_level} off your level)")
            if only_level:
                self._quick_metrics(artist, http)
                _, b = self.store.level_of(artist.metrics)
                if b in ("smaller", "bigger"):
                    off_level += 1
                    self.store.save_artist(artist)  # remembered, so it isn't re-checked
                    continue
            result = finder.lookup(artist) if finder else None
            self.store.save_artist(artist, result)
            if only_level and result:
                _, b = self.store.level_of({**artist.metrics, **result.metrics})
                if b in ("smaller", "bigger"):
                    off_level += 1
                    continue
            kept += 1
            self.store.update_job(job_id, progress=kept)
            if kept >= limit:
                break
        parts = [f"added {kept} artists"]
        if only_level:
            parts.append(f"{off_level} skipped as off your level")
        if skipped_known:
            parts.append(f"{skipped_known} already in your list")
        return ", ".join(parts)
