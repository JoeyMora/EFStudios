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
