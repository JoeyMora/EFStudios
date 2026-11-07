"""Claude-powered helpers: turn a prompt into a search plan, infer genres,
and suggest artists. Every function has a non-AI fallback, so the app still
works without ANTHROPIC_API_KEY (just less cleverly).

Identity terms in prompts ("female", "Asian", "Latin") are only ever matched
against what artists publish about themselves: MusicBrainz gender/country/
area, and the tags and genres artists and listeners use. Nothing is inferred
from names, photos, or appearance.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")

# --------------------------------------------------------------------------
# Search plan
# --------------------------------------------------------------------------


@dataclass
class SearchPlan:
    summary: str = ""
    genres: list[str] = field(default_factory=list)       # e.g. ["punk rock"]
    tags: list[str] = field(default_factory=list)         # extra descriptor tags to search, e.g. ["riot grrrl"]
    gender: str = "any"                                   # female | male | nonbinary | any
    artist_type: str = "any"                              # solo | group | any
    countries: list[str] = field(default_factory=list)    # ISO 3166-1 alpha-2
    identity_tags: list[str] = field(default_factory=list)  # self-described tags, e.g. ["asian american"]
    region_label: str = ""                                # human label, e.g. "Asia / Asian diaspora"
    areas: list[str] = field(default_factory=list)        # cities / regions, e.g. ["Atlanta"]
    level: str = "my_level"                               # my_level | any | bigger | smaller
    suggested_artists: list[str] = field(default_factory=list)
    limit: int = 20
    parsed_by: str = "keywords"

    @classmethod
    def from_dict(cls, d: dict) -> "SearchPlan":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)

    def chips(self) -> list[str]:
        """Short labels describing the plan, for the UI."""
        out = []
        if self.gender != "any":
            out.append({"female": "Female", "male": "Male", "nonbinary": "Non-binary"}.get(self.gender, self.gender))
        if self.artist_type != "any":
            out.append("Band / group" if self.artist_type == "group" else "Solo artist")
        if self.region_label:
            out.append(self.region_label)
        out += self.areas
        out += self.genres
        out += [t for t in self.tags if t not in self.genres][:3]
        if self.level != "any":
            out.append({"my_level": "At my level", "bigger": "Bigger than me",
                        "smaller": "Smaller than me"}[self.level])
        return out


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Short restatement, e.g. 'Female-fronted punk rock bands'"},
        "genres": {"type": "array", "items": {"type": "string"},
                   "description": "1-4 lowercase genre names as music databases use them, e.g. 'punk rock', 'rock and roll', 'alt r&b'"},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "0-6 extra lowercase descriptor tags listeners use on Last.fm/MusicBrainz that help find matches, e.g. 'riot grrrl', 'female fronted', 'garage rock'"},
        "gender": {"type": "string", "enum": ["female", "male", "nonbinary", "any"]},
        "artist_type": {"type": "string", "enum": ["solo", "group", "any"]},
        "countries": {"type": "array", "items": {"type": "string"},
                      "description": "ISO 3166-1 alpha-2 codes for countries of origin the user asked for (a region like 'Asian' expands to its countries)"},
        "identity_tags": {"type": "array", "items": {"type": "string"},
                          "description": "Self-identification tags artists use for the requested identity, e.g. 'asian american', 'j-rock'"},
        "region_label": {"type": "string", "description": "Human label for the requested region/identity, or ''"},
        "areas": {"type": "array", "items": {"type": "string"}, "description": "Cities or regions named, e.g. 'Atlanta'"},
        "level": {"type": "string", "enum": ["my_level", "any", "bigger", "smaller"],
                  "description": "'my_level' unless the user asks for any size, bigger, or smaller artists"},
        "suggested_artists": {"type": "array", "items": {"type": "string"},
                              "description": "Up to 15 real artists you are confident match, favouring independent and emerging acts over famous ones"},
        "limit": {"type": "integer", "description": "How many results they want; 20 if unspecified"},
    },
    "required": ["summary", "genres", "tags", "gender", "artist_type", "countries", "identity_tags",
                 "region_label", "areas", "level", "suggested_artists", "limit"],
    "additionalProperties": False,
}

PLAN_SYSTEM = """You turn a music producer's request for artists into a search plan for music databases \
(MusicBrainz, Last.fm, Spotify). The producer is looking for artists to offer mixing and mastering to.

Rules:
- Genres: use the names music databases use. Map casual wording ("rock and roll", "sad girl indie") to real genres and tags.
- Identity (gender, region, ethnicity, nationality): only translate what the user asked for into fields the databases \
publish: MusicBrainz gender, country of origin, area, and self-identification tags. For a regional or ethnic term, \
list the countries of origin and the tags artists use for themselves (for 'Asian': the countries of East, Southeast \
and South Asia plus tags like 'asian american'). Never invent identity requirements the user didn't ask for.
- "band", "group", "duo" mean artist_type group; "singer", "rapper", "solo" mean solo; otherwise any.
- level is 'my_level' unless the user asks for any size, famous/bigger, or smaller artists.
- suggested_artists: only real artists you are confident exist and match. Prefer independent and emerging acts. \
They will be verified against databases, so leave the list empty rather than guess."""


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return None
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception as exc:  # SDK missing or misconfigured
        log.warning("Claude unavailable: %s", exc)
        return None


def ai_available() -> bool:
    return _client() is not None


def _structured(system: str, prompt: str, schema: dict, max_tokens: int = 4000) -> dict | None:
    """One structured-output call. Returns parsed JSON or None on any failure."""
    client = _client()
    if client is None:
        return None
    import anthropic
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            # If a safety classifier declines, re-run on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        log.warning("Claude: invalid ANTHROPIC_API_KEY")
        return None
    except anthropic.RateLimitError:
        log.warning("Claude: rate limited")
        return None
    except anthropic.APIStatusError as exc:
        log.warning("Claude API error %s: %s", exc.status_code, exc.message)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude: network error")
        return None
    if response.stop_reason == "refusal":
        log.warning("Claude declined the request")
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        log.warning("Claude returned non-JSON output")
        return None


def plan_search(prompt: str) -> SearchPlan:
    data = _structured(PLAN_SYSTEM, prompt, PLAN_SCHEMA)
    if data:
        plan = SearchPlan.from_dict(data)
        plan.parsed_by = "claude"
        plan.genres = [g.lower().strip() for g in plan.genres if g.strip()]
        plan.tags = [t.lower().strip() for t in plan.tags if t.strip()]
        plan.countries = [c.upper()[:2] for c in plan.countries if len(c) >= 2]
        plan.limit = max(1, min(int(plan.limit or 20), 100))
        return plan
    return keyword_plan(prompt)


# --------------------------------------------------------------------------
# Keyword fallback (no API key)
# --------------------------------------------------------------------------

GENRE_WORDS = [
    "rock and roll", "rock & roll", "pop punk", "punk rock", "post punk", "hardcore punk", "indie rock",
    "indie pop", "alt rock", "alternative rock", "garage rock", "hard rock", "classic rock", "prog rock",
    "psychedelic rock", "shoegaze", "emo", "metalcore", "heavy metal", "death metal", "metal", "grunge",
    "punk", "rock", "dream pop", "synthpop", "hyperpop", "k-pop", "j-pop", "j-rock", "pop", "alt r&b",
    "r&b", "neo soul", "soul", "funk", "gospel", "drill", "trap", "boom bap", "hip hop", "hip-hop", "rap",
    "afrobeats", "amapiano", "dancehall", "reggaeton", "reggae", "latin trap", "latin pop", "bachata",
    "regional mexican", "corridos", "country", "americana", "folk", "singer-songwriter", "bluegrass",
    "blues", "jazz", "lofi", "lo-fi", "house", "techno", "drum and bass", "dubstep", "edm", "electronic",
    "ambient", "bedroom pop", "phonk", "jersey club", "uk garage", "grime",
]
REGIONS = {
    "asian": ("Asia / Asian diaspora", ["JP", "KR", "CN", "TW", "HK", "PH", "VN", "TH", "ID", "MY", "SG", "IN", "PK", "BD"], ["asian american", "asian"]),
    "east asian": ("East Asia", ["JP", "KR", "CN", "TW", "HK", "MN"], ["asian american"]),
    "japanese": ("Japan", ["JP"], ["japanese"]), "korean": ("Korea", ["KR"], ["korean"]),
    "chinese": ("China / Taiwan / HK", ["CN", "TW", "HK"], ["chinese"]), "filipino": ("Philippines", ["PH"], ["filipino", "opm"]),
    "indian": ("India", ["IN"], ["indian"]), "latin": ("Latin America", ["MX", "PR", "CO", "AR", "CL", "PE", "VE", "DO", "CU", "BR"], ["latin", "latino"]),
    "latino": ("Latin America", ["MX", "PR", "CO", "AR", "CL", "PE", "VE", "DO", "CU"], ["latino"]), "latina": ("Latin America", ["MX", "PR", "CO", "AR", "CL", "PE", "VE", "DO", "CU"], ["latina"]),
    "mexican": ("Mexico", ["MX"], ["mexican"]), "brazilian": ("Brazil", ["BR"], ["brazilian"]),
    "african": ("Africa", ["NG", "GH", "ZA", "KE", "TZ", "UG", "SN", "CI", "CM", "ET"], ["african"]),
    "nigerian": ("Nigeria", ["NG"], ["nigerian"]), "uk": ("UK", ["GB"], ["british"]), "british": ("UK", ["GB"], ["british"]),
    "canadian": ("Canada", ["CA"], ["canadian"]), "australian": ("Australia", ["AU"], ["australian"]),
    "japan": ("Japan", ["JP"], ["japanese"]), "korea": ("Korea", ["KR"], ["korean"]),
    "china": ("China", ["CN"], ["chinese"]), "philippines": ("Philippines", ["PH"], ["filipino", "opm"]),
    "india": ("India", ["IN"], ["indian"]), "mexico": ("Mexico", ["MX"], ["mexican"]),
    "brazil": ("Brazil", ["BR"], ["brazilian"]), "nigeria": ("Nigeria", ["NG"], ["nigerian"]),
    "canada": ("Canada", ["CA"], ["canadian"]), "australia": ("Australia", ["AU"], ["australian"]),
    "england": ("UK", ["GB"], ["british"]), "puerto rico": ("Puerto Rico", ["PR"], ["puerto rican"]),
}


def keyword_plan(prompt: str) -> SearchPlan:
    p = " " + prompt.lower() + " "
    plan = SearchPlan(summary=prompt.strip())
    if re.search(r"\b(female|woman|women|girl|girls|she|her|lady|ladies)\b", p):
        plan.gender = "female"
        if re.search(r"\b(band|group|fronted)\b", p):
            plan.tags.append("female fronted")
        else:
            plan.tags.append("female vocalists")
    elif re.search(r"\b(male|man|men|guy|guys|boy|boys)\b", p):
        plan.gender = "male"
    if re.search(r"\b(band|bands|group|groups|duo|trio|collective)\b", p):
        plan.artist_type = "group"
    elif re.search(r"\b(singers?|rappers?|solo|songwriters?|producers?|vocalists?|mcs?)\b", p):
        plan.artist_type = "solo"
    for word in sorted(GENRE_WORDS, key=len, reverse=True):
        if re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", p) and not any(word in g for g in plan.genres):
            plan.genres.append({"rock & roll": "rock and roll", "hip-hop": "hip hop", "lo-fi": "lofi"}.get(word, word))
    for word, (label, countries, tags) in sorted(REGIONS.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(word) + r"\b", p):
            plan.region_label, plan.countries, plan.identity_tags = label, countries, tags
            break
    m = re.search(r"\b(?:in|from)\s+([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?)", prompt)
    if m and m.group(1).lower() not in REGIONS and m.group(1).lower() != plan.region_label.lower():
        plan.areas = [m.group(1)]
    if re.search(r"\b(any size|famous|big|bigger|major)\b", p):
        plan.level = "bigger" if re.search(r"\b(famous|bigger|major)\b", p) else "any"
    elif re.search(r"\b(smaller|small|new|upcoming|underground)\b", p):
        plan.level = "my_level"
    m = re.search(r"\b(\d{1,3})\b", p)
    if m:
        plan.limit = max(1, min(int(m.group(1)), 100))
    return plan


# --------------------------------------------------------------------------
# Genres
# --------------------------------------------------------------------------

GENRE_SCHEMA = {
    "type": "object",
    "properties": {
        "genres": {"type": "array", "items": {"type": "string"},
                   "description": "1-3 lowercase genres, most specific first; empty if the text gives no real clue"},
    },
    "required": ["genres"],
    "additionalProperties": False,
}


def infer_genres(name: str, texts: list[str]) -> list[str]:
    """Genres for an artist the databases don't know, from their own bio/snippets."""
    text = "\n".join(t.strip() for t in texts if t and t.strip())[:4000]
    if not text:
        return []
    data = _structured(
        "You label the music genre of an artist from text they or others wrote about them "
        "(bios, link pages, search snippets). Only use clues in the text; return an empty list if there are none.",
        f"Artist: {name}\n\nText:\n{text}", GENRE_SCHEMA, max_tokens=1000)
    if data:
        return [g.lower().strip() for g in data.get("genres", []) if g.strip()][:3]
    low = text.lower()
    return [g for g in GENRE_WORDS if re.search(r"(?<![\w-])" + re.escape(g) + r"(?![\w-])", low)][:2]


# --------------------------------------------------------------------------
# Recommendations
# --------------------------------------------------------------------------

SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "artists": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "why": {"type": "string", "description": "Under 12 words"}},
            "required": ["name", "why"], "additionalProperties": False}},
    },
    "required": ["artists"],
    "additionalProperties": False,
}


def suggest_artists(liked: list[dict], disliked: list[dict], count: int = 15) -> list[dict]:
    """[{name, why}] — real artists similar to what the producer saved. Verified later."""
    if not liked:
        return []
    fmt = lambda xs: "\n".join(f"- {x['name']} ({x.get('genres') or 'genre unknown'})" for x in xs[:25]) or "(none)"
    data = _structured(
        "You recommend music artists for a mixing and mastering engineer to reach out to. Suggest real, "
        "independent or emerging artists (not household names) similar to the ones they saved and unlike the "
        "ones they passed on. Only name artists you are confident exist.",
        f"Saved:\n{fmt(liked)}\n\nPassed on:\n{fmt(disliked)}\n\nSuggest {count} artists.",
        SUGGEST_SCHEMA, max_tokens=3000)
    return (data or {}).get("artists", [])[:count]
