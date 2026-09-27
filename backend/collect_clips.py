"""Emily's asynchronous TikTok clipping-book collector (Bright Data). Trusted backend only.

Runs outside the interactive editorial path. Bounded, single process, restartable from state on disk.

    python -m backend.collect_clips import-seed            # fold data/brightdata/fashion_test.json in (once)
    python -m backend.collect_clips validate streetwear 5  # one tiny live job, tracked separately
    python -m backend.collect_clips run                    # full configured batch (resumes running jobs)
    python -m backend.collect_clips run --retry-failed     # also re-queue failed / timed-out queries
    python -m backend.collect_clips status
    python -m backend.collect_clips demo                   # build demo subsets if enough clips exist

The Bright Data token is only read inside backend.brightdata; it is never logged or written here.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

from backend import brightdata as bd
from backend.brightdata import _int
from backend.sandbox import REPO_ROOT

CONFIG = REPO_ROOT / "config" / "emily_collection_queries.json"
BASE = REPO_ROOT / "data" / "brightdata"
RAW, CLIPS, STATE, DEMO = BASE / "raw", BASE / "clips", BASE / "state", BASE / "demo"
CLIPBOOK = CLIPS / "tiktok_clipbook.jsonl"
STATE_FILE = STATE / "collection_state.json"
SEED = BASE / "fashion_test.json"

JOB_TIMEOUT_S = 30 * 60
POLL_S = 30
DONE = {"complete", "failed", "timed_out", "skipped"}
DEMO_BEATS = {
    "runway_couture": ["runway_couture", "couture_fashion_week"],
    "streetwear_genz": ["streetwear", "genz_fashion", "genz_shoes", "ballet_flats", "mesh_sneakers", "kitten_heels"],
    "budget_shopping": ["affordable_fashion", "budget_outfits", "fashion_dupes"],
}
DEMO_MIN, DEMO_MAX = 30, 50


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    token = (os.getenv("BRIGHTDATA_API_TOKEN") or "").strip()
    if token:
        msg = msg.replace(token, "[redacted]")
    print(f"{now()} {msg}", flush=True)


def _write_json(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"queries": {}, "imports": {}, "updated_at": None}


def save_state(state: dict) -> None:
    state["updated_at"] = now()
    _write_json(STATE_FILE, state)


# ---------------- clipping book ----------------

def clip_key(c: dict) -> str | None:
    return f"id:{c['post_id']}" if c.get("post_id") else (f"url:{c['url']}" if c.get("url") else None)


def load_clipbook() -> dict[str, dict]:
    book = {}
    if CLIPBOOK.exists():
        for line in CLIPBOOK.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                book[clip_key(c)] = c
    return book


def save_clipbook(book: dict[str, dict]) -> None:
    CLIPS.mkdir(parents=True, exist_ok=True)
    tmp = CLIPBOOK.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in book.values()), encoding="utf-8")
    os.replace(tmp, CLIPBOOK)


def normalize_record(r: dict, query_id: str, query: str, snapshot_id: str) -> dict | None:
    """Keep what Bright Data actually returned; absent values stay None / empty."""
    if r.get("error") or not (r.get("post_id") or r.get("url")):
        return None
    return {
        "post_id": str(r["post_id"]) if r.get("post_id") else None,
        "url": r.get("url"),
        "description": (r.get("description") or "").strip() or None,
        "create_time": r.get("create_time"),
        "profile_id": r.get("profile_id"),
        "profile_username": r.get("profile_username"),
        "profile_url": r.get("profile_url"),
        "profile_followers": _int(r.get("profile_followers")),
        "is_verified": r.get("is_verified"),
        "hashtags": [h for h in (r.get("hashtags") or []) if isinstance(h, str)],
        "digg_count": _int(r.get("digg_count")),
        "share_count": _int(r.get("share_count")),
        "collect_count": _int(r.get("collect_count")),
        "comment_count": _int(r.get("comment_count")),
        "play_count": _int(r.get("play_count")),
        "video_duration": r.get("video_duration"),
        "preview_image": r.get("preview_image"),
        "source_query_id": query_id,
        "source_query": query,
        "source_query_ids": [query_id],
        "source_queries": [query],
        "brightdata_snapshot_id": snapshot_id,
        "collected_at": r.get("timestamp") or now(),
    }


def ingest(records: list, query_id: str, query: str, snapshot_id: str) -> tuple[int, int]:
    """Merge records into the global book. Returns (valid records, new unique clips)."""
    book, valid, added = load_clipbook(), 0, 0
    for r in records if isinstance(records, list) else []:
        c = normalize_record(r, query_id, query, snapshot_id) if isinstance(r, dict) else None
        if not c:
            continue
        valid += 1
        k = clip_key(c)
        if k in book:  # same post from another search: remember the extra source query only
            old = book[k]
            if query_id not in old.setdefault("source_query_ids", []):
                old["source_query_ids"].append(query_id)
                old.setdefault("source_queries", []).append(query)
        else:
            book[k] = c
            added += 1
    save_clipbook(book)
    return valid, added


# ---------------- Bright Data jobs ----------------

def _finish_job(st: dict, qid: str, query: str) -> None:
    sid = st["snapshot_id"]
    raw = bd.download_snapshot(sid)
    RAW.mkdir(parents=True, exist_ok=True)
    raw_path = RAW / f"{qid}__{sid}.json"
    if not raw_path.exists():  # never overwrite an earlier raw download
        raw_path.write_bytes(raw)
    records = json.loads(raw)
    valid, added = ingest(records, qid.removeprefix("validate_"), query, sid)
    st.update(status="complete", downloaded=len(records) if isinstance(records, list) else 0, valid=valid,
              unique_added=added, raw_file=str(raw_path.relative_to(REPO_ROOT)).replace("\\", "/"), completed_at=now())
    log(f"[{qid}] complete: {st['downloaded']} downloaded, {valid} valid, {added} new unique clips")


def poll_job(st: dict, qid: str, query: str) -> None:
    """Advance one running job by at most one step. Errors are recorded, never raised."""
    try:
        status = bd.snapshot_status(st["snapshot_id"])
        st["last_polled_at"], st["last_bd_status"] = now(), status
        if status == "ready":
            _finish_job(st, qid, query)
        elif status == "failed":
            st.update(status="failed", error="Bright Data reported the snapshot failed", completed_at=now())
            log(f"[{qid}] failed at Bright Data")
        elif time.time() - st["triggered_ts"] > JOB_TIMEOUT_S:
            st.update(status="timed_out", error=f"not ready after {JOB_TIMEOUT_S // 60} min", completed_at=now())
            log(f"[{qid}] timed out (snapshot {st['snapshot_id']} may still finish at Bright Data)")
    except Exception as exc:  # transient network / parse errors: keep polling until the job timeout
        st["last_error"] = str(exc)[:200]
        log(f"[{qid}] poll error: {st['last_error']}")
        if time.time() - st["triggered_ts"] > JOB_TIMEOUT_S:
            st.update(status="timed_out", error=st["last_error"], completed_at=now())


def start_job(state: dict, qid: str, query: str, posts: int) -> None:
    st = state["queries"].setdefault(qid, {})
    st.update(query=query, requested=posts, status="running", error=None, triggered_at=now(), triggered_ts=time.time())
    try:
        st["snapshot_id"] = bd.trigger_search(query, posts)
        log(f"[{qid}] triggered '{query}' ({posts} posts) snapshot={st['snapshot_id']}")
    except Exception as exc:
        st.update(status="failed", error=str(exc)[:200], completed_at=now())
        log(f"[{qid}] trigger failed: {st['error']}")


def run(jobs: list[dict], posts: int, max_concurrent: int, ceiling: int, retry_failed: bool = False) -> None:
    state = load_state()
    for j in jobs:
        st = state["queries"].get(j["id"])
        if st is None or (retry_failed and st.get("status") in {"failed", "timed_out"}):
            state["queries"][j["id"]] = {"query": j["query"], "requested": posts, "status": "pending"}
    save_state(state)
    log(f"collector start: {len(jobs)} queries, {posts} posts/query, max_concurrent={max_concurrent}, ceiling={ceiling}")
    while True:
        qs = state["queries"]
        active = [j for j in jobs if qs[j["id"]]["status"] == "running"]
        for j in active:
            poll_job(qs[j["id"]], j["id"], j["query"])
        unique = len(load_clipbook())
        active = [j for j in jobs if qs[j["id"]]["status"] == "running"]
        pending = [j for j in jobs if qs[j["id"]]["status"] == "pending"]
        if unique >= ceiling and pending:
            for j in pending:
                qs[j["id"]].update(status="skipped", error=f"clip ceiling {ceiling} reached")
            pending = []
            log(f"ceiling reached ({unique} unique clips); remaining queries skipped")
        while pending and len(active) < max_concurrent:
            j = pending.pop(0)
            start_job(state, j["id"], j["query"], posts)
            if qs[j["id"]]["status"] == "running":
                active.append(j)
        state["clipbook_unique"] = unique
        save_state(state)
        if not active and not pending:
            break
        time.sleep(POLL_S)
    log(f"collector finished: {len(load_clipbook())} unique clips; " + ", ".join(
        f"{j['id']}={state['queries'][j['id']]['status']}" for j in jobs))


# ---------------- seed, status, demo ----------------

def import_seed() -> None:
    state = load_state()
    if SEED.name in state["imports"]:
        log(f"{SEED.name} already imported: {state['imports'][SEED.name]}")
        return
    records = json.loads(SEED.read_text(encoding="utf-8"))
    by_query = {q["query"].lower(): q["id"] for q in load_config()["queries"]}
    # The seed's own input URL says which search produced it; attribute it to that configured query.
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit((records[0].get("input") or {}).get("url", "")).query).get("q", ["seed"])[0]
    qid = by_query.get(q.lower(), "seed")
    valid, added = ingest(records, qid, q, f"seed:{SEED.name}")
    state["imports"][SEED.name] = {"raw": len(records), "valid": valid, "unique_added": added,
                                   "attributed_to": qid, "imported_at": now()}
    save_state(state)
    log(f"imported {SEED.name}: {len(records)} raw, {valid} valid, {added} new unique clips (query '{q}')")


def status() -> None:
    state, book = load_state(), load_clipbook()
    print(f"clipbook unique clips: {len(book)}   state updated: {state.get('updated_at')}")
    for qid, st in state["queries"].items():
        print(f"  {qid:24} {st.get('status'):10} requested={st.get('requested')} downloaded={st.get('downloaded', '-')} "
              f"new={st.get('unique_added', '-')} snapshot={st.get('snapshot_id', '-')}"
              + (f" error={st['error']}" if st.get("error") else ""))
    for name, imp in state.get("imports", {}).items():
        print(f"  import {name}: {imp}")


def build_demo() -> None:
    book = list(load_clipbook().values())
    DEMO.mkdir(parents=True, exist_ok=True)
    for beat, qids in DEMO_BEATS.items():
        clips = [c for c in book if set(c.get("source_query_ids") or [c.get("source_query_id")]) & set(qids)]
        if len(clips) < DEMO_MIN:
            log(f"demo {beat}: only {len(clips)} relevant clips (< {DEMO_MIN}); not generated yet")
            continue
        # Deterministic: most-played first, post_id as tiebreak.
        clips.sort(key=lambda c: (-(c.get("play_count") or 0), c.get("post_id") or c.get("url") or ""))
        _write_json(DEMO / f"{beat}.json", {"beat": beat, "source_query_ids": qids, "generated_at": now(),
                                            "count": min(len(clips), DEMO_MAX), "clips": clips[:DEMO_MAX]})
        log(f"demo {beat}: wrote {min(len(clips), DEMO_MAX)} clips")


def main(argv: list[str]) -> int:
    cfg = load_config()
    d = cfg["defaults"]
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "import-seed":
        import_seed()
    elif cmd == "validate":
        qid, n = argv[2], int(argv[3]) if len(argv) > 3 else 5
        q = next(q for q in cfg["queries"] if q["id"] == qid)
        run([{"id": f"validate_{qid}", "query": q["query"]}], n, 1, 10**9)
    elif cmd == "run":
        run(cfg["queries"], d["posts_per_query"], d["max_concurrent"], d["max_unique_clips"], "--retry-failed" in argv)
    elif cmd == "demo":
        build_demo()
    else:
        status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
