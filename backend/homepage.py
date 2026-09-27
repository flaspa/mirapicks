"""Regenerate the Mira Picks front page from pages/*/article.json.

Usage: python -m backend.homepage [pages_dir]
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path

from backend.sandbox import REPO_ROOT

PAGES_DIR = REPO_ROOT / "pages"
BRANDING_DIR = REPO_ROOT / "assets" / "branding"  # canonical favicon source
FAVICONS = ("favicon-32.png", "favicon-48.png", "favicon-512.png")
FAVICON_TAGS = (
    '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">\n'
    '<link rel="icon" type="image/png" sizes="48x48" href="/favicon-48.png">\n'
    '<link rel="icon" type="image/png" sizes="512x512" href="/favicon-512.png">'
)


def ensure_favicon(html: str) -> str:
    """Insert the Mira Picks favicon tags right after <head> if they are missing."""
    if 'href="/favicon-32.png"' in html:
        return html
    m = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    return html[: m.end()] + "\n" + FAVICON_TAGS + html[m.end():] if m else html


# ---------------- Canonical editorial footer ("The Desk"), shared by the homepage and every Pick page ----------------

DESK_ROLES = [
    ("Andy", "Fashion Press Scout",
     "Reads the product when one is supplied, then scans the fashion press for evidence, signals and contradictions."),
    ("Emily", "Social Culture Scout",
     "Reads social culture through her TikTok clipping book and surfaces the signals people are actually amplifying."),
    ("Nigel", "Fashion Director",
     "Weighs press and social evidence, identifies agreements and contradictions, and states his confidence."),
    ("Miranda", "Editor-in-Chief",
     "Sets the editorial assignment, then makes the final call &mdash; feature, watch or pass. Only features are published."),
]
DESK_ACCENT = "#c2185b"  # the publication pink: same footer on every page, whatever the story's accent
DESK_START, DESK_END = "<!-- mira-desk-footer -->", "<!-- /mira-desk-footer -->"
# Self-contained, class-scoped styles so a Developer-generated page's own CSS cannot restyle the footer.
DESK_CSS = """
.mp-desk,.mp-desk *,.mp-foot,.mp-foot *{box-sizing:border-box;margin:0;padding:0;border:0;text-align:left;text-transform:none;letter-spacing:normal;font-style:normal;background:none;}
.mp-desk{display:grid;grid-template-columns:minmax(0,4fr) minmax(0,8fr);gap:clamp(32px,5vw,80px);background:#111;color:#f5f2ed;
  padding:clamp(64px,8vw,120px) clamp(20px,4vw,56px);font-family:'Inter',system-ui,sans-serif;-webkit-font-smoothing:antialiased;}
.mp-desk h2{font-family:'Cormorant Garamond',Georgia,serif;font-weight:500;font-size:clamp(40px,5vw,76px);line-height:.95;color:#f5f2ed;}
.mp-desk h2 em{font-style:italic;color:var(--mp-accent);}
.mp-desk .mp-roles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:24px;}
.mp-desk .mp-role{border-top:1px solid rgba(245,242,237,.3);padding-top:18px;}
.mp-desk .mp-role b{display:block;font-family:'Cormorant Garamond',Georgia,serif;font-size:28px;font-weight:500;margin-bottom:4px;color:#f5f2ed;}
.mp-desk .mp-role small{display:block;font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:#a9a39a;margin-bottom:14px;}
.mp-desk .mp-role p{font-size:14px;line-height:1.6;color:#d8d2c8;}
.mp-foot{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:28px clamp(20px,4vw,56px);background:#f5f2ed;
  font-family:'Inter',system-ui,sans-serif;font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#6f6a63;}
.mp-foot a{color:inherit;text-decoration:none;letter-spacing:inherit;text-transform:inherit;}
@media (max-width:1200px){.mp-desk{grid-template-columns:1fr;}}
@media (max-width:900px){.mp-desk .mp-roles{grid-template-columns:repeat(2,minmax(0,1fr));gap:32px 24px;}}
@media (max-width:560px){.mp-desk .mp-roles{grid-template-columns:1fr;}}
"""
DESK_FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;1,500'
              '&family=Inter:wght@400;500&display=swap">')


def desk_footer(accent: str | None = None, year: int | None = None) -> str:
    """The Desk section plus the bottom bar, as one self-contained block."""
    accent = accent if accent and re.fullmatch(r"#[0-9a-fA-F]{3,8}", accent) else DESK_ACCENT
    year = year or datetime.now(timezone.utc).year
    roles = "".join(f'<div class="mp-role"><b>{n}</b><small>{t}</small><p>{d}</p></div>' for n, t, d in DESK_ROLES)
    return (f'{DESK_START}\n{DESK_FONTS}\n<style>{DESK_CSS}</style>\n'
            f'<section class="mp-desk" id="desk" style="--mp-accent:{accent}">\n'
            f'  <h2>Edited by agents.<br><em>Decided by Miranda.</em></h2>\n'
            f'  <div class="mp-roles">{roles}</div>\n</section>\n'
            f'<footer class="mp-foot"><span><a href="/">&copy; {year} Mira Picks</a></span>'
            f'<span>Built on Vultr &middot; Served via NetBird</span></footer>\n{DESK_END}')


def ensure_desk_footer(html: str) -> str:
    """Give a Pick page the canonical footer: replace a previous copy, or the page's own last <footer>. Content untouched."""
    block = desk_footer()
    if DESK_START in html and DESK_END in html:
        a, b = html.index(DESK_START), html.index(DESK_END) + len(DESK_END)
        return html[:a] + block + html[b:]
    footers = list(re.finditer(r"<footer\b[^>]*>.*?</footer>", html, re.IGNORECASE | re.DOTALL))
    if footers:
        f = footers[-1]
        return html[:f.start()] + block + html[f.end():]
    end = html.lower().rfind("</body>")
    return html[:end] + block + "\n" + html[end:] if end != -1 else html + block


# ---------------- Canonical site header, shared by every Pick/story page (the homepage keeps its front-page masthead) ----

HEAD_START, HEAD_END = "<!-- mira-site-header -->", "<!-- /mira-site-header -->"
HEAD_CSS = """
.site-header,.site-header *{box-sizing:border-box;margin:0;padding:0;border:0;background:none;}
.site-header{display:block;position:relative;z-index:100;background:#f5f2ed;border-bottom:1px solid #d9d3ca;
  padding:18px clamp(20px,4vw,56px);text-align:center;}
.site-header .mp-masthead{display:inline-block;font-family:'Cormorant Garamond',Georgia,serif;font-weight:500;
  font-size:clamp(20px,2.2vw,28px);line-height:1.2;letter-spacing:.18em;text-transform:uppercase;color:#111;text-decoration:none;}
.site-header .mp-masthead:hover{color:#6f6a63;}
"""


def site_header() -> str:
    return (f'{HEAD_START}\n{DESK_FONTS}\n<style>{HEAD_CSS}</style>\n'
            f'<header class="site-header"><a class="mp-masthead" href="/">Mira Picks</a></header>\n{HEAD_END}')


def ensure_site_header(html: str) -> str:
    """Canonical header at the top of <body>. Replaces a previous copy, or the page's own Mira Picks masthead."""
    block = site_header()
    if HEAD_START in html and HEAD_END in html:
        a, b = html.index(HEAD_START), html.index(HEAD_END) + len(HEAD_END)
        return html[:a] + block + html[b:]
    body = re.search(r"<body\b[^>]*>", html, re.IGNORECASE)
    if not body:
        return html
    first = re.compile(r"<header\b[^>]*>.*?</header>", re.IGNORECASE | re.DOTALL).search(html, body.end())
    # Only replace the Developer's own masthead: the first <header> after <body>, and only if it is the Mira Picks mark.
    if first and "mira picks" in re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", first.group(0)))).lower():
        return html[:first.start()] + block + html[first.end():]
    return html[:body.end()] + "\n" + block + html[body.end():]


def ensure_shell(html: str) -> str:
    """Every generated publication page: canonical header + content + canonical footer."""
    return ensure_site_header(ensure_desk_footer(html))


def apply_footers(pages_dir: Path = PAGES_DIR) -> list[str]:
    """Add/refresh the canonical shell (header + footer) on every published Pick page (index.html only)."""
    done = []
    for page in sorted(pages_dir.glob("*/index.html")):
        html = page.read_text(encoding="utf-8")
        new = ensure_shell(html)
        if new != html:
            page.write_text(new, encoding="utf-8")
            done.append(page.parent.name)
    return done


def copy_favicons(pages_dir: Path) -> None:
    for name in FAVICONS:
        if (BRANDING_DIR / name).exists():
            shutil.copy2(BRANDING_DIR / name, pages_dir / name)


def load_articles(pages_dir: Path) -> list[dict]:
    items = []
    for f in pages_dir.glob("*/article.json"):
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda a: a.get("published_at", ""), reverse=True)


def _date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d %B %Y").lstrip("0")
    except ValueError:
        return ""


def _card(a: dict, idx: int) -> str:
    e = lambda k: escape(str(a.get(k) or ""))
    return f"""
      <a class="card" href="/{e('slug')}/">
        <div class="card-img"><img src="{e('hero_image')}" alt="{e('title')}" loading="lazy"></div>
        <div class="card-meta"><span>No. {idx:02d}</span><span>{e('brand')}</span></div>
        <h3>{e('title')}</h3>
        <p>{e('deck')}</p>
      </a>"""


def render(articles: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    issue_date = now.strftime("%A %d %B %Y").replace(" 0", " ")
    if not articles:
        lead_html, more_html, strip = "<p class='empty'>The first story is being written.</p>", "", ""
        accent = "#b5543a"
    else:
        lead = articles[0]
        e = lambda k: escape(str(lead.get(k) or ""))
        accent = lead.get("accent") or "#b5543a"
        # The strip under the verdict shows the next three REAL published stories (not product-page gallery images):
        # each tile is built from the same article records as The Edit and links to that story's own page.
        strip = "".join(
            f'<a class="strip-img" href="/{escape(str(a.get("slug") or ""))}/"><img src="{escape(str(a.get("hero_image") or ""))}" '
            f'alt="{escape(str(a.get("title") or ""))}" loading="lazy"><span class="strip-cap">{escape(str(a.get("title") or ""))}</span></a>'
            for a in articles[1:4] if a.get("slug") and a.get("hero_image"))
        lead_html = f"""
    <section class="lead" id="cover">
      <a class="lead-img" href="/{e('slug')}/"><img src="{e('hero_image')}" alt="{e('title')}"></a>
      <div class="lead-copy">
        <div class="kicker"><span class="dot"></span>Cover Story &middot; {e('decision_label')}</div>
        <h1><a href="/{e('slug')}/">{e('title')}</a></h1>
        <p class="deck">{e('deck')}</p>
        <div class="byline">{e('product')} &mdash; {e('brand')}<br><span>{_date(lead.get('published_at', ''))} &middot; Art direction: {e('theme')}</span></div>
        <a class="read" href="/{e('slug')}/">Read the story <span aria-hidden="true">&rarr;</span></a>
      </div>
    </section>
    <section class="verdict">
      <div class="verdict-label">Mira&rsquo;s verdict</div>
      <blockquote>&ldquo;{e('pull_quote')}&rdquo;</blockquote>
      <div class="strip">{strip}</div>
    </section>"""
        # Only real published stories: no placeholder cards, whatever the count.
        more_html = "".join(_card(a, i + 1) for i, a in enumerate(articles))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mira Picks &mdash; The Edit</title>
{FAVICON_TAGS}
<meta name="description" content="Mira Picks: an AI-edited fashion publication. Only the pieces that earn a feature.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{ --ink:#111; --paper:#f5f2ed; --muted:#6f6a63; --line:#d9d3ca; --accent:{escape(accent)};
          --serif:'Cormorant Garamond', Georgia, serif; --sans:'Inter', system-ui, sans-serif; --gutter:clamp(20px,4vw,56px); }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  html {{ background:var(--paper); }}
  body {{ font-family:var(--sans); color:var(--ink); background:var(--paper); overflow-x:hidden; -webkit-font-smoothing:antialiased; }}
  a {{ color:inherit; text-decoration:none; }}
  img {{ display:block; width:100%; height:100%; object-fit:cover; }}

  .topbar {{ display:flex; justify-content:space-between; gap:16px; padding:12px var(--gutter); font-size:11px;
             letter-spacing:.14em; text-transform:uppercase; color:var(--muted); border-bottom:1px solid var(--line); }}
  .masthead {{ text-align:center; padding:clamp(28px,5vw,64px) var(--gutter) clamp(16px,2.5vw,28px); }}
  .masthead a {{ font-family:var(--serif); font-weight:500; font-size:clamp(56px,13vw,196px); line-height:.85;
                 letter-spacing:-.02em; text-transform:uppercase; display:inline-block; }}
  .tagline {{ margin-top:14px; font-family:var(--serif); font-style:italic; font-size:clamp(16px,1.6vw,21px); color:var(--muted); }}
  nav {{ position:sticky; top:0; z-index:5; background:rgba(245,242,237,.92); backdrop-filter:blur(8px);
         border-top:1px solid var(--ink); border-bottom:1px solid var(--line); }}
  nav ul {{ list-style:none; display:flex; justify-content:center; gap:clamp(18px,4vw,48px); padding:14px var(--gutter);
            font-size:12px; letter-spacing:.18em; text-transform:uppercase; flex-wrap:wrap; }}
  nav a:hover, nav a.on {{ color:var(--accent); }}

  .lead {{ display:grid; grid-template-columns:minmax(0,7fr) minmax(0,5fr); min-height:86vh; border-bottom:1px solid var(--line); }}
  .lead-img {{ overflow:hidden; background:#e8e3dc; }}
  .lead-img img {{ transition:transform 1.2s cubic-bezier(.2,.7,.2,1); }}
  .lead-img:hover img {{ transform:scale(1.035); }}
  .lead-copy {{ display:flex; flex-direction:column; justify-content:center; padding:clamp(32px,5vw,88px) var(--gutter); }}
  .kicker {{ display:flex; align-items:center; gap:10px; font-size:11px; letter-spacing:.2em; text-transform:uppercase; color:var(--accent); font-weight:600; }}
  .dot {{ width:8px; height:8px; border-radius:50%; background:var(--accent); }}
  .lead h1 {{ font-family:var(--serif); font-weight:500; font-size:clamp(44px,5.6vw,92px); line-height:.95; letter-spacing:-.015em; margin:22px 0 26px; }}
  .lead h1 a:hover {{ color:var(--accent); }}
  .deck {{ font-family:var(--serif); font-size:clamp(20px,1.8vw,26px); line-height:1.35; color:#2a2723; max-width:34ch; }}
  .byline {{ margin-top:32px; padding-top:18px; border-top:1px solid var(--line); font-size:12px; letter-spacing:.14em; text-transform:uppercase; }}
  .byline span {{ color:var(--muted); letter-spacing:.08em; text-transform:none; font-size:13px; line-height:2; }}
  .read {{ margin-top:34px; align-self:flex-start; font-size:12px; letter-spacing:.2em; text-transform:uppercase; font-weight:600;
           padding:16px 26px; background:var(--ink); color:var(--paper); transition:background .3s; }}
  .read:hover {{ background:var(--accent); }}

  .verdict {{ padding:clamp(64px,9vw,140px) var(--gutter); text-align:center; border-bottom:1px solid var(--line); }}
  .verdict-label {{ font-size:11px; letter-spacing:.24em; text-transform:uppercase; color:var(--muted); }}
  .verdict blockquote {{ font-family:var(--serif); font-style:italic; font-size:clamp(32px,4.6vw,72px); line-height:1.05;
                         max-width:18ch; margin:24px auto 0; }}
  .strip {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:clamp(8px,1.2vw,18px); margin-top:clamp(48px,6vw,88px); }}
  .strip-img {{ display:block; position:relative; aspect-ratio:4/5; overflow:hidden; background:#e8e3dc; color:inherit; text-decoration:none; }}
  .strip-cap {{ position:absolute; left:0; right:0; bottom:0; padding:28px 14px 12px; font-family:var(--serif); font-size:clamp(15px,1.4vw,20px);
               line-height:1.15; color:#fff; background:linear-gradient(transparent, rgba(0,0,0,.55)); }}

  .section-head {{ display:flex; justify-content:space-between; align-items:baseline; padding:clamp(48px,6vw,88px) var(--gutter) 28px; }}
  .section-head h2 {{ font-family:var(--serif); font-weight:500; font-size:clamp(36px,4vw,60px); }}
  .section-head span {{ font-size:11px; letter-spacing:.2em; text-transform:uppercase; color:var(--muted); }}
  .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:clamp(20px,2.5vw,40px); padding:0 var(--gutter) clamp(64px,8vw,120px); }}
  .card {{ display:flex; flex-direction:column; }}
  .card-img {{ aspect-ratio:4/5; overflow:hidden; margin-bottom:18px; background:#e8e3dc; }}
  .card-meta {{ display:flex; justify-content:space-between; font-size:11px; letter-spacing:.18em; text-transform:uppercase; color:var(--muted);
                padding-bottom:12px; border-bottom:1px solid var(--line); margin-bottom:16px; }}
  .card h3 {{ font-family:var(--serif); font-weight:500; font-size:clamp(26px,2.2vw,34px); line-height:1.05; margin-bottom:10px; }}
  .card p {{ color:#3b3732; line-height:1.6; font-size:15px; }}
  .card:hover h3 {{ color:var(--accent); }}
  .placeholder {{ border:1px solid var(--line); padding:clamp(24px,3vw,40px); justify-content:flex-end; min-height:360px;
                  background:linear-gradient(160deg, transparent 55%, rgba(0,0,0,.035)); }}

  .empty {{ padding:120px var(--gutter); text-align:center; font-family:var(--serif); font-size:32px; }}

  @media (max-width: 900px) {{
    .lead {{ grid-template-columns:1fr; min-height:0; }}
    .lead-img {{ aspect-ratio:4/5; }}
    .grid {{ grid-template-columns:1fr; }}
    .desk {{ grid-template-columns:1fr; }}
    .topbar span:last-child {{ display:none; }}
  }}
  @media (max-width: 560px) {{ .strip {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .strip-img:nth-child(3) {{ display:none; }} }}
</style>
</head>
<body>
  <div class="topbar"><span>{escape(issue_date)}</span><span>An AI-edited fashion publication</span></div>
  <header class="masthead">
    <a href="/">Mira Picks</a>
    <p class="tagline">Only the pieces that earn a feature.</p>
  </header>
  <nav><ul>
    <li><a class="on" href="#cover">Cover</a></li>
    <li><a href="#latest">Latest</a></li>
    <li><a href="#desk">The Desk</a></li>
  </ul></nav>
  <main>
    {lead_html}
    <div class="section-head" id="latest"><h2>The Edit</h2><span>{len(articles)} feature{'s' if len(articles) != 1 else ''} published</span></div>
    <div class="grid">{more_html}
    </div>
  </main>
  {desk_footer(None, now.year)}
</body>
</html>
"""


def build_homepage(pages_dir: Path = PAGES_DIR) -> Path:
    copy_favicons(pages_dir)
    out = pages_dir / "index.html"
    out.write_text(render(load_articles(pages_dir)), encoding="utf-8")
    return out


if __name__ == "__main__":
    # python -m backend.homepage [pages_dir] [--apply-footers]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = Path(args[0]) if args else PAGES_DIR
    if "--apply-footers" in sys.argv or "--apply-shell" in sys.argv:
        print("header + footer applied to:", apply_footers(target))
    print(build_homepage(target))
