/**
 * Mira Scout: inspects one web page inside a disposable, locked-down container.
 *
 * Contract:
 *   input  : one http(s) URL as argv[2]
 *   output : one bounded JSON object on stdout (ScoutEvidence)
 *   files  : /out/screenshot.png (viewport screenshot, never embedded in stdout)
 *   exit   : 0 on success, 1 on handled failure (JSON still printed), 2 on bad usage
 *
 * The Scout holds no secrets and makes no LLM calls. Everything it returns is
 * untrusted page data and must be validated again by the orchestrator.
 */
import { chromium, type Browser } from "playwright";
import { createHash } from "node:crypto";
import { statSync } from "node:fs";

const OUT_DIR = process.env.SCOUT_OUT_DIR ?? "/out";
const SCREENSHOT_NAME = "screenshot.png";
const NAV_TIMEOUT_MS = 45_000;
const SETTLE_MS = 2_000;
const HARD_DEADLINE_MS = 110_000; // two navigation attempts plus extraction
const MAX_STDOUT_BYTES = 64 * 1024;

// Per-field bounds. Keep in sync with the orchestrator's schema.
const LIMITS = {
  url: 2048,
  title: 300,
  description: 1000,
  metaValue: 500,
  headingCount: 20,
  heading: 200,
  textExcerpt: 4000,
  products: 5,
  productField: 500,
  productImages: 5,
  images: 10,
  links: 0, // links are intentionally not collected in milestone 1
} as const;

type Product = {
  name: string | null;
  brand: string | null;
  description: string | null;
  sku: string | null;
  price: string | null;
  currency: string | null;
  availability: string | null;
  images: string[];
};

type ScoutEvidence = {
  schema_version: 1;
  ok: boolean;
  error: string | null;
  requested_url: string;
  final_url: string | null;
  http_status: number | null;
  fetched_at: string;
  duration_ms: number;
  page: {
    title: string | null;
    description: string | null;
    canonical: string | null;
    lang: string | null;
    open_graph: Record<string, string>;
    headings: { level: number; text: string }[];
    text_excerpt: string | null;
    image_urls: string[];
  } | null;
  products: Product[];
  content_sha256: string | null;
  screenshot: { file: string; bytes: number; width: number; height: number } | null;
  truncated: boolean;
};

let truncated = false;

function clip(value: unknown, max: number): string | null {
  if (value === null || value === undefined) return null;
  const s = String(value).replace(/\s+/g, " ").trim();
  if (!s) return null;
  if (s.length > max) {
    truncated = true;
    return s.slice(0, max);
  }
  return s;
}

function isHttpUrl(raw: string): boolean {
  try {
    const u = new URL(raw);
    return (u.protocol === "http:" || u.protocol === "https:") && raw.length <= LIMITS.url;
  } catch {
    return false;
  }
}

function emit(evidence: ScoutEvidence): void {
  let json = JSON.stringify(evidence);
  if (Buffer.byteLength(json) > MAX_STDOUT_BYTES) {
    // Last-resort guard: drop the bulkiest fields rather than exceed the budget.
    evidence.truncated = true;
    if (evidence.page) evidence.page.text_excerpt = null;
    evidence.products = evidence.products.slice(0, 1);
    json = JSON.stringify(evidence);
  }
  process.stdout.write(json + "\n");
}

const PRODUCT_TYPES = new Set(["product", "productgroup"]);

/** Pull schema.org Product / ProductGroup objects out of JSON-LD blocks. */
function extractProducts(jsonLdBlocks: string[]): Product[] {
  const found: Product[] = [];
  const visit = (node: unknown): void => {
    if (found.length >= LIMITS.products || node === null || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.forEach(visit);
      return;
    }
    const obj = node as Record<string, unknown>;
    const type = obj["@type"];
    const types = Array.isArray(type) ? type : [type];
    if (types.some((t) => typeof t === "string" && PRODUCT_TYPES.has(t.toLowerCase()))) {
      const firstOf = (v: unknown) => (Array.isArray(v) ? v[0] : v) as Record<string, unknown> | undefined;
      // ProductGroup often carries price only on its variants.
      const offer = firstOf(obj["offers"]) ?? firstOf(firstOf(obj["hasVariant"])?.["offers"]);
      const brandRaw = obj["brand"];
      const brand =
        brandRaw && typeof brandRaw === "object" ? (brandRaw as Record<string, unknown>)["name"] : brandRaw;
      const imgRaw = obj["image"];
      const imgs = (Array.isArray(imgRaw) ? imgRaw : imgRaw ? [imgRaw] : [])
        .map((i) => (i && typeof i === "object" ? (i as Record<string, unknown>)["url"] : i))
        .filter((i): i is string => typeof i === "string" && isHttpUrl(i))
        .slice(0, LIMITS.productImages);
      found.push({
        name: clip(obj["name"], LIMITS.productField),
        brand: clip(brand, LIMITS.productField),
        description: clip(obj["description"], LIMITS.productField),
        sku: clip(obj["sku"], 100),
        price: clip(offer?.["price"] ?? offer?.["lowPrice"], 50),
        currency: clip(offer?.["priceCurrency"], 10),
        availability: clip(offer?.["availability"], 100),
        images: imgs,
      });
    }
    for (const key of ["@graph", "mainEntity", "itemListElement", "item"]) {
      if (key in obj) visit(obj[key]);
    }
  };
  for (const block of jsonLdBlocks) {
    try {
      visit(JSON.parse(block));
    } catch {
      // Malformed JSON-LD is common; ignore it.
    }
  }
  return found;
}

async function inspect(url: string): Promise<ScoutEvidence> {
  const started = Date.now();
  const evidence: ScoutEvidence = {
    schema_version: 1,
    ok: false,
    error: null,
    requested_url: url,
    final_url: null,
    http_status: null,
    fetched_at: new Date().toISOString(),
    duration_ms: 0,
    page: null,
    products: [],
    content_sha256: null,
    screenshot: null,
    truncated: false,
  };

  let browser: Browser | undefined;
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 1280, height: 800 },
      acceptDownloads: false,
      serviceWorkers: "block",
      javaScriptEnabled: true,
      userAgent:
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36 MiraScout/0.1",
    });
    // Only http(s) subresources; drop anything else (file:, ftp:, etc.).
    await context.route("**/*", (route) => {
      const u = route.request().url();
      return u.startsWith("http://") || u.startsWith("https://") || u.startsWith("data:")
        ? route.continue()
        : route.abort();
    });

    const page = await context.newPage();
    const nav = () => page.goto(url, { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT_MS });
    let response;
    try {
      response = await nav();
    } catch (err) {
      if (!(err instanceof Error && err.name === "TimeoutError")) throw err;
      response = await nav(); // one retry on timeout only
    }
    await page.waitForLoadState("networkidle", { timeout: SETTLE_MS * 3 }).catch(() => undefined);
    await page.waitForTimeout(SETTLE_MS);

    evidence.http_status = response?.status() ?? null;
    evidence.final_url = clip(page.url(), LIMITS.url);

    const html = await page.content();
    evidence.content_sha256 = createHash("sha256").update(html).digest("hex");

    const raw = await page.evaluate(() => {
      const meta = (sel: string) => document.querySelector(sel)?.getAttribute("content") ?? null;
      const og: Record<string, string> = {};
      document.querySelectorAll('meta[property^="og:"]').forEach((m) => {
        const k = m.getAttribute("property");
        const v = m.getAttribute("content");
        if (k && v && Object.keys(og).length < 15) og[k] = v;
      });
      const headings = Array.from(document.querySelectorAll("h1, h2, h3"))
        .slice(0, 40)
        .map((h) => ({ level: Number(h.tagName.slice(1)), text: (h as HTMLElement).innerText ?? "" }));
      const images = Array.from(document.images)
        .filter((img) => img.naturalWidth >= 200 && img.naturalHeight >= 200)
        .slice(0, 30)
        .map((img) => img.currentSrc || img.src);
      return {
        title: document.title,
        description: meta('meta[name="description"]'),
        canonical: document.querySelector('link[rel="canonical"]')?.getAttribute("href") ?? null,
        lang: document.documentElement.getAttribute("lang"),
        og,
        headings,
        text: (document.body?.innerText ?? "").slice(0, 20000),
        images,
        jsonLd: Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
          .slice(0, 20)
          .map((s) => (s.textContent ?? "").slice(0, 200000)),
      };
    });

    const openGraph: Record<string, string> = {};
    for (const [k, v] of Object.entries(raw.og)) {
      const key = clip(k, 50);
      const val = clip(v, LIMITS.metaValue);
      if (key && val) openGraph[key] = val;
    }

    evidence.page = {
      title: clip(raw.title, LIMITS.title),
      description: clip(raw.description, LIMITS.description),
      canonical: raw.canonical && isHttpUrl(raw.canonical) ? raw.canonical : null,
      lang: clip(raw.lang, 20),
      open_graph: openGraph,
      headings: raw.headings
        .map((h) => ({ level: h.level, text: clip(h.text, LIMITS.heading) ?? "" }))
        .filter((h, i, all) => h.text && all.findIndex((o) => o.text.toLowerCase() === h.text.toLowerCase()) === i)
        .slice(0, LIMITS.headingCount),
      text_excerpt: clip(raw.text, LIMITS.textExcerpt),
      image_urls: [...new Set(raw.images.filter(isHttpUrl))].slice(0, LIMITS.images),
    };
    evidence.products = extractProducts(raw.jsonLd);

    // Best effort: many marketing modals close on Escape. Never click page content.
    await page.keyboard.press("Escape").catch(() => undefined);
    await page.waitForTimeout(500);

    const shotPath = `${OUT_DIR}/${SCREENSHOT_NAME}`;
    await page.screenshot({ path: shotPath, fullPage: false, type: "png" });
    evidence.screenshot = { file: SCREENSHOT_NAME, bytes: statSync(shotPath).size, width: 1280, height: 800 };

    evidence.ok = true;
  } catch (err) {
    evidence.error = clip(err instanceof Error ? err.message : String(err), 500);
  } finally {
    await browser?.close().catch(() => undefined);
    evidence.duration_ms = Date.now() - started;
    evidence.truncated = evidence.truncated || truncated;
  }
  return evidence;
}

async function main(): Promise<void> {
  const url = process.argv[2] ?? "";
  if (!isHttpUrl(url)) {
    process.stderr.write("usage: scout <http(s) url>\n");
    process.exit(2);
  }
  const deadline = setTimeout(() => {
    emit({
      schema_version: 1,
      ok: false,
      error: `hard deadline of ${HARD_DEADLINE_MS} ms exceeded`,
      requested_url: url,
      final_url: null,
      http_status: null,
      fetched_at: new Date().toISOString(),
      duration_ms: HARD_DEADLINE_MS,
      page: null,
      products: [],
      content_sha256: null,
      screenshot: null,
      truncated: false,
    });
    process.exit(1);
  }, HARD_DEADLINE_MS);

  const evidence = await inspect(url);
  clearTimeout(deadline);
  emit(evidence);
  process.exit(evidence.ok ? 0 : 1);
}

void main();
