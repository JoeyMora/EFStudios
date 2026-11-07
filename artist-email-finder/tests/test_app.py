import time

import pytest

from artist_email_finder.config import Settings
from artist_email_finder.jobs import JobRunner
from artist_email_finder.models import Artist, ArtistResult, EmailResult
from artist_email_finder.store import Store
from artist_email_finder.web import bookmarklet, create_app, ingest_capture


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "leads.db")
    s.save_settings({"my_instagram_reel_engagement": "400", "my_tiktok_views": "3000"})
    return s


def test_store_merges_and_levels(store):
    a = Artist(name="Lil Nova", metrics={"instagram_followers": 10_000, "instagram_reel_engagement": 800})
    lead_id = store.save_artist(a)
    store.update_lead(lead_id, status="shortlist", notes="great hooks")
    result = ArtistResult(artist=a, instagram="LilNova", metrics={"instagram_reel_engagement": 960},
                          emails=[EmailResult("booking@lilnova.com", 85, "high", ["website"], ["u"])])
    assert store.save_artist(Artist(name="lil nova"), result) == lead_id
    lead = store.lead(lead_id)
    assert lead["status"] == "shortlist" and lead["notes"] == "great hooks"  # kept
    assert lead["instagram"] == "lilnova"
    assert lead["best_email"] == "booking@lilnova.com"
    assert lead["level_bucket"] == "match" and round(lead["level_ratio"], 1) == 2.4

    store.save_artist(Artist(name="Megastar", metrics={"instagram_reel_engagement": 90_000}))
    # Lots of followers but little engagement: still my level.
    store.save_artist(Artist(name="Inflated", metrics={"instagram_followers": 900_000,
                                                       "instagram_reel_engagement": 300}))
    rows, total = store.leads(level="match")
    assert sorted(r["name"] for r in rows) == ["Inflated", "Lil Nova"]
    rows, _ = store.leads(level="bigger")
    assert [r["name"] for r in rows] == ["Megastar"]
    # Changing my numbers re-buckets everyone.
    store.save_settings({"my_instagram_reel_engagement": "50000"})
    assert store.lead(lead_id)["level_bucket"] == "smaller"


def test_capture_instagram(store):
    text = ("8,200 Followers, 300 Following, 51 Posts - See Instagram photos and videos from "
            "Lil Nova (@lilnova)\nATL artist 🎤 bookings: team [at] novamusic [dot] com\nlinktr.ee/lilnova")
    lead_id, emails, metrics = ingest_capture(
        store, "https://www.instagram.com/lilnova/", "Lil Nova (@lilnova) • Instagram photos and videos", text)
    assert emails == ["team@novamusic.com"]
    assert metrics == {"instagram_followers": 8200}
    lead = store.lead(lead_id)
    assert lead["name"] == "Lil Nova" and lead["instagram"] == "lilnova"
    assert "https://linktr.ee/lilnova" in lead["links"]
    assert lead["level_bucket"] == "unknown"  # followers alone don't set the level
    # Capturing TikTok for the same name merges into the same lead.
    lead2, _, _ = ingest_capture(store, "https://www.tiktok.com/@lilnova.music",
                                 "Lil Nova (@lilnova.music) | TikTok",
                                 "45.1K Followers\nVIDEO_VIEWS: 2.1K | 3.4K | 1.8K | 250K | 2.9K")
    assert lead2 == lead_id
    lead = store.lead(lead_id)
    assert lead["metrics"]["tiktok_followers"] == 45_100
    assert lead["metrics"]["tiktok_views"] == 2900          # median, viral video ignored
    assert lead["level_bucket"] == "match" and round(lead["level_ratio"], 2) == 0.97
    assert "VIDEO_VIEWS" not in str(lead["links"])


def test_web_pages(tmp_path):
    app = create_app(str(tmp_path / "leads.db"), Settings(), start_jobs=False)
    store = app.config["store"]
    lead_id = store.save_artist(Artist(name="Lil Nova", genres=["trap"]))
    c = app.test_client()
    for path in ("/", "/find", "/settings", f"/lead/{lead_id}", "/api/jobs", "/export.csv"):
        assert c.get(path).status_code == 200, path
    assert b"Lil Nova" in c.get("/").data

    assert c.post(f"/api/lead/{lead_id}", json={"status": "contacted", "starred": True}).status_code == 200
    lead = store.lead(lead_id)
    assert lead["status"] == "contacted" and lead["starred"] == 1

    c.post(f"/lead/{lead_id}/paste", data={"text": "3.1K followers. mgmt: nova@mgmt.co", "platform": "instagram"})
    lead = store.lead(lead_id)
    assert lead["best_email"] == "nova@mgmt.co" and lead["metrics"]["instagram_followers"] == 3100

    c.post(f"/lead/{lead_id}", data={"instagram_reel_engagement": "250"})
    c.post("/settings", data={"my_instagram_reel_engagement": "300", "level_low": "0.5", "level_high": "2",
                              "email_subject": "Hi {artist}", "email_body": "Yo {first_name}"})
    assert store.my_metrics() == {"instagram_reel_engagement": 300}
    assert store.lead(lead_id)["level_bucket"] == "match"
    assert "8.1%" in c.get(f"/lead/{lead_id}").data.decode()   # 250 / 3,100 followers
    for view in ("cards", "board", "list"):
        page = c.get(f"/?view={view}&level=").data.decode()
        assert "Lil Nova" in page and "Where your leads sit" in page, view
    assert 'class="mini" draggable="true"' in c.get("/?view=board").data.decode()
    page = c.get(f"/lead/{lead_id}").data.decode()
    assert "mailto:nova@mgmt.co?subject=Hi%20Lil%20Nova&amp;body=Yo%20Lil" in page

    r = c.get("/capture?url=https://www.instagram.com/newartist/&title=New%20Artist%20(%40newartist)"
              "&text=900%20Followers%20contact%3A%20hi%40newartist.com")
    assert r.status_code == 200 and b"hi@newartist.com" in r.data

    csv_text = c.get("/export.csv?level=&status=").data.decode()
    assert "nova@mgmt.co" in csv_text and "hi@newartist.com" in csv_text
    assert bookmarklet("http://127.0.0.1:5005/").startswith("javascript:")


class FakeFinder:
    def __init__(self, metrics_by_name):
        self.metrics_by_name = metrics_by_name

    def lookup(self, artist):
        return ArtistResult(artist=artist, metrics=self.metrics_by_name.get(artist.name, {}),
                            emails=[EmailResult(f"booking@{artist.name.lower()}.com", 80, "high",
                                                ["website"], [])])


def test_discover_job_keeps_only_my_level(store, monkeypatch):
    candidates = [
        Artist("Huge", metrics={"spotify_followers": 9_000_000}, discovered_via="spotify"),
        Artist("BigButQuiet", metrics={"spotify_followers": 80_000}, discovered_via="spotify"),
        Artist("Peer", discovered_via="spotify"),
        Artist("Tiny", discovered_via="spotify"),
        Artist("Peer2", discovered_via="spotify"),
    ]
    finder = FakeFinder({"BigButQuiet": {"instagram_reel_engagement": 350},
                         "Peer": {"tiktok_views": 5000}, "Tiny": {"instagram_reel_engagement": 4},
                         "Peer2": {"instagram_reel_engagement": 900}})
    runner = JobRunner(store, Settings(), finder_factory=lambda http: finder)
    monkeypatch.setattr(runner, "_candidates", lambda params, http: iter(candidates))
    job_id = runner.submit("discover", {"source": "spotify", "genre": "trap", "limit": 3,
                                        "only_my_level": True, "find_emails": True,
                                        "max_spotify_followers": 1_000_000})
    runner.run(job_id)
    job = store.job(job_id)
    assert job["status"] == "done", job["message"]
    assert job["progress"] == 3
    rows, _ = store.leads(level="match")
    assert sorted(r["name"] for r in rows) == ["BigButQuiet", "Peer", "Peer2"]
    # Huge was skipped by the follower cap before the lookup; Tiny after it
    # (engagement too low). Both are remembered so they aren't re-checked.
    assert store.lead(store.find_lead(name="Huge")["id"])["best_email"] == ""
    assert store.has_lead("Tiny")


def test_capture_me_sets_my_level(tmp_path):
    app = create_app(str(tmp_path / "leads.db"), Settings(), start_jobs=False)
    store, c = app.config["store"], app.test_client()
    r = c.get("/capture", query_string={
        "url": "https://www.tiktok.com/@joeymora", "title": "Joey (@joeymora) | TikTok",
        "text": "1.2K Followers\nVIDEO_VIEWS: 800 | 1.1K | 950"})
    assert b"This is me" in r.data
    lead_id = store.find_lead(tiktok="joeymora")["id"]
    c.post("/capture/me", data={"lead_id": lead_id})
    assert store.my_metrics() == {"tiktok_views": 950}
    assert store.lead(lead_id) is None


def test_capture_instagram_reels_tab(store):
    store.save_settings({"my_instagram_reel_views": "5000"})
    lead_id, _, metrics = ingest_capture(
        store, "https://www.instagram.com/lilnova/reels/", "Lil Nova (@lilnova) • Instagram reels",
        "REEL_VIEWS: 4.1K | 6K | 3.9K | 1.2M | 5.5K", "https://cdn.example/pic.jpg")
    assert metrics == {"instagram_reel_views": 5500}
    lead = store.lead(lead_id)
    assert lead["instagram"] == "lilnova" and lead["image_url"] == "https://cdn.example/pic.jpg"
    assert lead["level_bucket"] == "match"


def test_instagram_counts_once_per_platform():
    from artist_email_finder.level import ratio
    mine = {"instagram_reel_views": 1000, "instagram_reel_engagement": 100, "tiktok_views": 1000}
    theirs = {"instagram_reel_views": 4000, "instagram_reel_engagement": 400, "tiktok_views": 1000}
    # Instagram 4x (both IG metrics agree), TikTok 1x -> sqrt(4 * 1) = 2x, not (4*4*1)^(1/3)
    assert round(ratio(theirs, mine), 6) == 2.0


def test_map_points_and_meters(tmp_path):
    import json, re
    app = create_app(str(tmp_path / "leads.db"), Settings(), start_jobs=False)
    store, c = app.config["store"], app.test_client()
    store.save_settings({"my_tiktok_views": "1000"})
    store.save_artist(Artist(name="Peer", metrics={"tiktok_views": 2000}, image_url="https://i.scdn.co/x.jpg"))
    store.save_artist(Artist(name="Star", metrics={"tiktok_views": 500_000}))
    store.save_artist(Artist(name="Mystery"))
    page = c.get("/?level=").data.decode()
    points = json.loads(re.search(r'<script id="points" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert {p["name"]: p["bucket"] for p in points} == {"Peer": "match", "Star": "bigger"}
    assert "1 artist with unknown engagement not shown" in page
    assert 'src="https://i.scdn.co/x.jpg"' in page
    # Peer at 2x sits right of center on the 1/32x..32x meter: (log 2 / log 32 + 1) / 2 = 60%
    assert "left:60.0%" in page
