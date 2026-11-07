from artist_email_finder.counts import followers_in, parse_count
from artist_email_finder.level import bucket, engagement_rate, ratio, typical


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


def test_ratio_uses_engagement_not_followers():
    mine = {"instagram_engagement": 400, "tiktok_views": 2000}
    # Followers are ignored entirely.
    assert ratio({"instagram_followers": 1_000_000}, mine) is None
    assert ratio({"instagram_engagement": 800}, mine) == 2.0
    # geometric mean of 4x (IG) and 1x (TikTok) = 2x
    assert round(ratio({"instagram_engagement": 1600, "tiktok_views": 2000}, mine), 6) == 2.0
    # A big account with weak engagement is at my level.
    assert bucket(ratio({"instagram_followers": 250_000, "instagram_engagement": 500}, mine),
                  0.25, 4) == "match"
    assert bucket(10, 0.25, 4) == "bigger"
    assert bucket(0.1, 0.25, 4) == "smaller"
    assert bucket(None, 0.25, 4) == "unknown"


def test_typical_is_median():
    assert typical([100, 120, 90, 5000]) == 110   # one viral post doesn't skew it
    assert typical([None, None]) is None


def test_engagement_rate():
    assert engagement_rate({"instagram_engagement": 300, "instagram_followers": 10_000}) == 3.0
    assert engagement_rate({"instagram_engagement": 300}) is None
