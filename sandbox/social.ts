/**
 * Emily's Social Scout extractor. Runs inside the same disposable, locked-down container as Andy.
 * Input: one public social/fashion URL. Output: one bounded JSON object on stdout; /out/screenshot.png.
 * Public access only: login walls, CAPTCHAs and blocks are reported as "blocked", never bypassed.
 */
import { chromium } from "playwright";
import { createHash } from "node:crypto";

const OUT = process.env.SCOUT_OUT_DIR ?? "/out";
const NAV_TIMEOUT_MS = 40_000;
const clip = (v: unknown, n: number): string | null => {
  if (v === null || v === undefined) return null;
  const s = String(v).replace(/\s+/g, " ").trim();
  return s ? s.slice(0, n) : null;
};

function platformOf(host: string): string {
  if (/(^|\.)instagram\.com$/.test(host)) return "instagram";
  if (/(^|\.)tiktok\.com$/.test(host)) return "tiktok";
  if (/(^|\.)pinterest\.[a-z.]+$/.test(host) || host === "pin.it") return "pinterest";
  return "web";
}

async function main(): Promise<void> {
  const url = process.argv[2] ?? "";
  const started = Date.now();
  const base = { schema_version: 1, requested_url: url, fetched_at: new Date().toISOString() };
  let platform = "web";
  try {
    platform = platformOf(new URL(url).hostname.toLowerCase());
  } catch {
    process.stdout.write(JSON.stringify({ ...base, status: "error", error: "invalid URL" }) + "\n");
    process.exit(1);
  }

  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({
      viewport: { width: 1280, height: 900 }, acceptDownloads: false, serviceWorkers: "block", locale: "en-US",
      userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36 MiraScout/0.1",
    });
    const page = await ctx.newPage();
    const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT_MS });
    await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => undefined);
    await page.waitForTimeout(2_500);
    await page.keyboard.press("Escape").catch(() => undefined);
    await page.waitForTimeout(400);

    const raw = await page.evaluate(() => {
      const meta = (k: string) =>
        document.querySelector(`meta[property="${k}"], meta[name="${k}"]`)?.getAttribute("content") ?? null;
      const origin = location.hostname.replace(/^www\./, "");
      const links = Array.from(document.querySelectorAll("a[href]"))
        .map((a) => (a as HTMLAnchorElement).href)
        .filter((h) => h.startsWith("http") && !new URL(h).hostname.replace(/^www\./, "").endsWith(origin));
      const imgs = Array.from(document.images).filter((i) => i.naturalWidth >= 300).map((i) => i.currentSrc || i.src);
      return {
        title: document.title,
        og_title: meta("og:title"), og_description: meta("og:description"), description: meta("description"),
        og_image: meta("og:image"), author_meta: meta("author") ?? meta("twitter:creator") ?? meta("article:author"),
        published_meta: meta("article:published_time") ?? meta("og:updated_time"),
        time_el: document.querySelector("time[datetime]")?.getAttribute("datetime") ?? null,
        jsonld: Array.from(document.querySelectorAll('script[type="application/ld+json"]')).slice(0, 10).map((s) => (s.textContent ?? "").slice(0, 50000)),
        text: (document.body?.innerText ?? "").slice(0, 15000),
        links: [...new Set(links)].slice(0, 15),
        images: [...new Set(imgs)].slice(0, 8),
      };
    });

    const finalUrl = page.url();
    const status = resp?.status() ?? 0;
    const lowerText = raw.text.toLowerCase();
    const blocked =
      [401, 403, 429].includes(status) ||
      /\/(accounts\/)?login|\/challenge|captcha/i.test(new URL(finalUrl).pathname) ||
      (/captcha|verify you are human|unusual traffic/.test(lowerText) && raw.text.length < 2000);

    // Structured data: author, date and engagement only when the page actually states them.
    let author: string | null = null, published: string | null = null;
    const engagement: Record<string, string> = {};
    for (const block of raw.jsonld) {
      try {
        const nodes = ([] as any[]).concat(JSON.parse(block)).flatMap((n: any) => n?.["@graph"] ?? [n]);
        for (const n of nodes) {
          author ??= clip(typeof n?.author === "object" ? (n.author?.name ?? n.author?.[0]?.name) : n?.author, 120);
          published ??= clip(n?.datePublished ?? n?.uploadDate ?? n?.dateCreated, 40);
          for (const s of ([] as any[]).concat(n?.interactionStatistic ?? [])) {
            const kind = String(s?.interactionType?.["@type"] ?? s?.interactionType ?? "").split("/").pop();
            if (kind && s?.userInteractionCount !== undefined) engagement[kind] = String(s.userInteractionCount);
          }
        }
      } catch { /* ignore malformed JSON-LD */ }
    }
    const desc = raw.og_description ?? raw.description ?? "";
    // Instagram/TikTok public meta often reads "1,234 likes, 56 comments - user on date: caption".
    for (const [k, re] of [["likes", /([\d.,]+[KMB]?)\s+likes/i], ["comments", /([\d.,]+[KMB]?)\s+comments/i],
                            ["followers", /([\d.,]+[KMB]?)\s+followers/i], ["posts", /([\d.,]+[KMB]?)\s+posts/i]] as const) {
      const m = desc.match(re);
      if (m && !engagement[k]) engagement[k] = m[1];
    }

    const caption = clip(desc || raw.text, 1500);
    const hashtags = [...new Set((`${desc} ${raw.text}`.match(/#[\p{L}\p{N}_]{2,40}/gu) ?? []))].slice(0, 20);
    const excerpt = clip(raw.text, 2500);
    const images = [...new Set([raw.og_image, ...raw.images].filter((u): u is string => !!u && u.startsWith("http")))].slice(0, 6);

    await page.screenshot({ path: `${OUT}/screenshot.png` });
    const usable = !!(caption && caption.length > 40) || images.length > 0;

    process.stdout.write(JSON.stringify({
      ...base,
      status: blocked ? "blocked" : usable ? "success" : "empty",
      platform, final_url: clip(finalUrl, 2048), http_status: status,
      title: clip(raw.og_title ?? raw.title, 300),
      author: author ?? clip(raw.author_meta, 120),
      caption, hashtags, engagement,
      published_at: published ?? clip(raw.published_meta ?? raw.time_el, 40),
      outbound_links: raw.links.map((l) => clip(l, 500)),
      image_urls: images,
      text_excerpt: excerpt,
      content_sha256: createHash("sha256").update(`${raw.og_title ?? raw.title}\n${caption ?? ""}`).digest("hex"),
      screenshot: "screenshot.png",
      duration_ms: Date.now() - started,
    }) + "\n");
  } catch (err) {
    process.stdout.write(JSON.stringify({ ...base, status: "error", platform, error: clip(String(err), 400),
                                          duration_ms: Date.now() - started }) + "\n");
  } finally {
    await browser.close();
  }
}

void main();
