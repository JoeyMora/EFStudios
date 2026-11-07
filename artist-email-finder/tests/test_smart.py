import json
from types import SimpleNamespace

import pytest

from artist_email_finder import jobs
from artist_email_finder.prompt import SearchPlan, infer_genres, keyword_plan
from artist_email_finder.config import Settings
from artist_email_finder.genres import split_tags
from artist_email_finder.jobs import JobRunner
from artist_email_finder.models import Artist, ArtistResult, EmailResult
from artist_email_finder.smart import match
from artist_email_finder.sources.musicbrainz import MusicBrainz
from artist_email_finder.store import Store
from artist_email_finder.web import create_app


# ---------------------------------------------------------------- prompt parsing
def test_keyword_plan_female_punk_band():
    plan = keyword_plan("Find me a female punk rock band")
    assert plan.gender == "female" and plan.artist_type == "group"
    assert plan.genres == ["punk rock"]
    assert "female fronted" in plan.tags


def test_keyword_plan_asian_rock_and_roll():
    plan = keyword_plan("find me an Asian artist that sings rock and roll")
    assert plan.genres == ["rock and roll"]
    assert "JP" in plan.countries and "KR" in plan.countries
    assert "asian american" in plan.identity_tags
    assert plan.region_label.startswith("Asia")
    assert plan.gender == "any"


def test_keyword_plan_city_and_count():
    plan = keyword_plan("10 R&B singers in Atlanta")
    assert plan.genres == ["r&b"] and plan.areas == ["Atlanta"] and plan.limit == 10
    assert plan.artist_type == "solo"
    assert "Atlanta" in plan.chips() and "At my level" in plan.chips()


def test_musicbrainz_query():
    q = MusicBrainz.build_query(["punk rock"], ["riot grrrl"], "female", "group", ["JP"], ["asian american"], ["Tokyo"])
    assert q == ('(tag:"punk rock" OR tag:"riot grrrl") AND gender:female AND type:group AND '
                 '(country:JP OR tag:"asian american") AND (area:"Tokyo" OR beginarea:"Tokyo")')


# ---------------------------------------------------------------- more prompt forms
def test_keyword_plan_like_artist_and_trending():
    plan = keyword_plan("Indie bands like Alvvays but smaller, trending")
    assert plan.similar_to == "Alvvays"
    assert plan.genres == [] and plan.artist_type == "group"   # "indie" alone isn't a genre; Alvvays' genres fill in
    assert plan.trending and plan.level == "smaller"
    assert "Like Alvvays" in plan.chips() and "Rising" in plan.chips()
    assert keyword_plan("I'd like R&B singers").similar_to == ""


def test_keyword_plan_spellings():
    assert keyword_plan("rock n roll bands from Japan").genres == ["rock and roll"]
    assert keyword_plan("rnb and hiphop").genres == ["r&b", "hip hop"]
    assert keyword_plan("pop punk duo").genres == ["pop punk"]           # not "pop" + "punk"
    assert keyword_plan("Chicago-based drill rappers").areas == ["Chicago"]
    assert keyword_plan("female rappers").genres == ["hip hop"]


def test_infer_genres_from_bio():
    assert infer_genres("X", ["Atlanta-based alt R&B singer. Neo-soul vibes. Booking: x@y.com"]) == ["alternative r&b", "neo soul"]


# ---------------------------------------------------------------- matching
def test_match_rules():
    plan = keyword_plan("female punk rock band from Japan")
    ok, score, why = match(Artist("A", gender="female", artist_type="group", country="JP", genres=["punk rock"]), plan)
    assert ok and why[:3] == ["Female", "Band", "Japan"] and "punk rock" in why
    assert not match(Artist("B", gender="male", genres=["punk rock"]), plan)[0]          # published gender differs
    assert not match(Artist("C", country="US", genres=["punk rock"]), plan)[0]           # wrong country
    assert not match(Artist("D", genres=["country"]), plan)[0]                           # wrong genre
    ok_unknown, s_unknown, _ = match(Artist("E", genres=["punk"]), plan)                  # facts unknown: kept, lower
    assert ok_unknown and s_unknown < score
    # Self-identification tags count for regional requests.
    asian = keyword_plan("Asian rock and roll artist")
    assert match(Artist("F", country="US", tags=["asian american"], genres=["rock and roll"]), asian)[0]


def test_split_tags():
    genres, other = split_tags(["Indie Rock", "seen live", "female vocalists", "shoegaze", "japanese", "dream pop"])
    assert genres == ["indie rock", "shoegaze", "dream pop"]
    assert "female vocalists" in other and "japanese" in other


# ---------------------------------------------------------------- jobs
class FakeMB:
    PROFILES = {
        "Riot Kids": dict(gender="female", artist_type="group", country="US", genres=["punk rock"]),
        "Dude Band": dict(gender="male", artist_type="group", country="US", genres=["punk rock"]),
        "Soft Girl": dict(gender="female", artist_type="solo", country="US", genres=["punk rock"]),
        "Echo Pals": dict(gender="", artist_type="group", country="", genres=["punk rock", "riot grrrl"]),
    }

    def __init__(self, http):
        pass

    @staticmethod
    def build_query(*a, **k):
        return MusicBrainz.build_query(*a, **k)

    def _artist(self, name, via):
        p = self.PROFILES[name]
        return Artist(name=name, discovered_via=via, profiled=True, location="", tags=[], **p)

    def search(self, query, max_results=1000):
        for n in ["Dude Band", "Riot Kids", "Soft Girl"]:
            yield self._artist(n, "musicbrainz")

    def profile(self, name):
        if name not in self.PROFILES:
            return {}
        return {"id": "x", "name": name, "urls": [], "tags": [], "location": "", **self.PROFILES[name]}


class FakeLastFm:
    def __init__(self, http, key):
        pass

    def similar_scored(self, seed, limit=40):
        return [("Echo Pals", .9)]

    def tag_artists(self, tag, max_pages=40):
        return iter([])


class FakeFinder:
    def __init__(self, metrics=None):
        self.metrics = metrics or {}

    def lookup(self, artist):
        return ArtistResult(artist=artist, metrics=self.metrics.get(artist.name, {}),
                            emails=[EmailResult(f"hi@{artist.name.replace(' ', '').lower()}.com", 85, "high", ["website"], [])])


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "leads.db")


def test_search_job_keeps_only_matches(store, monkeypatch):
    monkeypatch.setattr(jobs, "MusicBrainz", FakeMB)
    monkeypatch.setattr(jobs, "LastFm", FakeLastFm)
    plan = keyword_plan("female punk rock band")
    plan.similar_to = "Seed Band"   # its Last.fm neighbours come first
    search_id = store.create_search("female punk rock band", plan.to_dict())
    runner = JobRunner(store, Settings(lastfm_api_key="k"), finder_factory=lambda http: FakeFinder())
    job_id = runner.submit("search", {"search_id": search_id})
    runner.run(job_id)
    assert store.job(job_id)["status"] == "done", store.job(job_id)["message"]
    names = [r["name"] for r in store.search_results(search_id)]
    assert sorted(names) == ["Echo Pals", "Riot Kids"]       # male band and solo artist rejected
    riot = next(r for r in store.search_results(search_id) if r["name"] == "Riot Kids")
    assert riot["gender"] == "female" and riot["artist_type"] == "group" and riot["genre_list"] == ["punk rock"]
    assert riot["match_reason"].startswith("Female · Band")
    echo = next(r for r in store.search_results(search_id) if r["name"] == "Echo Pals")
    assert echo["match_reason"].startswith("Like Seed Band")


def test_recommend_job_makes_picks(store, monkeypatch):
    monkeypatch.setattr(jobs, "MusicBrainz", FakeMB)
    liked = store.save_artist(Artist("Seed", genres=["punk rock"]))
    store.update_lead(liked, status="shortlist")
    runner = JobRunner(store, Settings(), finder_factory=lambda http: FakeFinder())
    job_id = runner.submit("recommend", {"limit": 2})
    runner.run(job_id)
    assert store.job(job_id)["status"] == "done", store.job(job_id)["message"]
    picks = store.picks()
    assert len(picks) == 2 and all("punk rock" in p["pick_reason"] for p in picks)

    app = create_app(str(store.path), Settings(), start_jobs=False)
    c = app.test_client()
    page = c.get("/for-you?auto=0").data.decode()
    assert 'class="deck"' in page and picks[0]["name"] in page
    assert c.post(f"/api/pick/{picks[0]['id']}", json={"action": "save"}).json["remaining"] == 1
    assert store.lead(picks[0]["id"])["status"] == "shortlist"
    c.post(f"/api/pick/{picks[1]['id']}", json={"action": "pass"})
    assert store.lead(picks[1]["id"])["status"] == "not_a_fit" and store.picks() == []


def test_discover_page_and_search_api(store, monkeypatch):
    app = create_app(str(store.path), Settings(), start_jobs=False)
    c = app.test_client()
    monkeypatch.setattr("artist_email_finder.web.plan_search", keyword_plan)
    r = c.post("/search", data={"prompt": "female punk rock band"})
    assert r.status_code == 302 and "/discover?s=" in r.headers["Location"]
    page = c.get(r.headers["Location"]).data.decode()
    assert "female punk rock band" in page and "Band / group" in page and "punk rock" in page
    assert "anthropic" not in page.lower() and "claude" not in page.lower()
    data = c.get("/api/search/1").get_json()
    assert data["status"] == "queued" and data["count"] == 0
