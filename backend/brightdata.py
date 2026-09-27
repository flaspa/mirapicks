"""Bright Data TikTok search discovery for Emily. Trusted backend only.

The API token is read from the environment here and never leaves this process: it is not logged,
returned, written to artifacts, or passed to any sandbox container.

CLI:
    python -m backend.brightdata --fixture data/brightdata/fashion_test.json
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys
import time
from urllib.parse import quote

import httpx

import backend.vultr_inference  # noqa: F401  (loads .env)

API = "https://api.brightdata.com/datasets/v3"
DATASET_ID = os.getenv("BRIGHTDATA_TIKTOK_DATASET_ID", "gd_m7n5ixlw1gc4no56kx")
MAX_POSTS = 50
STOPWORDS = set("""the and for with this that you your are was but not have has had from they them their what when
just like get got can all out one our its it's i'm im my me so to of in on at is be a an or as if do it by we
how who why new more most very really also into about than then there here over under outfit outfits fashion
fyp foryou foryoupage viral trend trends trending part which these those some will would should could been
being every much many make made need want going know think look looks""".split())


class BrightDataError(RuntimeError):
    pass


def tiktok_search_url(query: str) -> str:
    return f"https://www.tiktok.com/search?lang=en&q={quote(query.strip())}"


def fetch_tiktok_search(query: str, num_posts: int = 20, timeout_s: int = 300, poll_s: int = 10) -> list[dict]:
    """Trigger the TikTok 'Posts by Search URL' dataset, poll until ready, return raw records."""
    token = (os.getenv("BRIGHTDATA_API_TOKEN") or "").strip()
    if not token:
        raise BrightDataError("BRIGHTDATA_API_TOKEN is not configured")
    headers = {"Authorization": f"Bearer {token}"}
    num_posts = max(1, min(int(num_posts), MAX_POSTS))
    deadline = time.time() + timeout_s

    with httpx.Client(timeout=60, headers=headers) as client:
        r = client.post(f"{API}/trigger",
                        params={"dataset_id": DATASET_ID, "format": "json", "include_errors": "true"},
                        json=[{"url": tiktok_search_url(query), "num_of_posts": num_posts, "country": ""}])
        if r.status_code != 200:
            raise BrightDataError(f"trigger failed ({r.status_code}): {r.text[:200]}")
        snapshot_id = r.json().get("snapshot_id")
        if not snapshot_id:
            raise BrightDataError("trigger returned no snapshot_id")

        while True:
            if time.time() > deadline:
                raise BrightDataError(f"timed out after {timeout_s}s waiting for snapshot {snapshot_id}")
            time.sleep(poll_s)
            p = client.get(f"{API}/progress/{snapshot_id}")
            status = p.json().get("status") if p.status_code == 200 else None
            if status == "ready":
                break
            if status == "failed":
                raise BrightDataError(f"snapshot {snapshot_id} failed")

        s = client.get(f"{API}/snapshot/{snapshot_id}", params={"format": "json"})
        if s.status_code != 200:
            raise BrightDataError(f"snapshot download failed ({s.status_code})")
        data = s.json()
    return data if isinstance(data, list) else []


# --- Non-blocking steps for the background clipping-book collector (backend/collect_clips.py). ---

def _client() -> httpx.Client:
    token = (os.getenv("BRIGHTDATA_API_TOKEN") or "").strip()
    if not token:
        raise BrightDataError("BRIGHTDATA_API_TOKEN is not configured")
    return httpx.Client(timeout=120, headers={"Authorization": f"Bearer {token}"})


def trigger_search(query: str, num_posts: int) -> str:
    """Start one TikTok search job and return its snapshot_id (no country: Bright Data rejects US here)."""
    with _client() as c:
        r = c.post(f"{API}/trigger", params={"dataset_id": DATASET_ID, "format": "json", "include_errors": "true"},
                   json=[{"url": tiktok_search_url(query), "num_of_posts": max(1, min(int(num_posts), MAX_POSTS)),
                          "country": ""}])
    if r.status_code != 200:
        raise BrightDataError(f"trigger failed ({r.status_code}): {r.text[:200]}")
    sid = r.json().get("snapshot_id")
    if not sid:
        raise BrightDataError("trigger returned no snapshot_id")
    return sid


def snapshot_status(snapshot_id: str) -> str | None:
    """'running' | 'ready' | 'failed' | None (unknown / transient error)."""
    with _client() as c:
        p = c.get(f"{API}/progress/{snapshot_id}")
    return p.json().get("status") if p.status_code == 200 else None


def download_snapshot(snapshot_id: str) -> bytes:
    """Raw snapshot bytes, untouched, for archiving before normalization."""
    with _client() as c:
        s = c.get(f"{API}/snapshot/{snapshot_id}", params={"format": "json"})
    if s.status_code != 200:
        raise BrightDataError(f"snapshot download failed ({s.status_code})")
    return s.content


def _int(v) -> int | None:
    """Counts arrive as ints or strings (sometimes '1.2K'). Missing stays None; never invented."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.fullmatch(r"\s*([\d.,]+)\s*([KMB]?)\s*", str(v), re.IGNORECASE)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(n * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()])


def normalize(records: list[dict], limit: int | None = None) -> list[dict]:
    """Compact records; drops error rows and duplicate post_ids (the API can repeat posts)."""
    posts, seen = [], set()
    for r in records:
        if not r.get("url") or not r.get("post_id") or r.get("error") or str(r["post_id"]) in seen:
            continue
        seen.add(str(r["post_id"]))
        posts.append({
            "url": r["url"],
            "post_id": str(r["post_id"]),
            "description": (r.get("description") or "").strip()[:500],
            "created_at": r.get("create_time"),
            "creator": r.get("profile_username"),
            "creator_url": r.get("profile_url"),
            "hashtags": [h.lower() for h in (r.get("hashtags") or []) if isinstance(h, str)],
            "likes": _int(r.get("digg_count")),
            "comments": _int(r.get("comment_count")),
            "shares": _int(r.get("share_count")),
            "saves": _int(r.get("collect_count")),
            "plays": _int(r.get("play_count")),
        })
    return posts[:limit] if limit else posts


def summarize(posts: list[dict], query: str = "") -> dict:
    """Transparent, deterministic aggregates over the sampled posts only."""
    dates = sorted(p["created_at"][:10] for p in posts if p.get("created_at"))
    tags = collections.Counter(t for p in posts for t in set(p["hashtags"]))
    words = collections.Counter(
        w for p in posts for w in set(re.findall(r"[a-z][a-z'-]{3,}", re.sub(r"#\w+", " ", p["description"].lower())))
        if w not in STOPWORDS)

    def total(k):
        vals = [p[k] for p in posts if p[k] is not None]
        return {"sum": sum(vals), "posts_with_value": len(vals)} if vals else None

    top_by_plays = sorted((p for p in posts if p["plays"] is not None), key=lambda p: p["plays"], reverse=True)[:5]
    top_by_saves = sorted((p for p in posts if p["saves"] is not None), key=lambda p: p["saves"], reverse=True)[:3]
    return {
        "source": "tiktok", "query": query, "sample_size": len(posts),
        "date_range": [dates[0], dates[-1]] if dates else None,
        "recurring_hashtags": [[t, n] for t, n in tags.most_common(12) if n > 1],
        "recurring_terms": [[w, n] for w, n in words.most_common(12) if n > 1],
        "creators_represented": len({p["creator"] for p in posts if p["creator"]}),
        "totals": {k: total(k) for k in ("plays", "likes", "comments", "shares", "saves")},
        "top_by_plays": [{"url": p["url"], "plays": p["plays"], "likes": p["likes"]} for p in top_by_plays],
        "top_by_saves": [{"url": p["url"], "saves": p["saves"]} for p in top_by_saves],
        "note": "Aggregates cover only this Bright Data sample of TikTok search results, not TikTok as a whole.",
    }


def representative(posts: list[dict], n: int = 12) -> list[dict]:
    """Most-played posts first (observed metric), then the rest in API order; bounded for the prompt."""
    ranked = sorted(posts, key=lambda p: (p["plays"] is None, -(p["plays"] or 0)))
    return [{k: p[k] for k in ("url", "description", "created_at", "creator", "hashtags",
                               "likes", "comments", "shares", "saves", "plays")} | {"description": p["description"][:300]}
            for p in ranked[:n]]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fixture":
        raw = json.load(open(sys.argv[2], encoding="utf-8"))
        posts = normalize(raw)
        print(json.dumps(summarize(posts, "fixture"), indent=2, ensure_ascii=False))
        print(f"normalized {len(posts)}/{len(raw)} records; first: {json.dumps(posts[0], ensure_ascii=False)[:300]}")
    else:
        print(__doc__)
