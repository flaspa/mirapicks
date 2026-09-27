"""Temporary demo story: publish one QA-passed staged build to a dedicated slug, then remove it and restore the homepage.

Safety properties:
- never touches permanent stories: the demo uses its own "demo-feature-<id>" slug and a demo.json record that the
  permanent inventory (pages/*/article.json) never reads;
- at most one live demo story;
- the homepage bytes are snapshotted before publishing (outside the public pages folder) and restored exactly on removal;
- removal is idempotent.
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone

from backend.homepage import DEMO_LIVE, DEMO_STATE_DIR, PAGES_DIR, build_homepage, load_demo_live

BACKUP = DEMO_STATE_DIR / "homepage_before_demo.html"
ARCHIVE = DEMO_STATE_DIR / "archive"
_LOCK = threading.Lock()


def _inventory() -> list[str]:
    return sorted(p.parent.name for p in PAGES_DIR.glob("*/article.json"))


def publish_demo(built: dict, job_id: str) -> dict:
    from backend.publish import promote_story  # lazy: publish imports homepage
    with _LOCK:
        if load_demo_live():
            raise ValueError("A demo story is already live. Remove it first.")
        if not built.get("ok"):
            raise ValueError("Only a QA-passed staged story can be published.")
        slug = f"demo-feature-{job_id[:8]}"
        if slug in _inventory() or (PAGES_DIR / slug).exists():
            raise ValueError("Demo slug collision; nothing published.")
        DEMO_STATE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PAGES_DIR / "index.html", BACKUP)                   # exact pre-demo homepage
        before = _inventory()
        promote_story(built, slug, register=False)                       # demo.json, never article.json
        state = {"slug": slug, "job_id": job_id, "title": built["brief"].get("headline"), "deck": built["brief"].get("dek"),
                 "hero_image": (built.get("images") or [None])[0], "url": f"https://www.mirapicks.com/{slug}/",
                 "published_at": datetime.now(timezone.utc).isoformat(), "inventory_before": before}
        DEMO_LIVE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        build_homepage(PAGES_DIR)                                        # adds the "Live from the desk" slot
        return state


def remove_demo() -> dict:
    with _LOCK:
        state = load_demo_live()
        if not state:
            return {"removed": False, "message": "No demo story is live."}
        slug = state["slug"]
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        page = PAGES_DIR / slug
        if slug.startswith("demo-feature-") and page.is_dir() and not (page / "article.json").exists():
            shutil.move(str(page), str(ARCHIVE / f"{slug}-{ts}"))
        DEMO_LIVE.unlink(missing_ok=True)
        if BACKUP.exists() and _inventory() == state.get("inventory_before"):
            shutil.copy2(BACKUP, PAGES_DIR / "index.html")               # exact pre-demo bytes
            restored = "exact pre-demo homepage restored"
        else:  # permanent stories changed meanwhile: rebuild (the demo slot is gone because live.json is gone)
            build_homepage(PAGES_DIR)
            restored = "homepage rebuilt without the demo slot (permanent stories changed while the demo was live)"
        if BACKUP.exists():
            shutil.move(str(BACKUP), str(ARCHIVE / f"homepage_before_demo-{ts}.html"))
        return {"removed": True, "slug": slug, "restore": restored}
