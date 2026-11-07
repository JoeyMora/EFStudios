"""Instagram via Meta's official Business Discovery API.

Business Discovery lets *your* Instagram business/creator account read the
public profile of any other business or creator account: bio, website,
follower count, and like/comment counts on recent posts (engagement). It doesn't work for personal accounts, and it doesn't return
the in-app "Email" button. Many artists put the email in the bio or behind the
website link (usually a Linktree), and this API returns both.

Needs META_ACCESS_TOKEN and IG_USER_ID (see README). Rate limit: about 200
calls per hour.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from ..level import typical
from ..extract import context_for, emails_from_text
from ..net import HttpClient
from ..models import Finding

log = logging.getLogger(__name__)

RESERVED = {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "about",
            "legal", "direct", "developer", "tags", "locations", "web", "share"}


def instagram_handle(url: str) -> str | None:
    parsed = urlparse(url if "://" in url else "https://" + url)
    if not parsed.netloc.lower().endswith("instagram.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts or parts[0].lower() in RESERVED:
        return None
    handle = parts[0].lstrip("@")
    return handle.lower() if re.fullmatch(r"[\w.]{1,30}", handle) else None


def tiktok_handle(url: str) -> str | None:
    parsed = urlparse(url if "://" in url else "https://" + url)
    if not parsed.netloc.lower().endswith("tiktok.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts or not parts[0].startswith("@"):
        return None
    handle = parts[0][1:]
    return handle.lower() if re.fullmatch(r"[\w.]{2,24}", handle) else None


class InstagramGraph:
    def __init__(self, http: HttpClient, access_token: str, ig_user_id: str,
                 api_version: str = "v24.0"):
        self.http = http
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self.base = f"https://graph.facebook.com/{api_version}"

    def profile(self, handle: str) -> dict | None:
        """Public profile of a business/creator account, or None."""
        fields = ("business_discovery.username({u}){{username,name,biography,website,"
                  "followers_count,media_count,"
                  "media.limit(12){{like_count,comments_count,timestamp,media_type}}}}").format(u=handle)
        data = self.http.get_json(f"{self.base}/{self.ig_user_id}",
                                  params={"fields": fields, "access_token": self.access_token})
        return (data or {}).get("business_discovery")

    def find(self, handle: str) -> tuple[list[Finding], dict]:
        """(findings, info): info has instagram_followers, instagram_engagement
        (median likes+comments over the last 12 posts), website and bio."""
        prof = self.profile(handle)
        if not prof:
            return [], {}
        url = f"https://www.instagram.com/{handle}/"
        bio = prof.get("biography") or ""
        findings = [Finding(e, "social_bio", url, context_for(e, bio))
                    for e in emails_from_text(bio)]
        posts = ((prof.get("media") or {}).get("data")) or []
        # Posts with hidden like counts don't return like_count; leave them out.
        per_post = [p["like_count"] + p.get("comments_count", 0)
                    for p in posts if p.get("like_count") is not None]
        info = {
            "instagram_followers": prof.get("followers_count"),
            "instagram_engagement": typical(per_post),
            "website": prof.get("website") or "",
            "bio": bio,
            "name": prof.get("name") or "",
        }
        return findings, info
