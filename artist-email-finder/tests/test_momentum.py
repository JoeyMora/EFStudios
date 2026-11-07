from artist_email_finder.models import Artist
from artist_email_finder.momentum import growth, reach, score
from artist_email_finder.store import Store

DAY = 86400


def test_reach_and_score():
    assert reach({"instagram_reel_views": 30_000, "instagram_followers": 10_000}) == (3.0, "per reel")
    m, note = score({"instagram_reel_views": 30_000, "instagram_followers": 10_000})
    assert m == 100.0 and note == "views 3.0× their followers per reel"
    low, _ = score({"tiktok_views": 100, "tiktok_followers": 10_000})
    assert low == 0.0
    assert score({"instagram_followers": 5000}) == (None, "")


def test_growth_needs_time():
    assert growth([(0, {"tiktok_views": 1000}), (DAY, {"tiktok_views": 2000})]) is None   # under 3 days
    g = growth([(0, {"tiktok_views": 1000}), (14 * DAY, {"tiktok_views": 4000})])
    assert round(g, 6) == 2.0   # 4x over two weeks = doubling weekly
    m, note = score({}, [(0, {"tiktok_views": 1000}), (14 * DAY, {"tiktok_views": 4000})])
    assert m == 100.0 and note == "engagement up 100% a week"


def test_store_tracks_snapshots_and_momentum(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    lead = store.save_artist(Artist("A"))
    store.update_metrics(lead, {"tiktok_views": 1000, "tiktok_followers": 10_000})
    with store.conn() as c:  # pretend the first check was two weeks ago
        c.execute("UPDATE snapshots SET at = at - ?", (14 * DAY,))
    store.update_metrics(lead, {"tiktok_views": 4000})
    assert len(store.snapshots(lead)) == 2
    row = store.lead(lead)
    assert "up 100% a week" in row["momentum_note"]
    assert row["momentum"] > 60
    rows, _ = store.leads(rising=True)
    assert [r["name"] for r in rows] == ["A"]
