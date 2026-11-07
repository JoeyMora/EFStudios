from artist_email_finder.cli import parse_line, read_artists
from artist_email_finder.config import Settings
from artist_email_finder.finder import Finder

PAGES = {
    "https://linktr.ee/lilnova": """<a href="https://lilnova.com">Website</a>
        <a href="https://open.spotify.com/artist/1">Spotify</a>
        <a href="https://www.youtube.com/@lilnova">YouTube</a>""",
    "https://lilnova.com": """<h1>Lil Nova</h1><a href="/booking">Booking</a>
        <a href="/blog/post-1">Blog</a>""",
    "https://lilnova.com/booking": "<p>For shows: booking [at] lilnova [dot] com</p>",
}


class FakeHttp:
    def __init__(self):
        self.fetched = []

    def get_html(self, url):
        self.fetched.append(url)
        url = url.rstrip("/")
        return (url, PAGES[url]) if url in PAGES else None

    def get_json(self, *a, **k):
        raise AssertionError("no API calls expected")


def test_finder_follows_link_page_to_contact_page():
    http = FakeHttp()
    finder = Finder(Settings(), http, use_musicbrainz=False)
    result = finder.lookup(parse_line("Lil Nova, https://linktr.ee/lilnova"))
    assert [e.email for e in result.emails] == ["booking@lilnova.com"]
    assert result.emails[0].confidence == "high"
    assert "https://lilnova.com/booking" in http.fetched
    assert not any("spotify" in u or "youtube" in u for u in http.fetched)
    assert "https://lilnova.com/blog/post-1" not in http.fetched


def test_parse_line_handles():
    a = parse_line("Lil Nova, @lilnova, lilnova.com")
    assert a.name == "Lil Nova" and a.links == ["@lilnova", "lilnova.com"]
    b = parse_line("https://linktr.ee/lil_nova")
    assert b.name == "lil nova" and b.links == ["https://linktr.ee/lil_nova"]


def test_read_csv(tmp_path):
    f = tmp_path / "artists.csv"
    f.write_text("Name,Instagram,Website,Notes\nLil Nova,@lilnova,lilnova.com,met at show\n")
    [a] = read_artists(f)
    assert a.name == "Lil Nova"
    assert a.links == ["@lilnova", "lilnova.com"]


class FakeSearch:
    def find(self, name):
        return [], []

    def profile(self, platform, handle="", name=""):
        if platform == "instagram":
            return {"handle": "lilnova", "url": "https://www.instagram.com/lilnova/",
                    "text": "9K Followers, 10 Following - Lil Nova (@lilnova)", "followers": 9000}
        return {"handle": "lilnova", "url": "https://www.tiktok.com/@lilnova",
                "text": "Lil Nova (@lilnova) on TikTok | 40K Followers. mgmt: nova@team.co",
                "followers": 40000}


class FakeGraph:
    def __init__(self):
        self.calls = []

    def find(self, handle):
        self.calls.append(handle)
        return ([], {"instagram_followers": 9100, "instagram_engagement": 410,
                     "website": "https://linktr.ee/lilnova"})


def test_finder_instagram_and_tiktok_paths():
    http = FakeHttp()
    finder = Finder(Settings(), http, use_musicbrainz=False)
    finder.web_search, finder.social_search = FakeSearch(), True
    finder.instagram = FakeGraph()
    from artist_email_finder.models import Artist
    result = finder.lookup(Artist(name="Lil Nova"))
    # Handle found via search, then the official API, whose bio link is crawled.
    assert finder.instagram.calls == ["lilnova"]
    assert "https://linktr.ee/lilnova" in http.fetched
    assert result.instagram == "lilnova" and result.tiktok == "lilnova"
    assert result.metrics == {"instagram_followers": 9100, "instagram_engagement": 410,
                              "tiktok_followers": 40000}
    assert [e.email for e in result.emails] == ["booking@lilnova.com", "nova@team.co"]
    # Instagram/TikTok pages themselves are never fetched directly.
    assert not any("instagram.com" in u or "tiktok.com" in u for u in http.fetched)


class GraphHttp:
    def get_json(self, url, params=None, **kw):
        assert "media.limit(12){like_count,comments_count" in params["fields"]
        return {"business_discovery": {
            "biography": "booking: hi@nova.co", "followers_count": 20000, "website": "",
            "media": {"data": [
                {"like_count": 300, "comments_count": 20}, {"like_count": 280, "comments_count": 10},
                {"comments_count": 5},  # likes hidden: ignored
                {"like_count": 9000, "comments_count": 400}, {"like_count": 350, "comments_count": 15},
            ]}}}


def test_instagram_engagement_is_median_of_recent_posts():
    from artist_email_finder.sources.instagram import InstagramGraph
    findings, info = InstagramGraph(GraphHttp(), "tok", "123").find("lilnova")
    assert [f.email for f in findings] == ["hi@nova.co"]
    assert info["instagram_followers"] == 20000
    assert info["instagram_engagement"] == 342   # median of 290, 320, 365, 9400


class YtHttp:
    def get_json(self, url, params=None, **kw):
        if url.endswith("/channels"):
            return {"items": [{"id": "UC1", "snippet": {"description": ""},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
                               "statistics": {"subscriberCount": "5000"}}]}
        if url.endswith("/playlistItems"):
            return {"items": [{"snippet": {"resourceId": {"videoId": f"v{i}"}, "description": ""}}
                              for i in range(4)]}
        if url.endswith("/videos"):
            assert params["id"] == "v0,v1,v2,v3"
            return {"items": [{"statistics": {"viewCount": n}} for n in ("1200", "900", "40000", "1500")]}
        raise AssertionError(url)


def test_youtube_typical_views():
    from artist_email_finder.sources.youtube import YouTube
    _, info = YouTube(YtHttp(), "key").find("Lil Nova", ["https://www.youtube.com/@lilnova"])
    assert info["subscribers"] == 5000 and info["views"] == 1350
