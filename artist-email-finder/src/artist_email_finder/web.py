"""Local web app for finding artists and working through leads.

    artist-email-finder serve        # then open http://127.0.0.1:5005
"""

from __future__ import annotations

import csv
import io
import math
import re
from urllib.parse import quote, urlencode, urlparse

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

from .prompt import SearchPlan, plan_search
from .config import Settings
from .counts import followers_in, parse_count
from .extract import context_for, emails_from_text
from .jobs import JobRunner
from .level import (AUDIENCE, ENGAGEMENT, METRICS, PLATFORMS, describe, engagement_rate,
                    platform_ratios, typical)
from .models import Artist, ArtistResult, EmailResult, Finding
from .net import HttpClient
from .scoring import rank
from .smart import COUNTRY_NAMES
from .sources.instagram import InstagramGraph, instagram_handle, tiktok_handle
from .sources.youtube import YouTube
from .store import STATUS_LABELS, STATUSES, Store

FILTER_KEYS = ("q", "status", "level", "has_email", "min_confidence", "genre", "sort", "starred", "rising")
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


METER_SPAN = 32  # level meters run from 1/32x to 32x my engagement, log scale


def meter_pos(r) -> float:
    """Position (0-100%) of a ratio on a level meter."""
    if r is None:
        return 50.0
    x = (math.log(max(r, 1e-9)) / math.log(METER_SPAN) + 1) / 2
    return round(min(1.0, max(0.0, x)) * 100, 2)


PLATFORM_LABELS = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}


def platform_rows(metrics: dict, mine: dict) -> list[dict]:
    """One row per platform the artist has engagement on, for the card meters."""
    per = platform_ratios(metrics, mine)
    rows = []
    for platform, keys in PLATFORMS.items():
        parts = []
        for key in keys:
            if metrics.get(key) is not None:
                unit = {"instagram_reel_views": "views / reel",
                        "instagram_reel_engagement": "likes+comments / reel",
                        "tiktok_views": "views / video", "youtube_views": "views / video"}[key]
                parts.append(f"{compact(metrics[key])} {unit}")
        if parts:
            r = per.get(platform)
            rows.append({"platform": platform, "label": PLATFORM_LABELS[platform],
                         "text": parts[0], "detail": " · ".join(parts), "ratio": r,
                         "pos": meter_pos(r) if r is not None else None})
    return rows


def fill(template: str, lead: dict) -> str:
    first = lead["name"].split()[0] if lead["name"].split() else lead["name"]
    return (template or "").replace("{artist}", lead["name"]).replace("{first_name}", first)


def ingest_capture(store: Store, url: str, title: str, text: str,
                   image_url: str = "") -> tuple[int, list[str], dict]:
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
    views = video_views(text)
    if views is not None and platform == "tiktok":
        metrics["tiktok_views"] = views
    reel_views = video_views(text, "REEL_VIEWS")
    if reel_views is not None and platform == "instagram":
        metrics["instagram_reel_views"] = reel_views
    text = VIEWS_LINE_RE.sub("", text)
    bio_links = []
    for m in BIO_LINK_RE.finditer(text or ""):
        link = m.group(1).rstrip(".,)")
        if not any(s in link.lower() for s in ("instagram.com", "tiktok.com")):
            bio_links.append(link if link.startswith("http") else "https://" + link)

    artist_name = existing["name"] if existing else name
    artist = Artist(name=artist_name, links=[url] + bio_links, metrics=metrics,
                    image_url=image_url if image_url.startswith("https://") else "",
                    discovered_via="captured" if not existing else existing["discovered_via"])
    result = ArtistResult(artist=artist, instagram=ig, tiktok=tt, metrics=metrics,
                          links=[url] + bio_links, emails=rank(findings, artist_name, set()))
    lead_id = store.save_artist(artist, result)
    return lead_id, [e.email for e in result.emails], metrics


VIEWS_LINE_RE = re.compile(r"^(?:VIDEO|REEL)_VIEWS:(.*)$", re.MULTILINE)


def video_views(text: str, key: str = "VIDEO_VIEWS") -> int | None:
    """Median of the 'VIDEO_VIEWS: 1.2K | 980 | ...' (TikTok) or 'REEL_VIEWS: ...'
    (Instagram Reels tab) line the bookmarklet sends."""
    m = re.search(rf"^{key}:(.*)$", text or "", re.MULTILINE)
    if not m:
        return None
    return typical(parse_count(v) for v in m.group(1).split("|") if v.strip())


def bookmarklet(base_url: str) -> str:
    js = (
        "(()=>{const g=s=>{const e=document.querySelector(s);return e?(e.content||e.innerText||''):''};"
        "const h=location.hostname;let t;"
        "if(/instagram\\.com|tiktok\\.com/.test(h)){"
        "const f=g('[data-e2e=\"followers-count\"]');"
        # TikTok: views on recent videos in the grid, skipping pinned ones.
        "let it=[...document.querySelectorAll('[data-e2e=\"user-post-item\"]')];"
        "let vw=(it.length?it.filter(i=>!/pinned/i.test((i.querySelector('[data-e2e=\"video-card-badge\"]')||{}).innerText||''))"
        ".map(i=>i.querySelector('[data-e2e=\"video-views\"]')):[...document.querySelectorAll('[data-e2e=\"video-views\"]')])"
        ".filter(Boolean).map(e=>e.innerText.trim()).filter(Boolean).slice(0,12);"
        "t=[g('meta[property=\"og:description\"]'),g('meta[name=\"description\"]'),g('header'),"
        "g('[data-e2e=\"user-subtitle\"]'),g('[data-e2e=\"user-bio\"]'),f?f+' Followers':'',"
        "g('[data-e2e=\"user-link\"]'),vw.length?'VIDEO_VIEWS: '+vw.join(' | '):''].join('\\n')}"
        "else{t=g('meta[name=\"description\"]')+'\\n'+document.body.innerText}"
        # Instagram Reels tab: the view count shown on each reel tile, pinned skipped.
        "if(/instagram\\.com/.test(h)){let rv=[...document.querySelectorAll('a[href*=\"/reel/\"]')]"
        ".filter(a=>!a.querySelector('svg[aria-label*=\"inned\"]'))"
        ".map(a=>(a.innerText.split('\\n').map(x=>x.trim()).find(x=>/^[0-9][0-9.,]*[KkMm]?$/.test(x))))"
        ".filter(Boolean).slice(0,12);if(rv.length)t+='\\nREEL_VIEWS: '+rv.join(' | ')}"
        f"window.open('{base_url}capture?url='+encodeURIComponent(location.href)"
        "+'&title='+encodeURIComponent(document.title)"
        "+'&img='+encodeURIComponent(g('meta[property=\"og:image\"]'))"
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
    app.jinja_env.globals.update(STATUSES=STATUSES, STATUS_LABELS=STATUS_LABELS, METRICS=METRICS,
                                 ENGAGEMENT=ENGAGEMENT, AUDIENCE=AUDIENCE)
    app.jinja_env.filters["eng_rate"] = engagement_rate
    app.jinja_env.filters["meterpos"] = meter_pos
    app.jinja_env.filters["hue"] = lambda name: (sum(ord(c) * (i + 7) for i, c in enumerate(name or "")) * 37) % 360
    app.jinja_env.filters["country_name"] = lambda code: COUNTRY_NAMES.get(code, code)

    @app.context_processor
    def level_context():
        low, high = store.band()
        mine = store.my_metrics()
        return {"band_low": low, "band_high": high, "band_l": meter_pos(low),
                "band_h": meter_pos(high), "my": mine,
                "platform_rows": lambda m: platform_rows(m, mine)}

    def current_filters() -> dict:
        f = {k: request.args.get(k, "") for k in FILTER_KEYS}
        if "status" not in request.args:
            f["status"] = "active"
        if "level" not in request.args:
            f["level"] = ""
        f["sort"] = f["sort"] or ("level" if store.my_metrics() else "newest")
        return f

    def mailto_for(row: dict, email: str) -> str:
        s = store.settings()
        return "mailto:{}?{}".format(email, urlencode(
            {"subject": fill(s["email_subject"], row), "body": fill(s["email_body"], row)},
            quote_via=quote))

    @app.context_processor
    def shell_context():
        return {"pick_count": len(store.picks(99))}

    # -- discover (prompt search) -------------------------------------------------
    @app.get("/")
    def home():
        return redirect(url_for("discover_page"))

    @app.get("/discover")
    def discover_page():
        search_id = request.args.get("s", type=int)
        search = store.search(search_id) if search_id else None
        results = store.search_results(search_id) if search else []
        plan = SearchPlan.from_dict(search["plan"]) if search else None
        return render_template("discover.html", search=search, plan=plan, results=results,
                               recent=store.searches(8), status=settings.status(), s=settings,
                               has_level=bool(store.my_metrics()))

    @app.post("/search")
    def run_search():
        prompt = (request.form.get("prompt") or "").strip()
        if not prompt:
            return redirect(url_for("discover_page"))
        plan = plan_search(prompt)
        search_id = store.create_search(prompt, plan.to_dict())
        job_id = runner.submit("search", {"search_id": search_id, "prompt": prompt})
        store.set_search_job(search_id, job_id)
        return redirect(url_for("discover_page", s=search_id))

    @app.get("/api/search/<int:search_id>")
    def api_search(search_id: int):
        search = store.search(search_id)
        if not search:
            abort(404)
        job = search["job"] or {}
        html = render_template("_tiles.html", leads=store.search_results(search_id), show_reason=True)
        return jsonify(status=job.get("status", "done"), message=job.get("message", ""),
                       progress=job.get("progress", 0), total=job.get("total", 0),
                       count=search["n"], html=html)

    # -- for you ------------------------------------------------------------------
    def recommend_running() -> bool:
        return any(j["kind"] == "recommend" and j["status"] in ("queued", "running") for j in store.jobs(10))

    @app.get("/for-you")
    def for_you():
        picks = store.picks(30)
        liked, _ = store.taste()
        if len(picks) < 3 and liked and not recommend_running() and request.args.get("auto", "1") == "1":
            runner.submit("recommend", {"limit": 12})
        return render_template("foryou.html", picks=picks, liked=liked, running=recommend_running())

    @app.post("/picks/refresh")
    def refresh_picks():
        if not recommend_running():
            runner.submit("recommend", {"limit": int(request.form.get("limit") or 12)})
        return redirect(url_for("for_you", auto=0))

    @app.post("/api/pick/<int:lead_id>")
    def api_pick(lead_id: int):
        """Swipe actions: save (shortlist + star) or pass (not a fit)."""
        action = (request.get_json(force=True) or {}).get("action")
        if action == "save":
            store.update_lead(lead_id, status="shortlist", starred=1)
        elif action == "pass":
            store.update_lead(lead_id, status="not_a_fit")
        else:
            abort(400)
        return jsonify(ok=True, remaining=len(store.picks(99)))

    # -- artists library ------------------------------------------------------------
    @app.get("/artists")
    def leads():
        f = current_filters()
        per_page = 60
        page = max(1, request.args.get("page", 1, type=int))
        rows, total = store.leads(**{**f, "starred": f["starred"] == "1", "rising": f["rising"] == "1"}, limit=per_page * page)
        args = {k: v for k, v in f.items() if v}
        return render_template("artists.html", leads=rows, total=total, f=f, page=page,
                               more=total > per_page * page, args=args, counts=store.counts(),
                               genres=store.genre_counts(10), has_level=bool(store.my_metrics()),
                               export_url=url_for("export") + "?" + urlencode(args))

    @app.get("/pipeline")
    def pipeline():
        rows, _ = store.leads(status="", limit=2000, sort="updated")
        return render_template("pipeline.html", leads=rows,
                               counts=store.counts(), has_level=bool(store.my_metrics()))

    @app.get("/galaxy")
    def galaxy():
        rows, _ = store.leads(status="", limit=5000, sort="momentum")
        stars = [{"id": r["id"], "name": r["name"], "genre": (r["genre_list"] or [""])[0],
                  "r": r["level_ratio"], "bucket": r["level_bucket"], "m": r["momentum"],
                  "note": r["momentum_note"], "email": r["best_email"], "conf": r["best_confidence"],
                  "status": r["status"], "statusLabel": STATUS_LABELS[r["status"]], "starred": r["starred"]}
                 for r in rows]
        brightest = [r for r in rows if r["momentum"] is not None and r["status"] != "not_a_fit"][:8]
        return render_template("galaxy.html", stars=stars, brightest=brightest,
                               has_level=bool(store.my_metrics()), counts=store.counts())

    @app.get("/export.csv")
    def export():
        f = current_filters()
        rows, _ = store.leads(**{**f, "starred": f["starred"] == "1", "rising": f["rising"] == "1"}, limit=100_000)
        buf = io.StringIO()
        cols = ["name", "best_email", "best_confidence", "status", "level", "genres", "tags",
                "gender", "artist_type", "country", "location", "instagram", "tiktok", "website",
                "spotify_url", *METRICS, "notes"]
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

    # -- one artist -----------------------------------------------------------------
    def lead_context(lead_id: int) -> dict:
        row = store.lead(lead_id)
        if not row:
            abort(404)
        return {"lead": row, "mailto": {e["id"]: mailto_for(row, e["email"]) for e in row["emails"]},
                "best_mailto": mailto_for(row, row["best_email"]) if row["best_email"] else ""}

    @app.get("/lead/<int:lead_id>")
    def lead(lead_id: int):
        return render_template("lead.html", **lead_context(lead_id), back=request.args.get("back", ""))

    @app.get("/lead/<int:lead_id>/drawer")
    def lead_drawer(lead_id: int):
        return render_template("_drawer.html", **lead_context(lead_id), in_drawer=True)

    @app.post("/api/lead/<int:lead_id>")
    def api_update_lead(lead_id: int):
        data = request.get_json(force=True)
        if "starred" in data:
            data["starred"] = 1 if data["starred"] else 0
        if "genres" in data:
            data["genres"] = "; ".join(g.strip().lower() for g in re.split(r"[,;]", data["genres"]) if g.strip())
        store.update_lead(lead_id, **data)
        return jsonify(ok=True)

    @app.post("/lead/<int:lead_id>")
    def update_lead(lead_id: int):
        form = request.form
        fields = {k: form[k].strip() for k in ("status", "notes", "name", "website") if k in form}
        if "genres" in form:
            fields["genres"] = "; ".join(g.strip().lower() for g in re.split(r"[,;]", form["genres"]) if g.strip())
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
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ok=True)
        return redirect(url_for("lead", lead_id=lead_id, checking=1))

    @app.post("/lead/<int:lead_id>/delete")
    def delete_lead(lead_id: int):
        store.delete_lead(lead_id)
        return redirect(request.form.get("back") or url_for("leads"))

    # -- other ways to add artists ------------------------------------------------------
    @app.get("/find")
    def find():
        return redirect(url_for("discover_page") + "#more")

    @app.post("/find/discover")
    def discover():
        form = request.form
        params = {
            "source": form.get("source"), "genre": form.get("genre", ""),
            "area": form.get("area", ""), "seed": form.get("seed", ""),
            "limit": form.get("limit", type=int, default=25),
            "only_my_level": form.get("only_my_level") == "on",
            "find_emails": form.get("find_emails") == "on",
            "max_spotify_followers": form.get("max_spotify_followers", type=int),
        }
        runner.submit("discover", params)
        return redirect(url_for("leads", sort="newest"))

    @app.post("/find/lookup")
    def lookup():
        lines = [l.strip() for l in request.form.get("lines", "").splitlines() if l.strip()]
        if lines:
            runner.submit("lookup", {"lines": lines})
        return redirect(url_for("leads", sort="newest"))

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
        lead_id, emails, metrics = ingest_capture(store, url, values.get("title", ""), text,
                                                  values.get("img", ""))
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
            for key in ENGAGEMENT:
                raw = request.form.get(f"my_{key}", "").strip()
                count = parse_count(raw) if raw else None
                values[f"my_{key}"] = str(count) if count is not None else ""
            for key in ("level_low", "level_high", "email_subject", "email_body",
                        "my_instagram", "my_youtube_url"):
                if key in request.form:
                    values[key] = request.form[key].strip()
            store.save_settings(values)
            message = "Saved."
        base = request.host_url
        return render_template("settings.html", s=store.settings(), status=settings.status(),
                               message=message, bookmarklet=bookmarklet(base))

    @app.post("/settings/autofill")
    def autofill():
        """Fill my own engagement from my Instagram (official API) and YouTube."""
        http = HttpClient()
        values, notes = {}, []
        ig = (request.form.get("my_instagram") or "").strip().lstrip("@")
        yt = (request.form.get("my_youtube_url") or "").strip()
        values.update(my_instagram=ig, my_youtube_url=yt)
        if ig and settings.has_instagram:
            graph = InstagramGraph(http, settings.meta_access_token, settings.ig_user_id,
                                   settings.graph_api_version)
            _, info = graph.find(ig)
            if info.get("instagram_reel_engagement") is not None:
                values["my_instagram_reel_engagement"] = str(info["instagram_reel_engagement"])
            else:
                notes.append("Couldn't read your Instagram posts (check the handle and Meta keys)")
        if yt and settings.youtube_api_key:
            info = YouTube(http, settings.youtube_api_key).channel_info(yt)
            if info.get("views") is not None:
                values["my_youtube_views"] = str(info["views"])
            else:
                notes.append("Couldn't read that YouTube channel")
        store.save_settings(values)
        return redirect(url_for("settings_page", note="; ".join(notes)))

    @app.post("/capture/me")
    def capture_me():
        """'This is me': use a captured profile's engagement as my level."""
        lead_id = request.form.get("lead_id", type=int)
        row = store.lead(lead_id) if lead_id else None
        if row:
            values = {f"my_{k}": str(v) for k, v in row["metrics"].items() if k in ENGAGEMENT}
            store.save_settings(values)
            store.delete_lead(lead_id)
        return redirect(url_for("settings_page", note="Updated your level from your profile."))

    return app
