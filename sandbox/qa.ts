/**
 * Technical QA: opens /out/index.html in a disposable browser and checks the basics.
 * Prints one JSON object on stdout; writes qa-desktop.png and qa-mobile.png to /out.
 * Usage: qa "<required text 1>" "<required text 2>" ...
 */
import { chromium } from "playwright";

const OUT = process.env.SCOUT_OUT_DIR ?? "/out";
const required = process.argv.slice(2).filter(Boolean);

async function main(): Promise<void> {
  const issues: string[] = [];
  const pageErrors: string[] = [];
  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => pageErrors.push(e.message.slice(0, 200)));

    await page.goto(`file://${OUT}/index.html`, { waitUntil: "load", timeout: 30_000 });
    await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
    // Trigger lazy images, then return to top.
    await page.evaluate(async () => {
      for (let y = 0; y < document.body.scrollHeight; y += 600) { window.scrollTo(0, y); await new Promise((r) => setTimeout(r, 120)); }
      window.scrollTo(0, 0);
    });
    await page.waitForTimeout(1500);

    const desk = await page.evaluate((needles: string[]) => {
      const imgs = Array.from(document.images);
      const broken = imgs.filter((i) => i.complete && i.naturalWidth === 0).map((i) => i.src.slice(0, 120));
      const loaded = imgs.filter((i) => i.naturalWidth > 0);
      const widest = Math.max(0, ...loaded.map((i) => i.getBoundingClientRect().width));
      const text = document.body.innerText.toLowerCase();
      return {
        title: document.title,
        textLength: text.length,
        imgTotal: imgs.length,
        imgLoaded: loaded.length,
        broken,
        widest,
        overflowX: document.documentElement.scrollWidth - window.innerWidth,
        missing: needles.filter((n) => !text.includes(n.toLowerCase())),
        shell: (() => {
          // Publication shell: canonical navigation header (nav.site-nav: Cover / Latest / The Desk) + content + footer.
          const header = document.querySelector("nav.site-nav");
          const links = header ? Array.from(header.querySelectorAll("a")) as HTMLAnchorElement[] : [];
          const find = (label: string) => links.find((l) => (l.textContent ?? "").trim().toLowerCase() === label);
          const expect: [string, string][] = [["cover", "/#cover"], ["latest", "/#latest"], ["the desk", "/#desk"]];
          const r = header?.getBoundingClientRect();
          const cs = header ? getComputedStyle(header) : null;
          const desk = document.querySelector(".mp-desk"), foot = document.querySelector(".mp-foot");
          const vis = (e: Element | null) => ((e as HTMLElement | null)?.innerText ?? "").replace(/\s+/g, " ").length;
          const chrome = vis(header) + vis(desk) + vis(foot);
          return {
            header: !!header,
            navLinks: expect.map(([label]) => !!find(label)),
            navHrefs: expect.map(([label, href]) => find(label)?.getAttribute("href") === href),
            navVisible: !!(r && r.width > 0 && r.height > 0 && cs && cs.visibility !== "hidden" && cs.display !== "none" && Number(cs.opacity) > 0),
            contentChars: vis(document.body) - chrome,
            footer: !!desk && !!foot && /decided by miranda/i.test(desk?.textContent ?? ""),
          };
        })(),
      };
    }, required);
    await page.screenshot({ path: `${OUT}/qa-desktop.png` });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(800);
    const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    await page.screenshot({ path: `${OUT}/qa-mobile.png` });

    if (pageErrors.length) issues.push(`JavaScript runtime errors: ${pageErrors.slice(0, 3).join(" | ")}`);
    if (desk.textLength < 300) issues.push("Page has almost no visible text content.");
    if (desk.imgLoaded === 0) issues.push("No images rendered.");
    if (desk.broken.length) issues.push(`Broken images (${desk.broken.length}): ${desk.broken.slice(0, 3).join(", ")}`);
    if (desk.widest < 600) issues.push(`No large hero image: widest rendered image is ${Math.round(desk.widest)}px at 1440px viewport.`);
    if (desk.overflowX > 4) issues.push(`Horizontal overflow of ${desk.overflowX}px on desktop.`);
    if (mobileOverflow > 4) issues.push(`Horizontal overflow of ${mobileOverflow}px on a 390px mobile viewport.`);
    if (desk.missing.length) issues.push(`Required story text missing: ${desk.missing.join("; ")}`);
    const sh = desk.shell;
    if (!sh.header) issues.push("Publication shell: canonical navigation header (nav.site-nav) missing.");
    if (sh.header && !sh.navLinks.every(Boolean)) issues.push("Publication shell: navigation must contain Cover, Latest and The Desk links.");
    if (sh.header && !sh.navHrefs.every(Boolean)) issues.push("Publication shell: navigation links must point to /#cover, /#latest and /#desk.");
    if (sh.header && !sh.navVisible) issues.push("Publication shell: navigation header is not visible.");
    if (sh.contentChars < 300) issues.push("Publication shell: no main story/product content between header and footer.");
    if (!sh.footer) issues.push("Publication shell: canonical editorial footer (.mp-desk + .mp-foot, 'Decided by Miranda') missing.");

    process.stdout.write(JSON.stringify({
      pass: issues.length === 0, issues,
      metrics: { ...desk, mobileOverflow, pageErrors: pageErrors.length },
      screenshots: ["qa-desktop.png", "qa-mobile.png"],
    }) + "\n");
  } catch (err) {
    process.stdout.write(JSON.stringify({ pass: false, issues: [`Page failed to load: ${String(err).slice(0, 300)}`] }) + "\n");
  } finally {
    await browser.close();
  }
}

void main();
