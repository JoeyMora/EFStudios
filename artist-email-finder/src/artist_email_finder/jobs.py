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

from . import ai
from .ai import SearchPlan
from .config import Settings
from .genres import merge as merge_genres
from .smart import genre_profile, interleave, match
from .finder import Finder
from .models import Artist
from .net import HttpClient
from .sources.lastfm import LastFm
from .sources.musicbrainz import MusicBrainz, artist_from_profile
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
                       "recheck": self._recheck, "search": self._search,
                       "recommend": self._recommend}[job["kind"]]
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
            artist = lead_to_artist(lead)
            result = finder.lookup(artist)
            self.store.save_artist(artist, result)
            found += bool(result.emails)
            self.store.update_job(job_id, progress=i)
        return f"emails for {found} of {len(ids)} artists"

    # -- prompt search ----------------------------------------------------------
    def _level_ok(self, metrics: dict, wanted: str) -> bool:
        _, b = self.store.level_of(metrics)
        if b == "unknown" or wanted == "any":
            return True
        return b == ("match" if wanted == "my_level" else wanted)

    def _search(self, job_id: int, params: dict, http: HttpClient) -> str:
        search = self.store.search(params["search_id"])
        plan = SearchPlan.from_dict(search["plan"])
        s = self.settings
        mb = MusicBrainz(http)
        finder = self.finder_factory(http)
        limit = plan.limit or 20

        sources = []
        if plan.suggested_artists:
            sources.append(Artist(name=n, discovered_via="ai suggestion") for n in plan.suggested_artists)
        query = mb.build_query(plan.genres, plan.tags, plan.gender, plan.artist_type,
                               plan.countries, plan.identity_tags, plan.areas)
        sources.append(mb.search(query, max_results=500))
        if not (plan.gender != "any" or plan.countries) and plan.genres:
            # A looser MusicBrainz query by genre alone, when no identity filters apply.
            sources.append(mb.search(mb.build_query(plan.genres, areas=plan.areas), max_results=300))
        style_tags = (plan.genres + plan.tags + plan.identity_tags)[:4]
        if s.lastfm_api_key:
            lf = LastFm(http, s.lastfm_api_key)
            sources += [lf.tag_artists(t, max_pages=8) for t in style_tags]
        if s.has_spotify and plan.genres:
            sp = Spotify(http, s.spotify_client_id, s.spotify_client_secret)
            sources += [sp.discover(g) for g in plan.genres[:2]]

        kept = scanned = 0
        seen: set[str] = set()
        self.store.update_job(job_id, total=limit, message="searching...")
        for cand in interleave(*sources):
            self._check(job_id)
            key = cand.name.lower()
            if key in seen:
                continue
            seen.add(key)
            scanned += 1
            if scanned > max(150, limit * 15):
                break
            self.store.update_job(job_id, message=f"{kept} found · checking {cand.name}")

            existing = self.store.find_lead(name=cand.name)
            if existing:
                lead = self.store.lead(existing["id"])
                ok, score, reasons = match(lead_to_artist(lead), plan)
                if ok and self._level_ok(lead["metrics"], plan.level):
                    self.store.add_search_result(search["id"], lead["id"], score, " · ".join(reasons))
                    kept += 1
                    self.store.update_job(job_id, progress=kept)
                continue

            if not cand.profiled:  # get gender / type / country / genres before deciding
                prof = mb.profile(cand.name)
                if prof:
                    base = artist_from_profile(prof, cand.discovered_via)
                    base.metrics, base.spotify_url, base.image_url = cand.metrics, cand.spotify_url, cand.image_url
                    base.genres = merge_genres(base.genres, cand.genres)
                    cand = base
            trusted = cand.discovered_via in ("lastfm", "ai suggestion")
            ok, score, reasons = match(cand, plan, trusted_style=trusted)
            if not ok:
                continue
            result = finder.lookup(cand)
            metrics = {**cand.metrics, **result.metrics}
            lead_id = self.store.save_artist(cand, result)
            if not self._level_ok(metrics, plan.level):
                continue
            ok, score, reasons = match(cand, plan, trusted_style=trusted)  # genres may be filled in now
            self.store.add_search_result(search["id"], lead_id, score + (.3 if result.emails else 0),
                                         " · ".join(reasons))
            kept += 1
            self.store.update_job(job_id, progress=kept)
            if kept >= limit:
                break
        return f"found {kept} artist{'' if kept == 1 else 's'}" + ("" if kept else " — try a broader prompt")

    # -- recommendations --------------------------------------------------------
    def _recommend(self, job_id: int, params: dict, http: HttpClient) -> str:
        limit = int(params.get("limit") or 12)
        liked, passed = self.store.taste()
        if not liked:
            liked, _ = self.store.leads(level="match", sort="email", limit=10)
        if not liked:
            return "save or star a few artists first, then I can suggest more like them"
        s = self.settings
        mb = MusicBrainz(http)
        finder = self.finder_factory(http)
        liked_genres = genre_profile(liked, passed)
        passed_genres = {g for p in passed for g in p.get("genre_list", [])[:2]} - set(liked_genres[:3])

        cands: dict[str, dict] = {}

        def add(name: str, weight: float, reason: str):
            c = cands.setdefault(name.lower(), {"name": name, "score": 0.0, "reasons": []})
            c["score"] += weight
            if reason not in c["reasons"]:
                c["reasons"].append(reason)

        self.store.update_job(job_id, message="reading your taste...")
        if s.lastfm_api_key:
            lf = LastFm(http, s.lastfm_api_key)
            for seed in liked[:10]:
                self._check(job_id)
                for name, m in lf.similar_scored(seed["name"]):
                    add(name, m, f"Similar to {seed['name']}")
            for g in liked_genres[:2]:
                for i, a in enumerate(lf.tag_artists(g, max_pages=4)):
                    add(a.name, .25, f"Plays {g}, like artists you saved")
                    if i > 120:
                        break
        for sug in ai.suggest_artists(liked, passed):
            add(sug["name"], .8, sug["why"])
        for g in liked_genres[:2]:
            self._check(job_id)
            for i, a in enumerate(mb.search(mb.build_query([g]), max_results=100)):
                add(a.name, .2, f"Plays {g}, like artists you saved")
                if i >= 15:
                    break

        ranked = sorted((c for k, c in cands.items() if not self.store.has_lead(c["name"])),
                        key=lambda c: -c["score"])
        kept = 0
        self.store.update_job(job_id, total=limit)
        for c in ranked[: limit * 5]:
            self._check(job_id)
            self.store.update_job(job_id, message=f"{kept} picks · checking {c['name']}")
            prof = mb.profile(c["name"])
            artist = artist_from_profile(prof, "for you") if prof else Artist(name=c["name"], discovered_via="for you")
            result = finder.lookup(artist)
            score = c["score"]
            genres = set(artist.genres)
            score += .3 * len(genres & set(liked_genres[:5]))
            if genres and genres <= passed_genres:
                score -= 1
            lead_id = self.store.save_artist(artist, result)
            if not self._level_ok({**artist.metrics, **result.metrics}, "my_level") or score <= 0:
                continue
            self.store.set_pick(lead_id, " · ".join(c["reasons"][:2]), round(score, 2))
            kept += 1
            self.store.update_job(job_id, progress=kept)
            if kept >= limit:
                break
        return f"{kept} new pick{'' if kept == 1 else 's'} for you"

    def _candidates(self, params: dict, http: HttpClient) -> Iterator[Artist]:
        source, genre = params.get("source"), (params.get("genre") or "").strip()
        area, seed = (params.get("area") or "").strip(), (params.get("seed") or "").strip()
        s = self.settings
        if source == "spotify":
            if not s.has_spotify:
                raise RuntimeError("Spotify keys are not set (SPOTIFY_CLIENT_ID / SECRET)")
            sp = Spotify(http, s.spotify_client_id, s.spotify_client_secret)
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

    def _too_big(self, artist: Artist, cap: int | None, http: HttpClient) -> bool:
        """Optional speed-up: skip obvious superstars before the full lookup.
        Uses the Spotify follower count when discovery already has it (free)."""
        if not cap:
            return False
        n = artist.metrics.get("spotify_followers")
        return n is not None and n > cap

    def _discover(self, job_id: int, params: dict, http: HttpClient) -> str:
        limit = int(params.get("limit") or 25)
        only_level = bool(params.get("only_my_level"))
        find_emails = bool(params.get("find_emails", True))
        max_scan = int(params.get("max_scan") or max(200, limit * 20))
        finder = self.finder_factory(http) if (find_emails or only_level) else None
        cap = int(params["max_spotify_followers"]) if params.get("max_spotify_followers") else None
        too_big = 0

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
            if self._too_big(artist, cap, http):
                too_big += 1
                self.store.save_artist(artist)  # remembered, so it isn't re-checked
                continue
            result = finder.lookup(artist) if finder else None
            self.store.save_artist(artist, result)
            if only_level and result:
                # Engagement comes from the lookup (Instagram posts, YouTube views).
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
            parts.append(f"{off_level} off your engagement level")
        if too_big:
            parts.append(f"{too_big} over the Spotify follower cap")
        if skipped_known:
            parts.append(f"{skipped_known} already in your list")
        return ", ".join(parts)


def lead_to_artist(lead: dict) -> Artist:
    """Rebuild an Artist from a stored lead, for re-checks and matching."""
    links = list(lead["links"])
    if lead.get("instagram"):
        links.append(f"https://www.instagram.com/{lead['instagram']}/")
    if lead.get("tiktok"):
        links.append(f"https://www.tiktok.com/@{lead['tiktok']}")
    if lead.get("website"):
        links.append(lead["website"])
    return Artist(name=lead["name"], links=links, genres=lead.get("genre_list", []),
                  metrics=lead["metrics"], location=lead.get("location", ""),
                  spotify_url=lead.get("spotify_url", ""), discovered_via=lead.get("discovered_via", ""),
                  image_url=lead.get("image_url", ""), gender=lead.get("gender", ""),
                  artist_type=lead.get("artist_type", ""), country=lead.get("country", ""),
                  tags=lead.get("tag_list", []))
