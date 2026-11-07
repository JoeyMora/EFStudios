"""Pull email addresses out of text and HTML, and drop the junk ones."""

from __future__ import annotations

import html as html_lib
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

EMAIL_RE = re.compile(
    r"(?<![\w.+%-])"
    r"([A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,24})"
    r"(?![\w-])"
)

# "name [at] domain [dot] com", "name (at) domain (dot) com", "name {at} ..."
_AT_RE = re.compile(r"\s*[\[\(\{<]\s*at\s*[\]\)\}>]\s*", re.IGNORECASE)
_DOT_RE = re.compile(r"\s*[\[\(\{<]\s*dot\s*[\]\)\}>]\s*", re.IGNORECASE)
# "name AT domain DOT com" (only the all-caps form; lowercase "at" is too common in prose)
_AT_CAPS_RE = re.compile(r"(?<=\w) AT (?=\w)")
_DOT_CAPS_RE = re.compile(r"(?<=\w) DOT (?=\w)")

_FILE_TLDS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "ico", "bmp", "tiff",
    "js", "mjs", "css", "map", "json", "xml", "txt", "pdf", "zip", "mp3",
    "mp4", "wav", "mov", "woff", "woff2", "ttf", "eot", "php", "html", "htm",
}

# Addresses on these domains belong to the platform, not the artist.
PLATFORM_DOMAINS = {
    "example.com", "example.org", "example.net", "domain.com", "email.com",
    "yourdomain.com", "yourname.com", "mysite.com", "test.com",
    "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "sentry.wixpress.com",
    "linktr.ee", "linktree.com", "beacons.ai", "instagram.com", "facebook.com",
    "fb.com", "meta.com", "tiktok.com", "bytedance.com", "soundcloud.com",
    "youtube.com", "google.com", "spotify.com", "apple.com", "bandcamp.com",
    "squarespace.com", "wix.com", "shopify.com", "cloudflare.com", "w3.org",
    "schema.org", "twitter.com", "x.com", "godaddy.com", "wordpress.com",
    "wordpress.org", "musicbrainz.org", "brave.com", "serpapi.com",
    "stan.store", "carrd.co", "hoo.be", "lnk.bio", "bio.link", "komi.io",
    "mailchimp.com", "sendgrid.net", "amazonaws.com", "github.com",
}

_JUNK_LOCAL = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|privacy|abuse|legal|dmca|copyright|"
    r"webmaster|postmaster|hostmaster|mailer-daemon|unsubscribe|bounce[s]?|"
    r"name|email|your-?email|you|user|username|someone|firstname\.lastname)$",
    re.IGNORECASE,
)
_HEX_LOCAL = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)


def normalize_email(email: str) -> str:
    return email.strip().strip(".").lower()


def is_plausible(email: str) -> bool:
    email = normalize_email(email)
    if email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not local or not domain or ".." in email:
        return False
    tld = domain.rsplit(".", 1)[-1]
    if tld in _FILE_TLDS:
        return False
    if re.search(r"@\d+x\.", email):  # retina image names like logo@2x.png
        return False
    if domain in PLATFORM_DOMAINS or any(domain.endswith("." + d) for d in PLATFORM_DOMAINS):
        return False
    if _JUNK_LOCAL.match(local) or _HEX_LOCAL.match(local):
        return False
    if local.startswith("u00") or local.startswith("x22"):  # escaped-JSON debris
        return False
    return True


def deobfuscate(text: str) -> str:
    text = _AT_RE.sub("@", text)
    text = _DOT_RE.sub(".", text)
    text = _AT_CAPS_RE.sub("@", text)
    text = _DOT_CAPS_RE.sub(".", text)
    return text


def emails_from_text(text: str) -> list[str]:
    """Return unique plausible emails in order of first appearance."""
    if not text:
        return []
    text = html_lib.unescape(text)
    found: list[str] = []
    seen: set[str] = set()
    for candidate_text in (text, deobfuscate(text)):
        for match in EMAIL_RE.finditer(candidate_text):
            email = normalize_email(match.group(1))
            if email not in seen and is_plausible(email):
                seen.add(email)
                found.append(email)
    return found


def emails_from_html(html: str) -> list[str]:
    """Emails from mailto: links, visible text, meta descriptions and JSON-LD."""
    soup = BeautifulSoup(html, "html.parser")
    chunks: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            chunks.append(unquote(href[7:].split("?", 1)[0]))

    for meta in soup.find_all("meta"):
        if (meta.get("name") or meta.get("property") or "").lower() in {
            "description", "og:description", "twitter:description",
        }:
            chunks.append(meta.get("content") or "")

    for script in soup.find_all("script", type="application/ld+json"):
        chunks.append(script.string or "")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    chunks.append(soup.get_text(" "))

    found: list[str] = []
    for chunk in chunks:
        for email in emails_from_text(chunk):
            if email not in found:
                found.append(email)
    return found


def context_for(email: str, text: str, width: int = 60) -> str:
    """A short snippet of text around the email, for the output CSV."""
    idx = text.lower().find(email.split("@")[0].lower())
    if idx < 0:
        return ""
    start = max(0, idx - width)
    snippet = text[start: idx + len(email) + width]
    return " ".join(snippet.split())
