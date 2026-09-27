/**
 * Andy's fashion-press reader. One publication per disposable container run.
 * Usage: press <source_url> <term> [term...]
 * Visits the source homepage, scores internal links against the assignment terms (deterministic),
 * follows AT MOST ONE strongly relevant article, and prints one bounded JSON object on stdout.
 * Blocked / paywalled / CAPTCHA pages are reported, never bypassed.
 */
import { chromium, type Page } from "playwright";

const NAV_TIMEOUT_MS = 20_000;
const url = process.argv[2] ?? "";
const terms = process.argv.slice(3).map((t) => t.toLowerCase()).filter((t) => t.length >= 3);
const clip = (v: unknown, n: number) => (v == null ? null : String(v).replace(/\s+/g, " ").trim().slice(0, n) || null);
const BLOCK_RE = /just a moment|access denied|attention required|captcha|are you a robot|verify you are human|unusual traffic|enable javascript and cookies|request blocked/i;
const PAYWALL_RE = /subscribe to (continue|read)|subscriber[- ]only|already a subscriber|to continue reading|create a free account to continue/i;

const TERM_RES = terms.map((t) => ({ multi: t.includes(" "), re: new RegExp(`\\b${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i") }));
function score(text: string): number {
  let s = 0;
  for (const { multi, re } of TERM_RES) if (re.test(text)) s += multi ? 3 : 1;  // whole words only
  return s;
}

async function load(page: Page, target: string) {
  const resp = await page.goto(target, { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT_MS });
  await page.waitForTimeout(1500);
  await page.keyboard.press("Escape").catch(() => undefined);
  const status = resp?.status() ?? 0;
  const info = await page.evaluate(() => ({ title: document.title, text: (document.body?.innerText ?? "").slice(0, 20000) }));
  const blocked = [401, 402, 403, 429].includes(status) || (BLOCK_RE.test(info.title + " " + info.text.slice(0, 1500)) && info.text.length < 3000);
  return { status, blocked, ...info };
}

async function main(): Promise<void> {
  const started = Date.now();
  const out: Record<string, unknown> = { requested_url: url, fetched_at: new Date().toISOString() };
  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({
      viewport: { width: 1280, height: 900 }, acceptDownloads: false, serviceWorkers: "block", locale: "en-US",
      userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36 MiraScout/0.1",
    });
    const page = await ctx.newPage();
    const home = await load(page, url);
    out.http_status = home.status;
    out.final_url = page.url();
    if (home.blocked || home.text.length < 200) {
      Object.assign(out, { status: "blocked", reason: home.blocked ? `access blocked (HTTP ${home.status})` : "page unreadable" });
      return;
    }
    const host = new URL(page.url()).hostname.replace(/^www\./, "");
    const homeData = await page.evaluate(() => ({
      headings: Array.from(document.querySelectorAll("h1, h2, h3")).map((h) => (h as HTMLElement).innerText).slice(0, 60),
      links: Array.from(document.querySelectorAll("a[href]")).map((a) => ({
        href: (a as HTMLAnchorElement).href, text: ((a as HTMLElement).innerText || a.getAttribute("aria-label") || "").trim(),
      })).slice(0, 800),
    }));
    const seen = new Set<string>();
    const candidates = homeData.links
      .filter((l) => {
        try {
          const u = new URL(l.href);
          const ok = u.hostname.replace(/^www\./, "").endsWith(host) && u.pathname.split("/").filter(Boolean).length >= 1
            && l.text.length >= 20 && !seen.has(u.pathname);
          if (ok) seen.add(u.pathname);
          return ok;
        } catch { return false; }
      })
      .map((l) => ({ url: l.href.split("#")[0], title: clip(l.text, 200)!, score: score(l.text + " " + decodeURIComponent(new URL(l.href).pathname).replace(/[-_/]/g, " ")) }))
      .sort((a, b) => b.score - a.score);
    const relevant = candidates.filter((c) => c.score > 0).slice(0, 8);
    Object.assign(out, {
      homepage_title: clip(home.title, 200),
      headings: homeData.headings.map((h) => clip(h, 160)).filter(Boolean).slice(0, 12),
      relevant_links: relevant,
      article_links_seen: candidates.length,
    });

    const best = relevant[0];
    if (best && best.score >= 1) {
      const art = await load(page, best.url).catch((e) => ({ status: 0, blocked: true, title: "", text: String(e) }));
      if (art.blocked) {
        out.article = { url: best.url, title: best.title, status: "blocked" };
      } else {
        const a = await page.evaluate(() => {
          const meta = (k: string) => document.querySelector(`meta[property="${k}"], meta[name="${k}"]`)?.getAttribute("content") ?? null;
          return {
            h1: (document.querySelector("h1") as HTMLElement | null)?.innerText ?? null,
            date: meta("article:published_time") ?? document.querySelector("time[datetime]")?.getAttribute("datetime") ?? null,
            description: meta("og:description") ?? meta("description"),
            paragraphs: Array.from(document.querySelectorAll("article p, main p, p")).map((p) => (p as HTMLElement).innerText.trim())
              .filter((t) => t.length > 60).slice(0, 80),
          };
        });
        const hits = a.paragraphs.filter((p) => score(p) > 0);
        const snippets = (hits.length ? hits : a.paragraphs).slice(0, 5).map((p) => clip(p, 400));
        const paywalled = PAYWALL_RE.test(art.text.slice(0, 5000)) && a.paragraphs.join(" ").length < 1200;
        out.article = {
          url: page.url(), title: clip(a.h1 ?? art.title, 200), published_at: clip(a.date, 40),
          description: clip(a.description, 300), snippets, link_score: best.score,
          status: paywalled ? "paywalled" : "read",
        };
      }
    }
    out.status = out.article && (out.article as { status: string }).status === "read" ? "success"
      : relevant.length ? "success" : "no_relevant_evidence";
  } catch (err) {
    Object.assign(out, { status: "error", reason: clip(String(err), 300) });
  } finally {
    out.duration_ms = Date.now() - started;
    await browser.close().catch(() => undefined);
    process.stdout.write(JSON.stringify(out) + "\n");
  }
}

void main();
