from artist_email_finder.models import Finding
from artist_email_finder.scoring import name_similarity, rank


def test_ranking_prefers_artist_owned_matching_emails():
    findings = [
        Finding("booking@lilnova.com", "website", "https://lilnova.com/contact"),
        Finding("booking@lilnova.com", "youtube", "https://youtube.com/channel/x"),
        Finding("editor@musicblog.com", "web_search", "https://musicblog.com/a", on_artist_page=False),
    ]
    results = rank(findings, "Lil Nova", {"lilnova.com"})
    assert results[0].email == "booking@lilnova.com"
    assert results[0].confidence == "high"
    assert results[0].sources == ["website", "youtube"]
    assert results[-1].email == "editor@musicblog.com"
    assert results[-1].confidence == "low"


def test_name_similarity():
    assert name_similarity("Beyoncé", "beyonce") == 1.0
    assert name_similarity("Lil Nova", "Big Nova") < 0.9
