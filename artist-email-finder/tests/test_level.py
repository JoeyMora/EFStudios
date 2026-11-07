from artist_email_finder.counts import followers_in, parse_count
from artist_email_finder.level import bucket, ratio


def test_parse_counts():
    assert parse_count("12.5K") == 12_500
    assert parse_count("1,234") == 1234
    assert parse_count("2M") == 2_000_000
    assert parse_count("980") == 980
    assert parse_count("abc") is None


def test_followers_in_profile_text():
    ig = "12K Followers, 500 Following, 80 Posts - See Instagram photos and videos from Lil Nova (@lilnova)"
    assert followers_in(ig) == 12_000
    tt = "Lil Nova (@lilnova) on TikTok | 2.3M Likes. 120.5K Followers. ATL"
    assert followers_in(tt) == 120_500
    header = "lilnova Follow 120 posts 3,402 followers 400 following"
    assert followers_in(header) == 3402


def test_ratio_and_bucket():
    mine = {"instagram_followers": 5000, "spotify_followers": 1000}
    assert ratio({"instagram_followers": 10000}, mine) == 2.0
    # geometric mean of 4x and 1x = 2x
    assert round(ratio({"instagram_followers": 20000, "spotify_followers": 1000}, mine), 6) == 2.0
    assert ratio({"tiktok_followers": 5}, mine) is None
    assert bucket(2.0, 0.25, 4) == "match"
    assert bucket(10, 0.25, 4) == "bigger"
    assert bucket(0.1, 0.25, 4) == "smaller"
    assert bucket(None, 0.25, 4) == "unknown"
