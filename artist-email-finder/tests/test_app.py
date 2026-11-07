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
    s.save_settings({"my_instagram_followers": "5000"})
    return s


def test_store_merges_and_levels(store):
    a = Artist(name="Lil Nova", metrics={"instagram_followers": 10_000})
    lead_id = store.save_artist(a)
    store.update_lead(lead_id, status="shortlist", notes="great hooks")
    result = ArtistResult(artist=a, instagram="LilNova", metrics={"instagram_followers": 12_000},
                          emails=[EmailResult("booking@lilnova.com", 85, "high", ["website"], ["u"])])
    assert store.save_artist(Artist(name="lil nova"), result) == lead_id
    lead = store.lead(lead_id)
    assert lead["status"] == "shortlist" and lead["notes"] == "great hooks"  # kept
    assert lead["instagram"] == "lilnova"
    assert lead["best_email"] == "booking@lilnova.com"
    assert lead["level_bucket"] == "match" and round(lead["level_ratio"], 1) == 2.4

    store.save_artist(Artist(name="Megastar", metrics={"instagram_followers": 5_000_000}))
    rows, total = store.leads(level="match")
    assert [r["name"] for r in rows] == ["Lil Nova"]
    rows, _ = store.leads(level="bigger")
    assert [r["name"] for r in rows] == ["Megastar"]
    # Changing my numbers re-buckets everyone.
    store.save_settings({"my_instagram_followers": "2000000"})
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
    assert lead["level_bucket"] == "match"
    # Capturing TikTok for the same name merges into the same lead.
    lead2, _, _ = ingest_capture(store, "https://www.tiktok.com/@lilnova.music",
                                 "Lil Nova (@lilnova.music) | TikTok", "45.1K Followers")
    assert lead2 == lead_id
    assert store.lead(lead_id)["metrics"]["tiktok_followers"] == 45_100


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

    c.post("/settings", data={"my_instagram_followers": "3K", "level_low": "0.5", "level_high": "2",
                              "email_subject": "Hi {artist}", "email_body": "Yo {first_name}"})
    assert store.lead(lead_id)["level_bucket"] == "match"
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
        Artist("Peer", discovered_via="spotify"),
        Artist("Tiny", discovered_via="spotify"),
        Artist("Peer2", discovered_via="spotify"),
    ]
    store.save_settings({"my_spotify_followers": "2000"})
    finder = FakeFinder({"Peer": {"instagram_followers": 6000}, "Tiny": {"instagram_followers": 10},
                         "Peer2": {"spotify_followers": 1500}})
    runner = JobRunner(store, Settings(), finder_factory=lambda http: finder)
    monkeypatch.setattr(runner, "_candidates", lambda params, http: iter(candidates))
    job_id = runner.submit("discover", {"source": "spotify", "genre": "trap", "limit": 2,
                                        "only_my_level": True, "find_emails": True})
    runner.run(job_id)
    job = store.job(job_id)
    assert job["status"] == "done", job["message"]
    assert job["progress"] == 2
    rows, _ = store.leads(level="match")
    assert sorted(r["name"] for r in rows) == ["Peer", "Peer2"]
    # Huge was skipped before the email lookup; Tiny after it. Both are remembered.
    assert store.lead(store.find_lead(name="Huge")["id"])["best_email"] == ""
    assert store.has_lead("Tiny")
