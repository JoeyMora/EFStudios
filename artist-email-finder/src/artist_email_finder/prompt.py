"""Understand a search prompt ("find me a female punk rock band") without AI.

A rule-based parser turns plain English into a SearchPlan: genres, descriptor
tags, gender, solo/group, region, city, level, "like <artist>", trending, and
how many. Identity words are only matched against what artists publish about
themselves (MusicBrainz gender/type/country/area and self-identification
tags); nothing is inferred from names or photos.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass
class SearchPlan:
    summary: str = ""
    genres: list[str] = field(default_factory=list)        # e.g. ["punk rock"]
    tags: list[str] = field(default_factory=list)          # extra descriptor tags, e.g. ["female fronted"]
    gender: str = "any"                                    # female | male | nonbinary | any
    artist_type: str = "any"                               # solo | group | any
    countries: list[str] = field(default_factory=list)     # ISO 3166-1 alpha-2
    identity_tags: list[str] = field(default_factory=list) # self-described tags, e.g. ["asian american"]
    region_label: str = ""
    areas: list[str] = field(default_factory=list)         # cities, e.g. ["Atlanta"]
    level: str = "my_level"                                # my_level | any | bigger | smaller
    similar_to: str = ""                                   # "like <artist>"
    trending: bool = False                                 # "trending", "blowing up"
    limit: int = 20
    parsed_by: str = "keywords"

    @classmethod
    def from_dict(cls, d: dict) -> "SearchPlan":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def to_dict(self) -> dict:
        return asdict(self)

    def chips(self) -> list[str]:
        """Short labels describing how the prompt was understood."""
        out = []
        if self.gender != "any":
            out.append({"female": "Female", "male": "Male", "nonbinary": "Non-binary"}[self.gender])
        if self.artist_type != "any":
            out.append("Band / group" if self.artist_type == "group" else "Solo artist")
        if self.region_label:
            out.append(self.region_label)
        out += self.areas
        out += self.genres
        out += [t for t in self.tags if t not in self.genres][:3]
        if self.similar_to:
            out.append(f"Like {self.similar_to}")
        if self.trending:
            out.append("Rising")
        if self.level != "any":
            out.append({"my_level": "At my level", "bigger": "Bigger than me", "smaller": "Smaller than me"}[self.level])
        return out


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

# Canonical genre names as music databases use them, with common spellings.
GENRES: dict[str, list[str]] = {
    "rock and roll": ["rock and roll", "rock n roll", "rock 'n' roll", "rock'n'roll", "rock & roll", "rocknroll"],
    "pop punk": ["pop punk", "pop-punk"], "punk rock": ["punk rock"], "post-punk": ["post punk", "post-punk"],
    "hardcore punk": ["hardcore punk", "hardcore"], "riot grrrl": ["riot grrrl", "riot grrl", "riot girl"],
    "indie rock": ["indie rock"], "indie pop": ["indie pop"], "indie folk": ["indie folk"],
    "alternative rock": ["alt rock", "alternative rock", "alt-rock"], "garage rock": ["garage rock", "garage"],
    "hard rock": ["hard rock"], "classic rock": ["classic rock"], "psychedelic rock": ["psych rock", "psychedelic rock", "psychedelic"],
    "progressive rock": ["prog rock", "progressive rock", "prog"], "shoegaze": ["shoegaze"], "dream pop": ["dream pop"],
    "emo": ["emo", "midwest emo"], "metalcore": ["metalcore"], "death metal": ["death metal"], "heavy metal": ["heavy metal"],
    "metal": ["metal"], "grunge": ["grunge"], "surf rock": ["surf rock", "surf"], "punk": ["punk"], "rock": ["rock"],
    "synthpop": ["synthpop", "synth pop", "synth-pop"], "hyperpop": ["hyperpop"], "bedroom pop": ["bedroom pop"],
    "k-pop": ["k-pop", "kpop"], "j-pop": ["j-pop", "jpop"], "j-rock": ["j-rock", "jrock"], "dance pop": ["dance pop"],
    "pop": ["pop"], "alternative r&b": ["alt r&b", "alternative r&b", "alt rnb"],
    "r&b": ["r&b", "rnb", "r and b", "rhythm and blues"], "neo soul": ["neo soul", "neo-soul"], "soul": ["soul"],
    "funk": ["funk"], "gospel": ["gospel"], "drill": ["drill", "uk drill", "ny drill"], "trap": ["trap"],
    "boom bap": ["boom bap"], "hip hop": ["hip hop", "hip-hop", "hiphop"], "rap": ["rap", "rapper", "rappers"],
    "afrobeats": ["afrobeats", "afrobeat", "afro beats", "afropop"], "amapiano": ["amapiano"], "dancehall": ["dancehall"],
    "reggaeton": ["reggaeton", "regueton"], "reggae": ["reggae"], "latin trap": ["latin trap"], "latin pop": ["latin pop"],
    "bachata": ["bachata"], "salsa": ["salsa"], "cumbia": ["cumbia"], "corridos tumbados": ["corridos tumbados", "corridos"],
    "regional mexican": ["regional mexican", "regional mexicano"], "country": ["country"], "americana": ["americana"],
    "folk": ["folk"], "singer-songwriter": ["singer songwriter", "singer-songwriter"], "bluegrass": ["bluegrass"],
    "blues": ["blues"], "jazz": ["jazz"], "lo-fi": ["lofi", "lo-fi", "lo fi"], "house": ["house"], "techno": ["techno"],
    "drum and bass": ["drum and bass", "dnb", "d&b", "drum n bass"], "dubstep": ["dubstep"], "edm": ["edm"],
    "electronic": ["electronic", "electronica"], "ambient": ["ambient"], "phonk": ["phonk"], "jersey club": ["jersey club"],
    "uk garage": ["uk garage", "ukg"], "grime": ["grime"], "disco": ["disco"], "ska": ["ska"], "worship": ["worship", "christian"],
}
_ALIASES = sorted(((alias, canon) for canon, aliases in GENRES.items() for alias in aliases),
                  key=lambda x: -len(x[0]))
GENRE_WORDS = list(GENRES)

REGIONS = {
    "asian american": ("Asian American", ["US"], ["asian american"]),
    "asian": ("Asia / Asian diaspora", ["JP", "KR", "CN", "TW", "HK", "PH", "VN", "TH", "ID", "MY", "SG", "IN", "PK", "BD"], ["asian american", "asian"]),
    "east asian": ("East Asia", ["JP", "KR", "CN", "TW", "HK", "MN"], ["asian american"]),
    "southeast asian": ("Southeast Asia", ["PH", "VN", "TH", "ID", "MY", "SG"], ["asian american"]),
    "south asian": ("South Asia", ["IN", "PK", "BD", "LK", "NP"], ["desi"]),
    "desi": ("South Asia", ["IN", "PK", "BD", "LK", "NP"], ["desi"]),
    "japanese": ("Japan", ["JP"], ["japanese"]), "japan": ("Japan", ["JP"], ["japanese"]),
    "korean": ("Korea", ["KR"], ["korean"]), "korea": ("Korea", ["KR"], ["korean"]),
    "chinese": ("China / Taiwan / HK", ["CN", "TW", "HK"], ["chinese"]), "china": ("China", ["CN"], ["chinese"]),
    "filipino": ("Philippines", ["PH"], ["filipino", "opm"]), "philippines": ("Philippines", ["PH"], ["filipino", "opm"]),
    "indian": ("India", ["IN"], ["indian"]), "india": ("India", ["IN"], ["indian"]),
    "latin": ("Latin America", ["MX", "PR", "CO", "AR", "CL", "PE", "VE", "DO", "CU", "BR"], ["latin", "latino"]),
    "latino": ("Latin America", ["MX", "PR", "CO", "AR", "CL", "PE", "VE", "DO", "CU"], ["latino"]),
    "latina": ("Latin America", ["MX", "PR", "CO", "AR", "CL", "PE", "VE", "DO", "CU"], ["latina"]),
    "latinx": ("Latin America", ["MX", "PR", "CO", "AR", "CL", "PE", "VE", "DO", "CU"], ["latinx"]),
    "mexican": ("Mexico", ["MX"], ["mexican"]), "mexico": ("Mexico", ["MX"], ["mexican"]),
    "puerto rican": ("Puerto Rico", ["PR"], ["puerto rican"]), "puerto rico": ("Puerto Rico", ["PR"], ["puerto rican"]),
    "brazilian": ("Brazil", ["BR"], ["brazilian"]), "brazil": ("Brazil", ["BR"], ["brazilian"]),
    "african": ("Africa", ["NG", "GH", "ZA", "KE", "TZ", "UG", "SN", "CI", "CM", "ET"], ["african"]),
    "nigerian": ("Nigeria", ["NG"], ["nigerian"]), "nigeria": ("Nigeria", ["NG"], ["nigerian"]),
    "ghanaian": ("Ghana", ["GH"], ["ghanaian"]), "south african": ("South Africa", ["ZA"], ["south african"]),
    "british": ("UK", ["GB"], ["british"]), "uk": ("UK", ["GB"], ["british"]), "england": ("UK", ["GB"], ["british"]),
    "irish": ("Ireland", ["IE"], ["irish"]), "canadian": ("Canada", ["CA"], ["canadian"]), "canada": ("Canada", ["CA"], ["canadian"]),
    "australian": ("Australia", ["AU"], ["australian"]), "australia": ("Australia", ["AU"], ["australian"]),
    "french": ("France", ["FR"], ["french"]), "german": ("Germany", ["DE"], ["german"]),
    "swedish": ("Sweden", ["SE"], ["swedish"]), "caribbean": ("Caribbean", ["JM", "TT", "BB", "BS", "HT", "DO", "PR", "CU"], ["caribbean"]),
    "jamaican": ("Jamaica", ["JM"], ["jamaican"]),
}

STOP_AREA = {"the", "a", "an", "my", "our", "their", "town", "style", "general"}


def _has(p: str, pattern: str) -> bool:
    return re.search(pattern, p) is not None


def plan_search(prompt: str) -> SearchPlan:
    return keyword_plan(prompt)


def keyword_plan(prompt: str) -> SearchPlan:
    raw = prompt.strip()
    p = " " + raw.lower() + " "
    plan = SearchPlan(summary=raw)

    # "like Phoebe Bridgers", "similar to Alvvays", "sounds like X" (before other words get matched)
    m = re.search(r"\b(?:similar to|sounds? like|in the vein of|(?<!'d )(?<!would )like)\s+([A-Z0-9][\w.'&$!-]*(?:\s+(?:[A-Z0-9][\w.'&$!-]*|of|the|and|&))*)", raw)
    if m:
        plan.similar_to = re.sub(r"\s+(?:but|at|in|from|with|who|that)$", "", m.group(1).strip())
        p = p.replace(m.group(0).lower(), " ")

    # Gender (as published)
    if _has(p, r"\b(female|females|woman|women|girl|girls|she|her|lady|ladies|all-girl|women-led|female-fronted|female fronted)\b"):
        plan.gender = "female"
    elif _has(p, r"\b(male|males|man|men|guy|guys|boy|boys|he|his)\b"):
        plan.gender = "male"
    elif _has(p, r"\b(non-binary|nonbinary|enby|queer)\b"):
        plan.gender = "nonbinary"

    # Solo vs group
    if _has(p, r"\b(band|bands|group|groups|duo|duos|trio|trios|collective|quartet|crew)\b"):
        plan.artist_type = "group"
    elif _has(p, r"\b(singers?|rappers?|solo|songwriters?|vocalists?|mcs?|artist who|soloist)\b"):
        plan.artist_type = "solo"
    if plan.gender == "female":
        plan.tags.append("female fronted" if plan.artist_type == "group" else "female vocalists")

    # Genres (longest alias first, so "pop punk" wins over "pop" and "punk")
    plan.genres = _find_genres(p)
    if "rap" in plan.genres and "hip hop" not in plan.genres:
        plan.genres[plan.genres.index("rap")] = "hip hop"

    # Region / identity
    for word, (label, countries, tags) in sorted(REGIONS.items(), key=lambda kv: -len(kv[0])):
        if _has(p, r"\b" + re.escape(word) + r"\b"):
            plan.region_label, plan.countries, plan.identity_tags = label, list(countries), list(tags)
            break

    # City: "in Atlanta", "from New Orleans", "based in LA", "Chicago-based"
    for m in re.finditer(r"\b(?:in|from|based in|out of|around)\s+([A-Z][A-Za-z.]+(?:\s[A-Z][A-Za-z.]+){0,2})", raw):
        city = m.group(1).strip()
        if city.lower() not in REGIONS and city.lower() not in STOP_AREA and city.lower() != plan.region_label.lower():
            plan.areas = [city]
            break
    m = re.search(r"\b([A-Z][A-Za-z.]+(?:\s[A-Z][A-Za-z.]+)?)-based\b", raw)
    if m and not plan.areas:
        plan.areas = [m.group(1)]

    # Level & momentum
    if _has(p, r"\b(any size|any level|anyone)\b"):
        plan.level = "any"
    elif _has(p, r"\b(famous|bigger|big names?|major|established|popular)\b"):
        plan.level = "bigger"
    elif _has(p, r"\b(smaller|tiny|brand new|beginners?|starting out)\b"):
        plan.level = "smaller"
    if _has(p, r"\b(trending|rising|blowing up|buzzing|hot|up and coming|up-and-coming|viral|on the rise|momentum)\b"):
        plan.trending = True

    m = re.search(r"\b(\d{1,3})\b", p)
    if m:
        plan.limit = max(1, min(int(m.group(1)), 100))
    return plan


def _find_genres(text: str) -> list[str]:
    """Genres named in text, in the order they appear. Longer names win
    ("pop punk" is not also "pop" and "punk")."""
    hits = []
    for alias, canon in _ALIASES:
        pat = r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])"
        for m in re.finditer(pat, text):
            hits.append((m.start(), canon))
        text = re.sub(pat, lambda m: " " * len(m.group(0)), text)  # keep positions stable
    out = []
    for _, canon in sorted(hits):
        if canon not in out:
            out.append(canon)
    return out


def infer_genres(name: str, texts: list[str]) -> list[str]:
    """Genres named in an artist's own bio or snippets (keyword match)."""
    return _find_genres(" " + " ".join(t for t in texts if t).lower() + " ")[:3]
