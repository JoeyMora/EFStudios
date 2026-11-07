"""Command-line interface.

    artist-email-finder lookup "Artist One" "Artist Two, https://linktr.ee/artisttwo"
    artist-email-finder lookup -i artists.csv -o emails.csv
    artist-email-finder discover --genre "drill" --area "Chicago" --limit 30 -o leads.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from itertools import islice
from pathlib import Path

from .config import Settings, load_dotenv
from .finder import Finder, normalize_link
from .level import METRICS
from .net import DEFAULT_USER_AGENT, HttpClient
from .models import Artist, ArtistResult
from .sources.musicbrainz import MusicBrainz
from .sources.spotify import Spotify

OUTPUT_FIELDS = [
    "artist", "email", "confidence", "score", "sources", "found_on", "context",
    "instagram", "tiktok", "instagram_reel_engagement", "tiktok_views", "youtube_views",
    "instagram_followers", "tiktok_followers", "spotify_followers",
    "lastfm_listeners", "youtube_subscribers", "genres", "location", "spotify_url",
    "discovered_via", "pages_checked",
]
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
NAME_COLUMNS = ("name", "artist", "artist_name", "artist name")


# -- input ---------------------------------------------------------------
def _split_links(value: str) -> list[str]:
    return [p for p in re.split(r"[\s,;|]+", value) if p]


def parse_line(line: str) -> Artist | None:
    parts = [p.strip() for p in re.split(r"[,\t|]", line) if p.strip()]
    if not parts or parts[0].startswith("#"):
        return None
    name, links = parts[0], parts[1:]
    if normalize_link(name) and ("://" in name or name.startswith("@")):
        # A bare link or handle: use it as the link and guess a name from it.
        links.insert(0, name)
        name = re.sub(r"[_.\-]+", " ", name.rstrip("/").rsplit("/", 1)[-1].lstrip("@")).strip()
    return Artist(name=name, links=links)


def read_artists(path: Path) -> list[Artist]:
    text = path.read_text(encoding="utf-8-sig")
    first = text.splitlines()[0].lower() if text.strip() else ""
    if path.suffix.lower() == ".csv" and any(c in [h.strip() for h in first.split(",")]
                                             for c in NAME_COLUMNS):
        artists = []
        for row in csv.DictReader(text.splitlines()):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            name = next((row[c] for c in NAME_COLUMNS if row.get(c)), "")
            if not name:
                continue
            links = []
            for key, value in row.items():
                if key in NAME_COLUMNS or not value:
                    continue
                for part in _split_links(value):
                    if normalize_link(part) and ("." in part or part.startswith("@")):
                        links.append(part)
            artists.append(Artist(name=name, links=links))
        return artists
    return [a for a in (parse_line(l) for l in text.splitlines()) if a]


# -- output --------------------------------------------------------------
def rows_for(result: ArtistResult, min_confidence: str) -> list[dict]:
    a = result.artist
    base = {
        "artist": a.name,
        "genres": "; ".join(a.genres),
        "instagram": result.instagram,
        "tiktok": result.tiktok,
        **{k: {**a.metrics, **result.metrics}.get(k, "") for k in METRICS},
        "location": a.location,
        "spotify_url": a.spotify_url,
        "discovered_via": a.discovered_via or "input",
        "pages_checked": " ".join(result.pages_checked),
    }
    keep = [e for e in result.emails
            if CONFIDENCE_ORDER[e.confidence] >= CONFIDENCE_ORDER[min_confidence]]
    if not keep:
        return [{**base, "email": "", "confidence": "", "score": "", "sources": "",
                 "found_on": "", "context": "; ".join(result.notes)}]
    return [{**base, "email": e.email, "confidence": e.confidence, "score": e.score,
             "sources": "; ".join(e.sources), "found_on": " ".join(e.urls),
             "context": e.context} for e in keep]


def already_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["artist"].lower() for row in csv.DictReader(fh) if row.get("artist")}


def run_lookup(artists: list[Artist], finder: Finder, out: Path | None, resume: bool,
               min_confidence: str) -> int:
    seen: set[str] = set()
    unique = []
    for a in artists:
        if a.name.lower() not in seen:
            seen.add(a.name.lower())
            unique.append(a)
    artists = unique

    skip = already_done(out) if (out and resume) else set()
    if skip:
        artists = [a for a in artists if a.name.lower() not in skip]
        print(f"Resuming: skipping {len(skip)} artists already in {out}", file=sys.stderr)

    append = bool(out and resume and out.exists())
    fh = out.open("a" if append else "w", newline="", encoding="utf-8") if out else sys.stdout
    writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
    if not append:
        writer.writeheader()

    print(f"Sources: {', '.join(finder.enabled_sources())}", file=sys.stderr)
    found = 0
    try:
        for i, artist in enumerate(artists, 1):
            print(f"[{i}/{len(artists)}] {artist.name}", end="", file=sys.stderr, flush=True)
            result = finder.lookup(artist)
            rows = rows_for(result, min_confidence)
            writer.writerows(rows)
            fh.flush()
            best = rows[0]
            if best["email"]:
                found += 1
                print(f" -> {best['email']} ({best['confidence']})", file=sys.stderr)
            else:
                print(" -> no email found", file=sys.stderr)
    finally:
        if out:
            fh.close()
    print(f"Done: emails for {found} of {len(artists)} artists"
          + (f", saved to {out}" if out else ""), file=sys.stderr)
    return 0


# -- commands ------------------------------------------------------------
def make_finder(args, settings: Settings, http: HttpClient) -> Finder:
    if not args.no_youtube and not settings.youtube_api_key:
        print("Note: YOUTUBE_API_KEY not set, skipping YouTube.", file=sys.stderr)
    if not args.no_web_search and not settings.has_web_search:
        print("Note: BRAVE_API_KEY / SERPAPI_API_KEY not set, skipping web search.", file=sys.stderr)
    return Finder(settings, http,
                  use_musicbrainz=not args.no_musicbrainz,
                  use_youtube=not args.no_youtube,
                  use_web_search=not args.no_web_search,
                  youtube_search=not args.no_youtube_search,
                  max_pages=args.max_pages)


def cmd_lookup(args, settings: Settings, http: HttpClient) -> int:
    artists: list[Artist] = []
    if args.input:
        artists.extend(read_artists(Path(args.input)))
    for item in args.artists:
        a = parse_line(item)
        if a:
            artists.append(a)
    if not artists:
        print("No artists given. Pass names, or -i FILE.", file=sys.stderr)
        return 2
    return run_lookup(artists, make_finder(args, settings, http),
                      Path(args.output) if args.output else None, args.resume, args.min_confidence)


def cmd_discover(args, settings: Settings, http: HttpClient) -> int:
    source = args.source
    if source == "auto":
        source = "spotify" if (settings.has_spotify and not args.area) else "musicbrainz"
    if source == "spotify" and not settings.has_spotify:
        print("Spotify discovery needs SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.", file=sys.stderr)
        return 2
    if source == "spotify" and args.area:
        print("Note: Spotify can't filter by location; --area is ignored. "
              "Use --source musicbrainz to filter by area.", file=sys.stderr)
    if source == "spotify" and not args.genre:
        print("Spotify discovery needs --genre.", file=sys.stderr)
        return 2

    print(f"Discovering up to {args.limit} artists via {source}...", file=sys.stderr)
    if source == "spotify":
        candidates = Spotify(http, settings.spotify_client_id,
                             settings.spotify_client_secret).discover(args.genre, market=args.market)

        def in_range(a: Artist) -> bool:
            n = a.metrics.get("spotify_followers")
            if n is None:
                return True
            return ((args.min_followers is None or n >= args.min_followers)
                    and (args.max_followers is None or n <= args.max_followers))
        artists = list(islice(filter(in_range, candidates), args.limit))
    else:
        artists = list(islice(MusicBrainz(http).discover(genre=args.genre or "",
                                                         area=args.area or ""), args.limit))
    print(f"Found {len(artists)} artists.", file=sys.stderr)
    if not artists:
        return 1

    if args.no_emails:
        out = Path(args.output).open("w", newline="", encoding="utf-8") if args.output else sys.stdout
        writer = csv.DictWriter(out, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for a in artists:
            writer.writerows(rows_for(ArtistResult(artist=a), "low"))
        if args.output:
            out.close()
        return 0
    return run_lookup(artists, make_finder(args, settings, http),
                      Path(args.output) if args.output else None, args.resume, args.min_confidence)


def cmd_serve(args, settings: Settings, http: HttpClient) -> int:
    import os
    os.environ["FLASK_SKIP_DOTENV"] = "1"   # .env is already loaded by this CLI
    import flask.cli
    from .web import create_app
    flask.cli.show_server_banner = lambda *a, **k: None
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # no per-request lines or dev-server banner
    app = create_app(args.db, settings)
    url = f"http://{args.host}:{args.port}/"
    print(f"Artist Finder is running at {url}", file=sys.stderr)
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="artist-email-finder",
        description="Find public contact emails for music artists.")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    parser.add_argument("--env-file", default=".env", help="file with API keys (default: .env)")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--output", help="CSV file to write (default: print to screen)")
    common.add_argument("--resume", action="store_true",
                        help="append to --output, skipping artists already in it")
    common.add_argument("--min-confidence", choices=["low", "medium", "high"], default="low",
                        help="drop emails below this confidence (default: low = keep all)")
    common.add_argument("--max-pages", type=int, default=12,
                        help="max web pages to fetch per artist (default: 12)")
    common.add_argument("--no-musicbrainz", action="store_true",
                        help="don't look up artist links on MusicBrainz")
    common.add_argument("--no-youtube", action="store_true", help="skip YouTube")
    common.add_argument("--no-youtube-search", action="store_true",
                        help="only use YouTube links you supply (saves API quota)")
    common.add_argument("--no-web-search", action="store_true", help="skip web search")
    common.add_argument("--delay", type=float, default=1.0,
                        help="seconds between requests to the same site (default: 1)")

    p = sub.add_parser("lookup", parents=[common],
                       help="find emails for artists you name",
                       description="Find emails for a list of artists. Each artist can be "
                                   "followed by links: \"Name, https://site.com, @instagram\".")
    p.add_argument("artists", nargs="*", help='artist names, optionally "Name, link, link"')
    p.add_argument("-i", "--input",
                   help="CSV with a 'name' column (other columns read as links), "
                        "or a text file with one artist per line")
    p.set_defaults(func=cmd_lookup)

    d = sub.add_parser("discover", parents=[common],
                       help="find artists by genre/location, then their emails")
    d.add_argument("--genre", help='genre, e.g. "drill", "r&b", "indie pop"')
    d.add_argument("--area", help='city/region/country, e.g. "Atlanta" (MusicBrainz only)')
    d.add_argument("--source", choices=["auto", "spotify", "musicbrainz"], default="auto",
                   help="where to discover artists (auto: Spotify unless --area is given)")
    d.add_argument("--limit", type=int, default=25, help="how many artists (default: 25)")
    d.add_argument("--min-followers", type=int, help="Spotify: minimum followers")
    d.add_argument("--max-followers", type=int, help="Spotify: maximum followers")
    d.add_argument("--market", default="US", help="Spotify market code (default: US)")
    d.add_argument("--no-emails", action="store_true",
                   help="just list the artists; don't search for emails")
    d.set_defaults(func=cmd_discover)

    s = sub.add_parser("serve", help="open the web app for finding and working through leads")
    s.add_argument("--db", default="leads.db", help="database file (default: leads.db)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5005)
    s.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    s.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    load_dotenv(args.env_file)
    settings = Settings.from_env()
    http = HttpClient(user_agent=DEFAULT_USER_AGENT, per_host_delay=getattr(args, "delay", 1.0))
    try:
        return args.func(args, settings, http)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
