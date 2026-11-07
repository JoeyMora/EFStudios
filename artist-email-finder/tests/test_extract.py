from artist_email_finder.extract import emails_from_html, emails_from_text, is_plausible


def test_plain_and_obfuscated():
    text = "Booking: Bookings@ArtistSite.com | mgmt [at] artistmgmt [dot] co | press (at) label (dot) net"
    assert emails_from_text(text) == [
        "bookings@artistsite.com", "mgmt@artistmgmt.co", "press@label.net",
    ]


def test_caps_obfuscation():
    assert emails_from_text("email me: joey AT efent DOT com") == ["joey@efent.com"]


def test_junk_is_dropped():
    junk = [
        "logo@2x.png", "noreply@artist.com", "support@linktr.ee", "a1b2c3d4e5f6a7b8c9d0@sentry.io",
        "name@domain.com", "help@instagram.com", "privacy@artist.com",
    ]
    for email in junk:
        assert not is_plausible(email), email
    assert is_plausible("booking@artist.com")


def test_html_sources():
    html = """
    <html><head><meta property="og:description" content="Rapper. Booking: bookme@rapper.com">
    <script type="application/ld+json">{"email": "info@rapper.com"}</script>
    <script>var x = "tracking@analytics-junk.com";</script></head>
    <body><a href="mailto:mgmt%40rapper.com?subject=hi">Email</a>
    <p>Features: features &#64; rapper.com</p></body></html>
    """
    found = emails_from_html(html)
    assert "bookme@rapper.com" in found
    assert "info@rapper.com" in found
    assert "mgmt@rapper.com" in found
    assert "tracking@analytics-junk.com" not in found
