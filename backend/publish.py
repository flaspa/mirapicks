"""Editorial run -> Miranda FEATURE -> Developer -> Technical QA (one repair) -> published static page.

The research is the same editorial run as the agent console (backend.orchestrator.run_editorial):
Miranda assignment -> Andy (product page + fashion press) -> Emily (clipping book) -> Nigel -> Miranda verdict.
Only FEATURE continues to the Developer. WATCH and PASS stop after Miranda.

Usage (on the VM):
    /opt/mirapicks-venv/bin/python -m backend.publish <product_url> [--topic="..."] [--force-feature]
    /opt/mirapicks-venv/bin/python -m backend.publish --batch=<candidates.json> [--max-published=5]
      candidates.json: [{"topic": "...", "url": "<product page>"}, ...]  (each tried once, in order)

Code generation happens remotely on Vultr Serverless Inference (text only).
The generated page is only ever executed inside a disposable QA browser sandbox.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from backend.homepage import build_homepage, ensure_favicon, ensure_shell
from backend.orchestrator import MIRA_MODEL, UNTRUSTED_NOTE, assignment_block, run_editorial
from backend.sandbox import ARTIFACTS_DIR, REPO_ROOT, SCOUT_IMAGE, _docker_command
from backend.vultr_inference import chat_json, chat_text

DEVELOPER_MODEL = os.getenv("DEVELOPER_MODEL", "deepseek-v4-flash-0731")
PAGES_DIR = Path(os.getenv("MIRA_PAGES_DIR", REPO_ROOT / "pages"))

BRIEF_SYSTEM = f"""You are Mira, Editor-in-Chief of Mira Picks, a premium fashion publication.
This product is approved as a FEATURE. Write the brief your web developer will build the story page from.
{UNTRUSTED_NOTE}
Reply with only a JSON object:
{{"headline": "short, striking editorial headline (max 8 words)",
  "dek": "one-sentence standfirst",
  "angle": "the story angle in one sentence",
  "sections": [{{"title": "short section title", "body": "2-3 sentence editorial copy"}}],
  "verdict": "one-line Mira Picks verdict",
  "pull_quote": "one short quotable line"}}
Use 3 sections. Keep copy concise, confident and factual to the evidence. Ground the story in <assignment>
and Nigel's synthesis: one section should say what the fashion press and social culture show, naming only
publications and signals present in the synthesis."""

DEV_SYSTEM = """You are the Developer at Mira Picks. You build one polished, consumer-facing fashion
editorial web page as a single self-contained HTML file.

Hard requirements:
- Output ONLY the HTML document, starting with <!doctype html>. No explanations, no markdown fences.
- Plain HTML + inline <style> (+ optional tiny inline <script>). No frameworks, no build step.
- Google Fonts via <link> is allowed (e.g. a high-contrast serif for headlines and a clean sans for body).
- Use ONLY the image URLs provided. Never invent image URLs. Every <img> needs alt text.
- The hero must be a large full-bleed image (at least 70vh tall on desktop) with the headline over or beside it.
- Use the headline EXACTLY as given, verbatim. Show the product name and brand exactly as given.
- Responsive: must work at 390px wide with no horizontal scrolling (max-width:100% on images, no fixed widths).
- No JavaScript errors. Do not load external scripts.

MIRA PICKS BRAND SYSTEM (keep constant on every article):
- A slim masthead with the "Mira Picks" wordmark in Cormorant Garamond, uppercase, linking to "/" (the homepage).
- Base typography: Cormorant Garamond for headlines/wordmark, Inter for body and UI labels.
  You may add ONE extra Google display font if the art direction calls for it.
- Small uppercase letter-spaced labels for metadata; generous whitespace; paper-toned or deliberate backgrounds.
- A footer with the wordmark linking to "/".

PER-ARTICLE ART DIRECTION (you are also the art director):
Choose a theme that fits this product and Mira's angle, influenced by Vogue (authority, dramatic type),
Dazed (image-led energy, unexpected scale), SSENSE (restrained modernism) and Net-a-Porter (editorial
storytelling with clear product discovery). Vary hero composition, image scale and crop, accent palette,
headline treatment, section layout, background treatments and editorial devices. Never a dashboard,
SaaS page, blog theme or generic shop template.

Required content: large hero image, headline, dek, the brief's sections, a pull quote, the verdict,
an image gallery, and a "Shop the pick" card with price and a link to the product URL.

If "editorial_context" is provided, include a short "What the press says / what social shows" passage built only
from it. Name only the publications listed there; never invent sources, quotes, statistics or engagement numbers.

In <head>, declare your art direction exactly like this:
<meta name="mira:theme" content="2-4 word theme name">
<meta name="mira:accent" content="#hexcolor">"""


def _big(url: str) -> str:
    """Ask Shopify-style CDNs for a large rendition."""
    return re.sub(r"([?&])width=\d+", r"\1width=1600", url)


def _extract_html(text: str) -> str:
    start = text.lower().find("<!doctype")
    if start == -1:
        start = text.lower().find("<html")
    end = text.lower().rfind("</html>")
    if start == -1 or end == -1:
        raise RuntimeError(f"Developer returned no HTML document: {text[:200]}")
    return ensure_shell(ensure_favicon(text[start : end + len("</html>")]))


def run_qa(work: Path, needles: list[str]) -> dict:
    """Open work/index.html in a disposable, locked-down browser container."""
    os.chmod(work, 0o777)
    run_id = "qa-" + uuid.uuid4().hex[:8]
    cmd = _docker_command(run_id, work, "unused")[:-2]  # drop image + url
    cmd += ["--entrypoint", "node", SCOUT_IMAGE, "/app/dist/qa.js", *needles]
    proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    lines = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    if not lines:
        return {"pass": False, "issues": [f"QA sandbox produced no output: {proc.stderr.decode()[-300:]}"]}
    return json.loads(lines[-1])


# ---------------- Evidence links: publications Andy read + posts from Emily's sample (stored data only) ----------------

SRC_START, SRC_END = "<!-- mira-sources -->", "<!-- /mira-sources -->"
SRC_CSS = """
.mp-src,.mp-src *{box-sizing:border-box;margin:0;padding:0;border:0;background:none;text-transform:none;letter-spacing:normal;}
.mp-src{background:#f5f2ed;color:#6f6a63;padding:28px clamp(20px,4vw,56px);border-top:1px solid #d9d3ca;
  font-family:'Inter',system-ui,sans-serif;font-size:13px;line-height:1.7;}
.mp-src p{margin:2px 0;color:#6f6a63;font-size:13px;}
.mp-src b{font-weight:500;color:#111;letter-spacing:.12em;text-transform:uppercase;font-size:11px;margin-right:8px;}
.mp-src a,a.mp-ref{color:inherit;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:3px;}
.mp-src a:hover,a.mp-ref:hover{color:#111;}
"""
# Short forms the Developer tends to write for registry names ("Refinery29 Fashion" -> "Refinery29").
_SUFFIXES = (" Fashion", " Style", " Editorial")


def _names(pub: str) -> list[str]:
    out = [pub]
    for suf in _SUFFIXES:
        if pub.endswith(suf):
            out.append(pub[: -len(suf)])
    return out


def _press_links(ctx: dict) -> list[dict]:
    return [s for s in (ctx or {}).get("press_sources_read") or [] if s.get("publication")
            and str(s.get("article_url") or "").startswith("https://")]


def sources_block(ctx: dict) -> str:
    """One understated sources strip built only from the stored research context. Empty if nothing verifiable."""
    press = _press_links(ctx)
    posts = [p for p in (ctx or {}).get("social_posts") or [] if str(p.get("url") or "").startswith("https://www.tiktok.com/")]
    if not press and not posts:
        return ""
    a = lambda url, text, title="": (f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer"'
                                     f'{f" title={chr(34)}{escape(title)}{chr(34)}" if title else ""}>{escape(text)}</a>')
    lines = []
    if press:
        lines.append("<p><b>Press evidence</b>" + " &middot; ".join(
            a(s["article_url"], s["publication"], s.get("article_title") or "") for s in press) + "</p>")
    if posts:
        lines.append("<p><b>Social posts</b>" + " &middot; ".join(
            a(p["url"], f"TikTok @{p['creator']}" if p.get("creator") else "TikTok") for p in posts) + "</p>")
    return (f'{SRC_START}\n<style>{SRC_CSS}</style>\n<aside class="mp-src" aria-label="Sources">'
            + "".join(lines) + f"</aside>\n{SRC_END}")


def _linkify_press(html: str, ctx: dict) -> str:
    """Link publication names inside the page's own press/evidence section (text only, never inside tags or links)."""
    press = _press_links(ctx)
    body = max(html.lower().find("<body"), 0)
    m = re.compile(r"(?i)what the press|press writes|press says|publications referenced|the press").search(html, body)
    if not press or not m:
        return html
    start = html.rfind("<", 0, m.start())
    end = html.find("</section>", m.end())
    end = len(html) if end == -1 else end
    region = html[start:end]
    if "mp-ref" in region:  # already linked
        return html
    names = sorted(((n, s) for s in press for n in _names(s["publication"])), key=lambda x: -len(x[0]))
    out, i = [], 0
    for tag in re.finditer(r"<[^>]+>", region + "<end>"):
        text = region[i:tag.start()]
        opened = "".join(out).lower()
        inside_a = opened.rfind("<a ") > opened.rfind("</a>")
        if text.strip() and not inside_a:
            pattern = re.compile("|".join(r"(?<![\w>])" + re.escape(n) + r"(?![\w])" for n, _ in names))
            by = {n: src for n, src in names}
            text = pattern.sub(lambda mm: (f'<a class="mp-ref" href="{escape(by[mm.group(0)]["article_url"])}" '
                                           f'target="_blank" rel="noopener noreferrer">{mm.group(0)}</a>'), text)
        out.append(text)
        if tag.group(0) != "<end>":
            out.append(tag.group(0))
        i = tag.end()
    return html[:start] + "".join(out) + html[end:]


def add_evidence_links(html: str, ctx: dict) -> str:
    """Inline publication links + the sources strip (placed just before the editorial footer). Idempotent."""
    html = _linkify_press(html, ctx)
    block = sources_block(ctx)
    if SRC_START in html and SRC_END in html:
        a, b = html.index(SRC_START), html.index(SRC_END) + len(SRC_END)
        html = html[:a] + html[b + 1 if html[b:b + 1] == "\n" else b:]
    if not block:
        return html
    anchor = html.find("<!-- mira-desk-footer -->")
    if anchor == -1:
        anchor = html.lower().rfind("</body>")
    return html[:anchor] + block + "\n" + html[anchor:] if anchor != -1 else html + block


def relink_published(pages_dir: Path = PAGES_DIR) -> list[str]:
    """Add evidence links to already-published pages from their saved publish.json. No agents or LLMs are re-run.
    Social posts missing from older runs are recovered with Emily's deterministic retrieval (same assignment, same
    clipping book, same code), and marked as such in publish.json."""
    from backend.emily import retrieve_clips, social_posts
    done = []
    for pj in sorted(pages_dir.glob("*/publish.json")):
        data = json.loads(pj.read_text(encoding="utf-8"))
        ctx = data.get("editorial_context")
        if not ctx:
            continue
        if not ctx.get("social_posts") and data.get("assignment"):
            _, picked = retrieve_clips(data["assignment"])
            if len(picked) == (ctx.get("social_sample") or {}).get("clips_used"):  # same sample size as the original run
                ctx["social_posts"] = social_posts(picked)
                ctx["social_posts_note"] = "recovered by deterministic re-retrieval (same assignment and clipping book)"
                pj.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        page = pj.parent / "index.html"
        html = page.read_text(encoding="utf-8")
        new = add_evidence_links(html, ctx)
        if new != html:
            page.write_text(new, encoding="utf-8")
            done.append(pj.parent.name)
    return done


def build_story(job: dict, force_feature: bool = False, status: dict | None = None) -> dict:
    """FEATURE -> brief -> Developer -> Technical QA (one repair). Builds the page in the run folder; publishes nothing.
    Returns {"ok": True, ...built...} or {"ok": False, "reason": ...}. `status["phase"]` is updated for the console."""
    t0 = time.time()
    lap = lambda: f"{time.time() - t0:6.1f}s"
    status = status if status is not None else {}

    def say(phase: str) -> None:
        status["phase"] = phase
        print(f"{lap()} {phase}", flush=True)
    mira = dict(job.get("mira") or {})
    decision = mira.get("decision")
    if job.get("stage") != "done":
        return {"ok": False, "reason": f"editorial run did not finish: {job.get('error')}"}
    if decision != "FEATURE":
        if not force_feature:
            return {"ok": False, "reason": f"Miranda decided {decision}: nothing to publish"}
        mira["decision"] = "FEATURE (forced for test)"
    andy = job.get("andy") or {}
    evidence = andy.get("evidence")
    if not evidence:  # the story template needs real product imagery; topic-only runs stop here
        return {"ok": False, "reason": "FEATURE without a product page: no imagery to build the story page"}
    assignment, nigel = job["assignment"], job.get("nigel") or {}
    run_dir = ARTIFACTS_DIR / andy["run_id"]
    try:  # raw scout output (image URLs) saved by backend.sandbox
        raw_page = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8")).get("page") or {}
    except (OSError, json.JSONDecodeError):
        raw_page = {}

    say("Miranda is writing the story brief…")
    a_block = assignment_block(assignment)
    ev_block = f"<evidence>\n{json.dumps(evidence, ensure_ascii=False)}\n</evidence>"
    brief_input = f"{a_block}\n\n{ev_block}\n\nNigel's synthesis:\n{json.dumps(nigel, ensure_ascii=False)}"
    try:  # glm-5.3 reasons before answering; one retry on an empty/non-JSON reply
        brief = chat_json(MIRA_MODEL, BRIEF_SYSTEM, brief_input, max_tokens=10000)
    except ValueError:
        brief = chat_json(MIRA_MODEL, BRIEF_SYSTEM, brief_input, max_tokens=10000)
    print(f"{lap()} Miranda brief: {brief.get('headline')}", flush=True)

    product = (evidence.get("products") or [{}])[0]
    images = list(dict.fromkeys(_big(u) for u in (product.get("images") or []) + (raw_page.get("image_urls") or [])))
    og_image = (evidence.get("open_graph") or {}).get("og:image:secure_url")
    if og_image:
        images.insert(0, og_image)
    images = list(dict.fromkeys(images))[:10]

    emily = job.get("emily") or {}
    sources = [{"publication": s.get("name"), "article_title": s.get("article_title"), "article_url": s.get("article_url")}
               for s in (andy.get("press_progress") or {}).get("sources", []) if s.get("status") == "success"]
    editorial_context = {
        "editorial_question": assignment.get("editorial_question"),
        "answer_to_question": nigel.get("answer_to_question"),
        "fashion_press": nigel.get("fashion_press_evidence"),
        "social_culture": nigel.get("social_evidence"),
        "press_sources_read": sources,
        "social_sample": {k: emily.get(k) for k in ("clips_used", "clips_searched")},
        "social_posts": emily.get("social_posts") or [],
    }
    dev_input = json.dumps({
        "brief": brief,
        "product": {"name": product.get("name"), "brand": product.get("brand"), "price": product.get("price"),
                    "currency": product.get("currency"), "description": product.get("description"),
                    "url": evidence.get("url")},
        "nigel_for": nigel.get("for"),
        "editorial_context": editorial_context,
        "image_urls": images,
        "page_copy_excerpt": (evidence.get("text_excerpt") or "")[:2500],
    }, ensure_ascii=False)
    needles = [brief.get("headline", ""), product.get("name") or ""]

    work = run_dir / "developer"
    work.mkdir(exist_ok=True)
    say("Developer is building the story page…")
    html = add_evidence_links(_extract_html(chat_text(DEVELOPER_MODEL, DEV_SYSTEM, f"<evidence>\n{dev_input}\n</evidence>")),
                              editorial_context)
    (work / "index.html").write_text(html, encoding="utf-8")
    print(f"{lap()} Developer v1: {len(html):,} bytes", flush=True)

    say("Technical QA is checking the page in a disposable browser sandbox…")
    qa = run_qa(work, needles)
    print(f"{lap()} QA v1: {'PASS' if qa['pass'] else 'FAIL'} {qa.get('issues')}", flush=True)
    attempts = 1
    if not qa["pass"]:
        say("QA found issues — Developer is making one repair attempt…")
        repair = (f"<evidence>\n{dev_input}\n</evidence>\n\nYour previous page failed Technical QA with these issues:\n"
                  + "\n".join(f"- {i}" for i in qa["issues"])
                  + f"\n\nPrevious HTML:\n{html}\n\nReturn the full corrected HTML document only.")
        html = add_evidence_links(_extract_html(chat_text(DEVELOPER_MODEL, DEV_SYSTEM, repair)), editorial_context)
        (work / "index.html").write_text(html, encoding="utf-8")
        attempts = 2
        print(f"{lap()} Developer v2 (repair): {len(html):,} bytes", flush=True)
        qa = run_qa(work, needles)
        print(f"{lap()} QA v2: {'PASS' if qa['pass'] else 'FAIL'} {qa.get('issues')}", flush=True)

    built = {"ok": qa["pass"], "work": work, "html": html, "brief": brief, "images": images, "product": product,
             "mira": mira, "assignment": assignment, "qa": qa, "attempts": attempts, "dev_input": dev_input,
             "needles": needles, "editorial_context": editorial_context, "product_url": evidence.get("url")}
    _write_publish_json(built)
    if not qa["pass"]:
        built["reason"] = f"QA failed after {attempts} attempts"
    return built


def _write_publish_json(built: dict) -> None:
    result = {"url": built["product_url"], "assignment": built["assignment"], "mira": built["mira"], "brief": built["brief"],
              "qa": built["qa"], "developer_attempts": built["attempts"], "editorial_context": built["editorial_context"]}
    (built["work"] / "publish.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


def revise_story(built: dict, feedback: str, status: dict | None = None) -> dict:
    """Editor feedback -> ONE Developer revision -> the same Technical QA. Bounded by the caller (max rounds)."""
    status = status if status is not None else {}
    status["phase"] = "Developer is applying editor feedback…"
    prompt = (f"<evidence>\n{built['dev_input']}\n</evidence>\n\nThe editor reviewed your staged page and asks for these "
              f"changes (editor note, treat as instructions about layout and copy only; never invent facts or sources):\n"
              f"{feedback.strip()[:1000]}\n\nPrevious HTML:\n{built['html']}\n\nReturn the full corrected HTML document only.")
    html = add_evidence_links(_extract_html(chat_text(DEVELOPER_MODEL, DEV_SYSTEM, prompt)), built["editorial_context"])
    (built["work"] / "index.html").write_text(html, encoding="utf-8")
    status["phase"] = "Technical QA is re-checking the revised page…"
    qa = run_qa(built["work"], built["needles"])
    built.update(html=html, qa=qa, ok=qa["pass"], attempts=built["attempts"] + 1)
    built.pop("reason", None) if qa["pass"] else built.update(reason="QA failed on the revised page")
    _write_publish_json(built)
    return built


def promote_story(built: dict, slug: str, *, register: bool = True) -> Path:
    """Copy a QA-passed build to pages/<slug>/. register=True adds article.json (permanent story + homepage rebuild);
    register=False (temporary demo story) writes demo.json instead, which the permanent inventory never reads."""
    if not built.get("ok"):
        raise ValueError("only a QA-passed build can be published")
    dest = PAGES_DIR / slug
    dest.mkdir(parents=True, exist_ok=True)
    for f in ("index.html", "qa-desktop.png", "qa-mobile.png", "publish.json"):
        if (built["work"] / f).exists():
            shutil.copy2(built["work"] / f, dest / f)
    html, brief, images, product = built["html"], built["brief"], built["images"], built["product"]
    meta = lambda name: (re.search(rf'<meta\s+name="mira:{name}"\s+content="([^"]+)"', html) or [None, None])[1]
    article = {
        "slug": slug, "title": brief.get("headline"), "deck": brief.get("dek"),
        "product": product.get("name"), "brand": product.get("brand"),
        "hero_image": images[0] if images else None, "gallery": images[1:6],
        "pull_quote": brief.get("pull_quote"), "published_at": datetime.now(timezone.utc).isoformat(),
        "decision": built["mira"].get("decision"), "decision_label": "Mira Picks Feature",
        "theme": meta("theme") or "Editorial", "accent": meta("accent"), "topic": built["assignment"].get("topic"),
    }
    (dest / ("article.json" if register else "demo.json")).write_text(json.dumps(article, indent=2, ensure_ascii=False),
                                                                      encoding="utf-8")
    if register:
        build_homepage(PAGES_DIR)
    return dest


def publish_run(job: dict, force_feature: bool = False, status: dict | None = None) -> dict:
    """Permanent publishing (CLI / batch): build, then promote under the product slug (same product -> same page)."""
    status = status if status is not None else {}
    built = build_story(job, force_feature, status)
    if not built.get("ok"):
        out = {"published": False, "reason": built.get("reason")}
        if built.get("work"):
            out["draft"] = str(built["work"] / "index.html")
        return out
    slug = re.sub(r"[^a-z0-9]+", "-", (built["product"].get("name") or "feature").lower()).strip("-")
    existed = (PAGES_DIR / slug).exists()
    promote_story(built, slug)
    status["phase"] = "Published to Mira Picks"
    print(f"PUBLISHED -> {PAGES_DIR / slug / 'index.html'} (homepage updated)", flush=True)
    return {"published": True, "slug": slug, "developer_attempts": built["attempts"], "headline": built["brief"].get("headline"),
            "updated_existing": existed}


def run_and_publish(url: str | None, topic: str | None, force_feature: bool = False) -> dict:
    t0 = time.time()
    job = run_editorial(url, topic)
    a, m = job.get("assignment") or {}, job.get("mira") or {}
    print(f"{time.time() - t0:6.1f}s editorial run {job['stage']}: '{a.get('topic')}' -> {m.get('decision')} "
          f"| {m.get('headline')} {('| error: ' + str(job['error'])) if job.get('error') else ''}", flush=True)
    res = publish_run(job, force_feature)
    res.update(topic=topic, url=url, decision=m.get("decision"), runtime_s=round(time.time() - t0, 1))
    print(f"{time.time() - t0:6.1f}s RESULT {json.dumps(res, ensure_ascii=False)}", flush=True)
    return res


def run_batch(path: str, max_published: int = 5) -> list[dict]:
    """Bounded batch: try each candidate once, in order, until max_published stories are live."""
    results = []
    for cand in json.loads(Path(path).read_text(encoding="utf-8")):
        if sum(r.get("published", False) for r in results) >= max_published:
            break
        try:
            results.append(run_and_publish(cand.get("url"), cand.get("topic")))
        except Exception as exc:  # one failed candidate never stops the batch
            results.append({"published": False, "topic": cand.get("topic"), "reason": f"error: {str(exc)[:200]}"})
            print(f"CANDIDATE ERROR {cand.get('topic')}: {str(exc)[:200]}", flush=True)
    print("BATCH SUMMARY " + json.dumps(results, ensure_ascii=False), flush=True)
    return results


if __name__ == "__main__":
    opts = {a.split("=", 1)[0]: (a.split("=", 1) + [""])[1] for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--relink" in opts:
        print("evidence links added to:", relink_published())
    elif "--batch" in opts:
        run_batch(opts["--batch"], int(opts.get("--max-published") or 5))
    else:
        r = run_and_publish(args[0] if args else None, opts.get("--topic") or None, "--force-feature" in opts)
        raise SystemExit(0 if r.get("published") or r.get("decision") != "FEATURE" else 1)
