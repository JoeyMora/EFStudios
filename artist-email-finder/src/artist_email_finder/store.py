"""SQLite storage for leads, the emails found for them, settings, and jobs."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from .level import ENGAGEMENT, bucket, ratio
from .models import Artist, ArtistResult, EmailResult
from .scoring import label

STATUSES = ["new", "shortlist", "contacted", "replied", "not_a_fit"]
STATUS_LABELS = {"new": "New", "shortlist": "Shortlist", "contacted": "Contacted",
                 "replied": "Replied", "not_a_fit": "Not a fit"}

DEFAULT_SETTINGS = {
    "level_low": "0.25",     # down to 1/4 of my size
    "level_high": "4",       # up to 4x my size
    "email_subject": "Mixing/mastering for {artist}",
    "email_body": ("Hey {artist},\n\nI've been listening to your music and really "
                   "like what you're doing.\n\nI'm Joey, a mix & master engineer. "
                   "I'd love to work on your next release.\n\n- Joey"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    genres TEXT DEFAULT '',
    location TEXT DEFAULT '',
    spotify_url TEXT DEFAULT '',
    instagram TEXT DEFAULT '',
    tiktok TEXT DEFAULT '',
    website TEXT DEFAULT '',
    links TEXT DEFAULT '[]',
    metrics TEXT DEFAULT '{}',
    discovered_via TEXT DEFAULT '',
    image_url TEXT DEFAULT '',
    status TEXT DEFAULT 'new',
    starred INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    best_email TEXT DEFAULT '',
    best_confidence TEXT DEFAULT '',
    best_score INTEGER DEFAULT 0,
    level_ratio REAL,
    level_bucket TEXT DEFAULT 'unknown',
    last_checked REAL,
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    score INTEGER DEFAULT 0,
    confidence TEXT DEFAULT '',
    sources TEXT DEFAULT '',
    urls TEXT DEFAULT '',
    context TEXT DEFAULT '',
    UNIQUE(lead_id, email)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT,
    params TEXT,
    status TEXT DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    created_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS leads_status ON leads(status);
"""


def _absln(x):
    return abs(math.log(x)) if x and x > 0 else None


def name_key(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", folded) or name.lower().strip()


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self.conn() as c:
            c.executescript(SCHEMA)
            # Columns added after the first release.
            cols = {r["name"] for r in c.execute("PRAGMA table_info(leads)")}
            if "image_url" not in cols:
                c.execute("ALTER TABLE leads ADD COLUMN image_url TEXT DEFAULT ''")

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.create_function("ABSLN", 1, _absln, deterministic=True)
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # -- settings -----------------------------------------------------------
    def settings(self) -> dict[str, str]:
        with self.conn() as c:
            rows = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
        return {**DEFAULT_SETTINGS, **rows}

    def save_settings(self, values: dict[str, str]) -> None:
        with self.conn() as c:
            c.executemany("INSERT INTO settings(key, value) VALUES(?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          list(values.items()))
        self.recompute_levels()

    def my_metrics(self) -> dict[str, int]:
        s = self.settings()
        out = {}
        for key in ENGAGEMENT:
            value = (s.get(f"my_{key}") or "").replace(",", "").strip()
            if value.isdigit() and int(value) > 0:
                out[key] = int(value)
        return out

    def band(self) -> tuple[float, float]:
        s = self.settings()
        try:
            return float(s["level_low"]), float(s["level_high"])
        except ValueError:
            return 0.25, 4.0

    def level_of(self, metrics: dict) -> tuple[float | None, str]:
        r = ratio(metrics, self.my_metrics())
        low, high = self.band()
        return r, bucket(r, low, high)

    def recompute_levels(self) -> None:
        with self.conn() as c:
            rows = c.execute("SELECT id, metrics FROM leads").fetchall()
            mine = self.my_metrics()
            low, high = self.band()
            for row in rows:
                r = ratio(json.loads(row["metrics"] or "{}"), mine)
                c.execute("UPDATE leads SET level_ratio = ?, level_bucket = ? WHERE id = ?",
                          (r, bucket(r, low, high), row["id"]))

    # -- leads --------------------------------------------------------------
    def find_lead(self, *, name: str = "", instagram: str = "", tiktok: str = "") -> sqlite3.Row | None:
        with self.conn() as c:
            if instagram:
                row = c.execute("SELECT * FROM leads WHERE instagram = ?", (instagram.lower(),)).fetchone()
                if row:
                    return row
            if tiktok:
                row = c.execute("SELECT * FROM leads WHERE tiktok = ?", (tiktok.lower(),)).fetchone()
                if row:
                    return row
            if name:
                return c.execute("SELECT * FROM leads WHERE name_key = ?", (name_key(name),)).fetchone()
        return None

    def has_lead(self, name: str) -> bool:
        return self.find_lead(name=name) is not None

    def save_artist(self, artist: Artist, result: ArtistResult | None = None) -> int:
        """Insert or merge a lead. Never touches status, notes or star."""
        now = time.time()
        metrics = dict(artist.metrics)
        links = list(artist.links)
        fields = {
            "genres": "; ".join(artist.genres), "location": artist.location,
            "spotify_url": artist.spotify_url, "discovered_via": artist.discovered_via,
            "image_url": artist.image_url,
        }
        if result:
            metrics.update({k: v for k, v in result.metrics.items() if v is not None})
            links = result.links or links
            fields.update(instagram=result.instagram.lower(), tiktok=result.tiktok.lower(),
                          website=result.website)
            if result.image_url:
                fields["image_url"] = result.image_url

        existing = self.find_lead(name=artist.name, instagram=fields.get("instagram", ""),
                                  tiktok=fields.get("tiktok", ""))
        with self.conn() as c:
            if existing:
                lead_id = existing["id"]
                old_metrics = json.loads(existing["metrics"] or "{}")
                old_links = json.loads(existing["links"] or "[]")
                metrics = {**old_metrics, **metrics}
                links = old_links + [l for l in links if l not in old_links]
                # Keep existing values when the new run didn't find one.
                updates = {k: v for k, v in fields.items() if v}
                updates.update(links=json.dumps(links), metrics=json.dumps(metrics), updated_at=now)
                if result:
                    updates["last_checked"] = now
                sets = ", ".join(f"{k} = ?" for k in updates)
                c.execute(f"UPDATE leads SET {sets} WHERE id = ?", [*updates.values(), lead_id])
            else:
                cur = c.execute(
                    "INSERT INTO leads(name, name_key, genres, location, spotify_url, instagram, "
                    "tiktok, website, links, metrics, discovered_via, last_checked, created_at, "
                    "updated_at, image_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (artist.name, name_key(artist.name), fields["genres"], fields["location"],
                     fields["spotify_url"], fields.get("instagram", ""), fields.get("tiktok", ""),
                     fields.get("website", ""), json.dumps(links), json.dumps(metrics),
                     fields["discovered_via"], now if result else None, now, now,
                     fields.get("image_url", "")))
                lead_id = cur.lastrowid
        if result:
            self.add_emails(lead_id, result.emails)
        self._refresh(lead_id)
        return lead_id

    def add_emails(self, lead_id: int, emails: list[EmailResult]) -> None:
        with self.conn() as c:
            for e in emails:
                row = c.execute("SELECT * FROM emails WHERE lead_id = ? AND email = ?",
                                (lead_id, e.email)).fetchone()
                if row:
                    sources = sorted(set(row["sources"].split("; ")) | set(e.sources) - {""})
                    urls = row["urls"].split(" ") + [u for u in e.urls if u not in row["urls"]]
                    score = max(row["score"], e.score)
                    c.execute("UPDATE emails SET score = ?, confidence = ?, sources = ?, urls = ?, "
                              "context = COALESCE(NULLIF(context, ''), ?) WHERE id = ?",
                              (score, label(score), "; ".join(sources),
                               " ".join(u for u in urls if u), e.context, row["id"]))
                else:
                    c.execute("INSERT INTO emails(lead_id, email, score, confidence, sources, urls, "
                              "context) VALUES(?,?,?,?,?,?,?)",
                              (lead_id, e.email, e.score, e.confidence, "; ".join(e.sources),
                               " ".join(e.urls), e.context))
        self._refresh(lead_id)

    def update_metrics(self, lead_id: int, metrics: dict) -> None:
        with self.conn() as c:
            row = c.execute("SELECT metrics FROM leads WHERE id = ?", (lead_id,)).fetchone()
            merged = {**json.loads(row["metrics"] or "{}"),
                      **{k: v for k, v in metrics.items() if v is not None}}
            c.execute("UPDATE leads SET metrics = ?, updated_at = ? WHERE id = ?",
                      (json.dumps(merged), time.time(), lead_id))
        self._refresh(lead_id)

    def _refresh(self, lead_id: int) -> None:
        """Recompute the cached best email and level for one lead."""
        with self.conn() as c:
            best = c.execute("SELECT email, confidence, score FROM emails WHERE lead_id = ? "
                             "ORDER BY score DESC, email LIMIT 1", (lead_id,)).fetchone()
            row = c.execute("SELECT metrics FROM leads WHERE id = ?", (lead_id,)).fetchone()
            if not row:
                return
            r, b = self.level_of(json.loads(row["metrics"] or "{}"))
            c.execute("UPDATE leads SET best_email = ?, best_confidence = ?, best_score = ?, "
                      "level_ratio = ?, level_bucket = ? WHERE id = ?",
                      (best["email"] if best else "", best["confidence"] if best else "",
                       best["score"] if best else 0, r, b, lead_id))

    def update_lead(self, lead_id: int, **fields) -> None:
        allowed = {"status", "notes", "starred", "name", "instagram", "tiktok", "website"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError("bad status")
        if not fields:
            return
        fields["updated_at"] = time.time()
        with self.conn() as c:
            sets = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE leads SET {sets} WHERE id = ?", [*fields.values(), lead_id])

    def delete_email(self, lead_id: int, email_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM emails WHERE id = ? AND lead_id = ?", (email_id, lead_id))
        self._refresh(lead_id)

    def delete_lead(self, lead_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM leads WHERE id = ?", (lead_id,))

    def lead(self, lead_id: int) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
            if not row:
                return None
            emails = c.execute("SELECT * FROM emails WHERE lead_id = ? ORDER BY score DESC, email",
                               (lead_id,)).fetchall()
        return {**_lead_dict(row), "emails": [dict(e) for e in emails]}

    def leads(self, *, q: str = "", status: str = "", level: str = "", has_email: str = "",
              min_confidence: str = "", genre: str = "", starred: bool = False,
              sort: str = "level", limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        where, args = [], []
        if q:
            where.append("(name LIKE ? OR best_email LIKE ? OR instagram LIKE ? OR tiktok LIKE ? "
                         "OR notes LIKE ?)")
            args += [f"%{q}%"] * 5
        if status == "active":
            where.append("status != 'not_a_fit'")
        elif status:
            where.append("status = ?")
            args.append(status)
        if level == "match+unknown":
            where.append("level_bucket IN ('match', 'unknown')")
        elif level:
            where.append("level_bucket = ?")
            args.append(level)
        if has_email == "yes":
            where.append("best_email != ''")
        elif has_email == "no":
            where.append("best_email = ''")
        if min_confidence == "high":
            where.append("best_score >= 80")
        elif min_confidence == "medium":
            where.append("best_score >= 55")
        if genre:
            where.append("genres LIKE ?")
            args.append(f"%{genre}%")
        if starred:
            where.append("starred = 1")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        order = {
            # Closest to my size first (|log ratio|), unknown sizes last.
            "level": "level_ratio IS NULL, ABSLN(level_ratio), best_score DESC",
            "email": "best_score DESC, name COLLATE NOCASE",
            "name": "name COLLATE NOCASE",
            "newest": "created_at DESC",
            "updated": "updated_at DESC",
            "biggest": "level_ratio IS NULL, level_ratio DESC",
            "smallest": "level_ratio IS NULL, level_ratio ASC",
        }.get(sort, "created_at DESC")
        with self.conn() as c:
            total = c.execute(f"SELECT COUNT(*) FROM leads {clause}", args).fetchone()[0]
            rows = c.execute(f"SELECT * FROM leads {clause} ORDER BY starred DESC, {order} "
                             f"LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return [_lead_dict(r) for r in rows], total

    def counts(self) -> dict[str, int]:
        with self.conn() as c:
            out = {r["status"]: r["n"] for r in
                   c.execute("SELECT status, COUNT(*) n FROM leads GROUP BY status")}
            out["all"] = sum(out.values())
            out["with_email"] = c.execute(
                "SELECT COUNT(*) FROM leads WHERE best_email != ''").fetchone()[0]
            for r in c.execute("SELECT level_bucket, COUNT(*) n FROM leads GROUP BY level_bucket"):
                out[f"level_{r['level_bucket']}"] = r["n"]
        return out

    # -- jobs -----------------------------------------------------------------
    def create_job(self, kind: str, params: dict) -> int:
        with self.conn() as c:
            return c.execute("INSERT INTO jobs(kind, params, created_at) VALUES(?,?,?)",
                             (kind, json.dumps(params), time.time())).lastrowid

    def update_job(self, job_id: int, **fields) -> None:
        if fields.get("status") in {"done", "failed", "cancelled"}:
            fields["finished_at"] = time.time()
        with self.conn() as c:
            sets = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*fields.values(), job_id])

    def job(self, job_id: int) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_dict(row) if row else None

    def jobs(self, limit: int = 20) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_job_dict(r) for r in rows]

    def fail_stale_jobs(self) -> None:
        """Jobs left 'running' by a previous app run were interrupted."""
        with self.conn() as c:
            c.execute("UPDATE jobs SET status = 'cancelled', message = 'interrupted (app restarted)' "
                      "WHERE status IN ('queued', 'running')")


def _lead_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["links"] = json.loads(d.get("links") or "[]")
    d["metrics"] = json.loads(d.get("metrics") or "{}")
    return d


def _job_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["params"] = json.loads(d.get("params") or "{}")
    return d
