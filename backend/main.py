"""Mira Picks control plane. Run: python -m uvicorn backend.main:app --port 8000"""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.orchestrator import (RUNS, STAGED, publish_staged, remove_live_demo, start_demo_run, start_feedback,
                                  start_run)
from backend.sandbox import ARTIFACTS_DIR, REPO_ROOT

app = FastAPI(title="Mira Picks")
app.mount("/artifacts", StaticFiles(directory=ARTIFACTS_DIR), name="artifacts")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
INDEX = Path(__file__).parent / "static" / "index.html"
BRANDING_DIR = Path(__file__).parent.parent / "assets" / "branding"  # canonical favicon source
FAVICONS = {"favicon-32.png", "favicon-48.png", "favicon-512.png"}
DEMO_PRESETS = REPO_ROOT / "config" / "demo_presets.json"


@app.get("/{name}.png", include_in_schema=False)
def favicon(name: str):
    if f"{name}.png" not in FAVICONS:
        raise HTTPException(404)
    return FileResponse(BRANDING_DIR / f"{name}.png", media_type="image/png")


class RunRequest(BaseModel):
    # Only `topic` is shown in the console; the rest are optional API/debug inputs. Public runs never publish.
    url: str | None = None          # product/page URL (legacy name, still accepted)
    product_url: str | None = None  # same as `url`; wins if both are given
    social_url: str | None = None
    trend_query: str | None = None
    topic: str | None = None


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.post("/api/runs")
def create_run(req: RunRequest):
    product = (req.product_url or req.url or "").strip() or None
    return {"id": start_run(product, (req.social_url or "").strip() or None, (req.trend_query or "").strip()[:100] or None,
                          (req.topic or "").strip()[:80] or None)}


@app.get("/api/runs/{job_id}")
def get_run(job_id: str):
    if job_id not in RUNS:
        raise HTTPException(404)
    return RUNS[job_id]


# ---------------- Authenticated Demo Publish (server-side PIN; no accounts) ----------------
# The PIN lives only in the server environment (DEMO_PUBLISH_PIN). After a correct PIN the browser gets a short-lived,
# HttpOnly, signed cookie. The cookie carries only an expiry and an HMAC made with a per-process random key: it never
# contains the PIN, and a server restart invalidates it.

COOKIE, SESSION_S = "mp_demo", 4 * 3600
_KEY = secrets.token_bytes(32)
_FAILS: list[float] = []            # recent failed PIN attempts (global throttle)
_FAIL_LOCK = threading.Lock()
MAX_FAILS, FAIL_WINDOW_S = 8, 600


def _pin() -> str:
    return (os.getenv("DEMO_PUBLISH_PIN") or "").strip()


def _sign(exp: int) -> str:
    return hmac.new(_KEY, str(exp).encode(), hashlib.sha256).hexdigest()


def _demo_ok(request: Request) -> bool:
    token = request.cookies.get(COOKIE) or ""
    exp, _, sig = token.partition(".")
    return bool(_pin()) and exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign(int(exp)))


def _presets() -> list[dict]:
    return json.loads(DEMO_PRESETS.read_text(encoding="utf-8"))["presets"]


class UnlockRequest(BaseModel):
    pin: str


class DemoRunRequest(BaseModel):
    preset: str


@app.get("/api/demo/status")
def demo_status(request: Request):
    ok = _demo_ok(request)
    live = None
    if ok:
        from backend.homepage import load_demo_live
        d = load_demo_live()
        live = {"slug": d["slug"], "title": d.get("title"), "url": d.get("url")} if d else None
    return {"configured": bool(_pin()), "enabled": ok, "live": live,
            "presets": [{"id": p["id"], "label": p["label"]} for p in _presets()] if ok else []}


@app.post("/api/demo/unlock")
def demo_unlock(req: UnlockRequest, response: Response):
    if not _pin():
        raise HTTPException(503, "Demo publishing is not configured on this server.")
    now = time.time()
    with _FAIL_LOCK:
        _FAILS[:] = [t for t in _FAILS if now - t < FAIL_WINDOW_S]
        if len(_FAILS) >= MAX_FAILS:
            raise HTTPException(429, "Too many attempts. Try again later.")
        if not hmac.compare_digest(req.pin.strip().encode(), _pin().encode()):
            _FAILS.append(now)
            raise HTTPException(401, "Incorrect PIN.")
    exp = int(now) + SESSION_S
    response.set_cookie(COOKIE, f"{exp}.{_sign(exp)}", max_age=SESSION_S, httponly=True, secure=True, samesite="strict")
    return {"enabled": True}


@app.post("/api/demo/lock")
def demo_lock(response: Response):
    response.delete_cookie(COOKIE)
    return {"enabled": False}


@app.post("/api/demo/runs")
def demo_run(req: DemoRunRequest, request: Request):
    if not _demo_ok(request):  # server-side authorization: hidden buttons are not the boundary
        raise HTTPException(401, "Demo publishing is locked.")
    preset = next((p for p in _presets() if p["id"] == req.preset), None)
    if not preset:
        raise HTTPException(400, "Unknown demo preset.")
    job_id = start_demo_run(preset)
    if not job_id:
        raise HTTPException(409, "A demo publish run is already in progress.")
    return {"id": job_id}


# ---------------- Emily's clipping book: READ-ONLY inspector (no collection, no edits) ----------------

_CLIPS_CACHE: dict = {"mtime": None, "clips": []}


def _clipbook() -> list[dict]:
    """Parsed clipping book, re-read only when the file changes. Never written here."""
    from backend.emily import CLIPBOOK
    try:
        mtime = CLIPBOOK.stat().st_mtime
    except OSError:
        return []
    if _CLIPS_CACHE["mtime"] != mtime:
        _CLIPS_CACHE["clips"] = [json.loads(l) for l in CLIPBOOK.read_text(encoding="utf-8").splitlines() if l.strip()]
        _CLIPS_CACHE["mtime"] = mtime
    return _CLIPS_CACHE["clips"]


def _queries(c: dict) -> list[str]:
    return c.get("source_queries") or ([c["source_query"]] if c.get("source_query") else [])


@app.get("/api/clips/summary")
def clips_summary():
    clips = _clipbook()
    counts: dict[str, int] = {}
    for c in clips:  # a clip found by several searches counts once under each of them
        for q in _queries(c):
            counts[q] = counts.get(q, 0) + 1
    status = None
    try:  # collection status only (no job ids, errors or paths)
        st = json.loads((REPO_ROOT / "data" / "brightdata" / "state" / "collection_state.json").read_text(encoding="utf-8"))
        jobs = {k: v.get("status") for k, v in st.get("queries", {}).items() if not k.startswith("validate_")}
        done = sum(1 for s in jobs.values() if s == "complete")
        status = {"searches": len(jobs), "complete": done, "running": sum(1 for s in jobs.values() if s in ("running", "pending")),
                  "finished": bool(jobs) and all(s in ("complete", "failed", "timed_out", "skipped") for s in jobs.values())}
    except (OSError, json.JSONDecodeError):
        pass
    demo = {}
    for f in sorted((REPO_ROOT / "data" / "brightdata" / "demo").glob("*.json")):
        try:
            demo[f.stem] = len(json.loads(f.read_text(encoding="utf-8")).get("clips") or [])
        except (OSError, json.JSONDecodeError):
            continue
    return {"total_unique_clips": len(clips),
            "queries": [{"query": q, "count": n} for q, n in sorted(counts.items(), key=lambda x: -x[1])],
            "collection": status, "demo_datasets": demo,
            "counting_note": "A post found by more than one search is counted under each search; the total counts it once."}


@app.get("/api/clips")
def clips_page(query: str | None = None, limit: int = 20, offset: int = 0):
    from backend.emily import clip_card
    limit, offset = max(1, min(int(limit), 20)), max(0, int(offset))
    clips = [c for c in _clipbook() if not query or query in _queries(c)]
    clips.sort(key=lambda c: (-(c.get("play_count") or 0), c.get("post_id") or ""))  # most-played first, deterministic
    return {"total": len(clips), "offset": offset, "limit": limit, "query": query,
            "clips": [clip_card(c) for c in clips[offset:offset + limit]]}


# ---------------- Andy's press desk: READ-ONLY view of the curated source registry (no crawling) ----------------

@app.get("/api/press/sources")
def press_sources():
    reg = json.loads((REPO_ROOT / "config" / "fashion_sources.json").read_text(encoding="utf-8"))
    g = reg.get("selection_guidance") or {}
    return {"total_sources": len(reg.get("sources") or []),
            "per_assignment": {"min": g.get("min_sources_per_assignment"), "default": g.get("default_sources_per_assignment"),
                               "max": g.get("max_sources_per_assignment")},
            "sources": [{"name": s.get("name"), "url": s["url"] if str(s.get("url") or "").startswith("https://") else None,
                         "description": s.get("description"), "access_note": s.get("access_note")}
                        for s in reg.get("sources") or []]}


# ---------------- Staging -> human review -> Publish Demo Story -> Remove (all require the demo session) ----------------

class FeedbackRequest(BaseModel):
    text: str


def _require_demo(request: Request) -> None:
    if not _demo_ok(request):
        raise HTTPException(401, "Demo publishing is locked.")


@app.get("/api/demo/preview/{job_id}", include_in_schema=False)
def demo_preview(job_id: str, request: Request):
    _require_demo(request)
    built = STAGED.get(job_id)
    if not built or not (built["work"] / "index.html").exists():
        raise HTTPException(404, "No staged story for this run.")
    html = (built["work"] / "index.html").read_text(encoding="utf-8")
    # Resolve the page's absolute links/assets against the publication, and keep the preview out of any index.
    head = '<base href="https://www.mirapicks.com/"><meta name="robots" content="noindex, nofollow">'
    html = html.replace("<head>", "<head>" + head, 1) if "<head>" in html else head + html
    return HTMLResponse(html, headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"})


@app.post("/api/demo/runs/{job_id}/feedback")
def demo_feedback(job_id: str, req: FeedbackRequest, request: Request):
    _require_demo(request)
    err = start_feedback(job_id, req.text[:1000])
    if err:
        raise HTTPException(409, err)
    return {"id": job_id, "accepted": True}


@app.post("/api/demo/runs/{job_id}/publish")
def demo_publish(job_id: str, request: Request):
    _require_demo(request)
    try:
        st = publish_staged(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"published": True, "slug": st["slug"], "url": st["url"]}


@app.post("/api/demo/remove")
def demo_remove(request: Request):
    _require_demo(request)
    return remove_live_demo()
