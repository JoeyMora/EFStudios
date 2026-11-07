"""Local web app for finding artists and working through leads.

    artist-email-finder serve        # then open http://127.0.0.1:5005
"""

from __future__ import annotations

import csv
import io
import re
from urllib.parse import quote, urlencode, urlparse

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

from .config import Settings
from .counts import followers_in, parse_count
from .extract import context_for, emails_from_text
from .jobs import JobRunner
from .level import METRICS, describe
from .models import Artist, ArtistResult, EmailResult, Finding
from .net import HttpClient
from .scoring import rank
from .sources.instagram import InstagramGraph, instagram_handle, tiktok_handle
from .sources.lastfm import LastFm
from .sources.spotify import Spotify
from .store import STATUS_LABELS, STATUSES, Store

FILTER_KEYS = ("q", "status", "level", "has_email", "min_confidence", "genre", "sort", "starred")
BIO_LINK_RE = re.compile(
    r"\b((?:https?://)?(?:www\.)?(?:linktr\.ee|beacons\.ai|lnk\.bio|hoo\.be|solo\.to|bio\.link|"
    r"stan\.store|[\w-]+\.carrd\.co|linkin\.bio|allmylinks\.com|msha\.ke|komi\.io|"
    r"[\w-]+\.(?:com|net|org|co|io|me|music|xyz|us|uk))(?:/[\w\-./?=&%]*)?)",
    re.IGNORECASE,
)


def compact(n) -> str:
    if n is None or n == "":
        return ""
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        return f"{n / 1_000:.0f}K"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def fill(template: str, lead: dict) -> str:
    first = lead["name"].split()[0] if lead["name"].split() else lead["name"]
    return (template or "").replace("{artist}", lead["name"]).replace("{first_name}", first)


def ingest_capture(store: Store, url: str, title: str, text: str) -> tuple[int, list[str], dict]:
    """Save a profile the user captured with the bookmarklet (or pasted).

    Returns (lead_id, new_emails, metrics)."""
    platform = "instagram" if instagram_handle(url) else "tiktok" if tiktok_handle(url) else "web"
    ig, tt = instagram_handle(url) or "", tiktok_handle(url) or ""
    handle = ig or tt
    # "Name (@handle) • Instagram photos and videos" / "Name (@handle) | TikTok"
    name = re.split(r"\s*\(@|\s*[|•·]\s*", title or "")[0].strip()
    if not name or name.lower() in {"instagram", "tiktok"}:
        name = handle or (urlparse(url).netloc or "Unknown artist")

    existing = store.find_lead(name=name, instagram=ig, tiktok=tt)
    source = "social_bio" if platform != "web" else "website"
    findings = [Finding(e, source, url, context_for(e, text)) for e in emails_from_text(text)]
    metrics = {}
    followers = followers_in(text)
    if followers is not None and platform != "web":
        metrics[f"{platform}_followers"] = followers
    bio_links = []
    for m in BIO_LINK_RE.finditer(text or ""):
        link = m.group(1).rstrip(".,)")
        if not any(s in link.lower() for s in ("instagram.com", "tiktok.com")):
            bio_links.append(link if link.startswith("http") else "https://" + link)

    artist_name = existing["name"] if existing else name
    artist = Artist(name=artist_name, links=[url] + bio_links, metrics=metrics,
                    discovered_via="captured" if not existing else existing["discovered_via"])
    result = ArtistResult(artist=artist, instagram=ig, tiktok=tt, metrics=metrics,
                          links=[url] + bio_links, emails=rank(findings, artist_name, set()))
    lead_id = store.save_artist(artist, result)
    return lead_id, [e.email for e in result.emails], metrics


def bookmarklet(base_url: str) -> str:
    js = (
        "(()=>{const g=s=>{const e=document.querySelector(s);return e?(e.content||e.innerText||''):''};"
        "const h=location.hostname;let t;"
        "if(/instagram\\.com|tiktok\\.com/.test(h)){"
        "const f=g('[data-e2e=\"followers-count\"]');"
        "t=[g('meta[property=\"og:description\"]'),g('meta[name=\"description\"]'),g('header'),"
        "g('[data-e2e=\"user-subtitle\"]'),g('[data-e2e=\"user-bio\"]'),f?f+' Followers':'',"
        "g('[data-e2e=\"user-link\"]')].join('\\n')}"
        "else{t=g('meta[name=\"description\"]')+'\\n'+document.body.innerText}"
        f"window.open('{base_url}capture?url='+encodeURIComponent(location.href)"
        "+'&title='+encodeURIComponent(document.title)"
        "+'&text='+encodeURIComponent(t.slice(0,5000)),'_blank')})()"
    )
    return "javascript:" + quote(js, safe="(){}[];:,'=!+.?/|&<>\\-_*\"")


def create_app(db_path: str = "leads.db", settings: Settings | None = None,
               start_jobs: bool = True) -> Flask:
    settings = settings or Settings.from_env()
    store = Store(db_path)
    runner = JobRunner(store, settings)
    if start_jobs:
        runner.start()

    app = Flask(__name__)
    app.config.update(store=store, runner=runner, settings=settings)
    app.jinja_env.filters["compact"] = compact
    app.jinja_env.filters["ratio"] = describe
    app.jinja_env.globals.update(STATUSES=STATUSES, STATUS_LABELS=STATUS_LABELS, METRICS=METRICS)

    def current_filters() -> dict:
        f = {k: request.args.get(k, "") for k in FILTER_KEYS}
        if "status" not in request.args:
            f["status"] = "active"
        if "level" not in request.args:
            f["level"] = "match+unknown" if store.my_metrics() else ""
        f["sort"] = f["sort"] or ("level" if store.my_metrics() else "newest")
        return f

    # -- leads ------------------------------------------------------------------
    @app.get("/")
    def leads():
        f = current_filters()
        page = max(1, request.args.get("page", 1, type=int))
        per_page = 100
        rows, total = store.leads(**{**f, "starred": f["starred"] == "1"},
                                  limit=per_page, offset=(page - 1) * per_page)
        args = {k: v for k, v in f.items() if v}
        return render_template("leads.html", leads=rows, total=total, f=f, page=page,
                               pages=max(1, -(-total // per_page)), counts=store.counts(),
                               has_level=bool(store.my_metrics()), args=args,
                               export_url=url_for("export") + "?" + urlencode(args))

    @app.get("/export.csv")
    def export():
        f = current_filters()
        rows, _ = store.leads(**{**f, "starred": f["starred"] == "1"}, limit=100_000)
        buf = io.StringIO()
        cols = ["name", "best_email", "best_confidence", "status", "level", "instagram",
                "tiktok", "website", "spotify_url", *METRICS, "genres", "location", "notes"]
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({**{k: r.get(k, "") for k in cols if k in r},
                        **{k: r["metrics"].get(k, "") for k in METRICS},
                        "level": describe(r["level_ratio"])})
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=artist-leads.csv"})

    @app.post("/leads/bulk")
    def bulk():
        ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
        action = request.form.get("action", "")
        if ids:
            if action in STATUSES:
                for i in ids:
                    store.update_lead(i, status=action)
            elif action == "recheck":
                runner.submit("recheck", {"lead_ids": ids})
            elif action == "delete":
                for i in ids:
                    store.delete_lead(i)
        return redirect(request.form.get("next") or url_for("leads"))

    @app.get("/lead/<int:lead_id>")
    def lead(lead_id: int):
        row = store.lead(lead_id)
        if not row:
            abort(404)
        s = store.settings()
        mailto = {}
        for e in row["emails"]:
            mailto[e["id"]] = "mailto:{}?{}".format(e["email"], urlencode(
                {"subject": fill(s["email_subject"], row), "body": fill(s["email_body"], row)},
                quote_via=quote))
        return render_template("lead.html", lead=row, mailto=mailto,
                               my=store.my_metrics(), back=request.args.get("back", ""))

    @app.post("/api/lead/<int:lead_id>")
    def api_update_lead(lead_id: int):
        data = request.get_json(force=True)
        if "starred" in data:
            data["starred"] = 1 if data["starred"] else 0
        store.update_lead(lead_id, **data)
        return jsonify(ok=True)

    @app.post("/lead/<int:lead_id>")
    def update_lead(lead_id: int):
        form = request.form
        fields = {k: form[k].strip() for k in ("status", "notes", "name", "website") if k in form}
        for key, parse in (("instagram", instagram_handle), ("tiktok", tiktok_handle)):
            if key in form:
                value = form[key].strip()
                if "/" in value:
                    value = parse(value) or ""
                fields[key] = value.lstrip("@").lower()
        store.update_lead(lead_id, **fields)
        metrics = {}
        for key in METRICS:
            if key in form and form[key].strip():
                count = parse_count(form[key])
                if count is not None:
                    metrics[key] = count
        if metrics:
            store.update_metrics(lead_id, metrics)
        return redirect(url_for("lead", lead_id=lead_id, back=form.get("back", "")))

    @app.post("/lead/<int:lead_id>/email")
    def add_email(lead_id: int):
        for email in emails_from_text(request.form.get("email", "")):
            store.add_emails(lead_id, [EmailResult(email, 100, "high", ["manual"], [], "added by you")])
        return redirect(url_for("lead", lead_id=lead_id))

    @app.post("/lead/<int:lead_id>/email/<int:email_id>/delete")
    def delete_email(lead_id: int, email_id: int):
        store.delete_email(lead_id, email_id)
        return redirect(url_for("lead", lead_id=lead_id))

    @app.post("/lead/<int:lead_id>/paste")
    def paste_bio(lead_id: int):
        row = store.lead(lead_id)
        text = request.form.get("text", "")
        platform = request.form.get("platform", "instagram")
        handle = row["instagram"] if platform == "instagram" else row["tiktok"]
        url = (f"https://www.instagram.com/{handle}/" if platform == "instagram" and handle
               else f"https://www.tiktok.com/@{handle}" if handle else f"pasted:{platform}")
        findings = [Finding(e, "social_bio", url, context_for(e, text)) for e in emails_from_text(text)]
        store.add_emails(lead_id, rank(findings, row["name"], set()))
        followers = followers_in(text)
        if followers is not None:
            store.update_metrics(lead_id, {f"{platform}_followers": followers})
        return redirect(url_for("lead", lead_id=lead_id))

    @app.post("/lead/<int:lead_id>/recheck")
    def recheck(lead_id: int):
        runner.submit("recheck", {"lead_ids": [lead_id]})
        return redirect(url_for("lead", lead_id=lead_id, checking=1))

    @app.post("/lead/<int:lead_id>/delete")
    def delete_lead(lead_id: int):
        store.delete_lead(lead_id)
        return redirect(request.form.get("back") or url_for("leads"))

    # -- finding artists --------------------------------------------------------
    @app.get("/find")
    def find():
        return render_template("find.html", status=settings.status(), s=settings,
                               has_level=bool(store.my_metrics()), my=store.my_metrics(),
                               jobs=store.jobs())

    @app.post("/find/discover")
    def discover():
        form = request.form
        params = {
            "source": form.get("source"), "genre": form.get("genre", ""),
            "area": form.get("area", ""), "seed": form.get("seed", ""),
            "limit": form.get("limit", type=int, default=25),
            "only_my_level": form.get("only_my_level") == "on",
            "find_emails": form.get("find_emails") == "on",
        }
        runner.submit("discover", params)
        return redirect(url_for("find"))

    @app.post("/find/lookup")
    def lookup():
        lines = [l.strip() for l in request.form.get("lines", "").splitlines() if l.strip()]
        if lines:
            runner.submit("lookup", {"lines": lines})
        return redirect(url_for("find"))

    @app.get("/api/jobs")
    def api_jobs():
        return jsonify(store.jobs(10))

    @app.post("/jobs/<int:job_id>/cancel")
    def cancel_job(job_id: int):
        runner.cancel(job_id)
        return redirect(request.form.get("next") or url_for("find"))

    # -- capture (bookmarklet) --------------------------------------------------
    @app.route("/capture", methods=["GET", "POST"])
    def capture():
        values = request.values
        url, text = values.get("url", ""), values.get("text", "")
        if not url:
            return redirect(url_for("settings_page") + "#capture")
        lead_id, emails, metrics = ingest_capture(store, url, values.get("title", ""), text)
        row = store.lead(lead_id)
        queued = False
        if not emails and any(l for l in row["links"] if "instagram.com" not in l
                              and "tiktok.com" not in l):
            runner.submit("recheck", {"lead_ids": [lead_id]})  # follow their bio link
            queued = True
        return render_template("captured.html", lead=row, emails=emails, metrics=metrics,
                               queued=queued)

    # -- settings ---------------------------------------------------------------
    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        message = ""
        if request.method == "POST":
            values = {}
            for key in METRICS:
                raw = request.form.get(f"my_{key}", "").strip()
                count = parse_count(raw) if raw else None
                values[f"my_{key}"] = str(count) if count is not None else ""
            for key in ("level_low", "level_high", "email_subject", "email_body",
                        "my_instagram", "my_spotify_url", "my_lastfm_name"):
                if key in request.form:
                    values[key] = request.form[key].strip()
            store.save_settings(values)
            message = "Saved."
        base = request.host_url
        return render_template("settings.html", s=store.settings(), status=settings.status(),
                               message=message, bookmarklet=bookmarklet(base))

    @app.post("/settings/autofill")
    def autofill():
        """Fill my own numbers from my Instagram / Spotify / Last.fm."""
        s = store.settings()
        http = HttpClient()
        values, notes = {}, []
        ig = (request.form.get("my_instagram") or "").strip().lstrip("@")
        sp_url = (request.form.get("my_spotify_url") or "").strip()
        lf = (request.form.get("my_lastfm_name") or "").strip()
        values.update(my_instagram=ig, my_spotify_url=sp_url, my_lastfm_name=lf)
        if ig and settings.has_instagram:
            prof = InstagramGraph(http, settings.meta_access_token, settings.ig_user_id,
                                  settings.graph_api_version).profile(ig)
            if prof and prof.get("followers_count") is not None:
                values["my_instagram_followers"] = str(prof["followers_count"])
            else:
                notes.append("Instagram lookup failed")
        if sp_url and settings.has_spotify:
            m = re.search(r"artist/([A-Za-z0-9]+)", sp_url)
            art = Spotify(http, settings.spotify_client_id,
                          settings.spotify_client_secret).artist(m.group(1)) if m else None
            if art and "spotify_followers" in art.metrics:
                values["my_spotify_followers"] = str(art.metrics["spotify_followers"])
            else:
                notes.append("Spotify lookup failed")
        if lf and settings.lastfm_api_key:
            info = LastFm(http, settings.lastfm_api_key).info(lf)
            if info.get("listeners") is not None:
                values["my_lastfm_listeners"] = str(info["listeners"])
            else:
                notes.append("Last.fm lookup failed")
        store.save_settings({**{k: v for k, v in s.items() if k.startswith("my_")}, **values})
        return redirect(url_for("settings_page", note="; ".join(notes)))

    return app
