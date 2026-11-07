import json
from types import SimpleNamespace

import pytest

from artist_email_finder import ai, jobs
from artist_email_finder.ai import SearchPlan, keyword_plan
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


# ---------------------------------------------------------------- Claude request shape
def test_plan_search_uses_structured_output_and_fallbacks(monkeypatch):
    sent = {}

    def create(**kw):
        sent.update(kw)
        plan = {"summary": "Female punk bands", "genres": ["Punk Rock"], "tags": ["riot grrrl"],
                "gender": "female", "artist_type": "group", "countries": [], "identity_tags": [],
                "region_label": "", "areas": [], "level": "my_level",
                "suggested_artists": ["Some Band"], "limit": 15}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(plan))])

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(ai, "_client", lambda: fake)
    plan = ai.plan_search("female punk bands")
    assert plan.parsed_by == "claude" and plan.genres == ["punk rock"] and plan.limit == 15
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]


def test_plan_search_falls_back_on_refusal(monkeypatch):
    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(stop_reason="refusal", content=[]))))
    monkeypatch.setattr(ai, "_client", lambda: fake)
    plan = ai.plan_search("female punk band")
    assert plan.parsed_by == "keywords" and plan.gender == "female"


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
    plan = keyword_plan("female punk rock band")
    plan.suggested_artists = ["Echo Pals"]
    search_id = store.create_search("female punk rock band", plan.to_dict())
    runner = JobRunner(store, Settings(), finder_factory=lambda http: FakeFinder())
    job_id = runner.submit("search", {"search_id": search_id})
    runner.run(job_id)
    assert store.job(job_id)["status"] == "done", store.job(job_id)["message"]
    names = [r["name"] for r in store.search_results(search_id)]
    assert sorted(names) == ["Echo Pals", "Riot Kids"]       # male band and solo artist rejected
    riot = next(r for r in store.search_results(search_id) if r["name"] == "Riot Kids")
    assert riot["gender"] == "female" and riot["artist_type"] == "group" and riot["genre_list"] == ["punk rock"]
    assert riot["match_reason"].startswith("Female · Band")


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
    data = c.get("/api/search/1").get_json()
    assert data["status"] == "queued" and data["count"] == 0
