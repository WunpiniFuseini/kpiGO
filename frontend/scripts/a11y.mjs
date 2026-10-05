// Accessibility gate (Design Brief §8): every Storybook story, which covers every
// component state and every screen, is checked with axe-core against WCAG 2.1 AA
// in a real browser, so colour contrast is measured, not guessed. Screens are also
// checked at phone width for horizontal page scroll (§9).
//
//   npm run build-storybook && npm run a11y
//
// KPIGO_CHROMIUM points at a Chromium binary when Playwright's own is not installed.
import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";
import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";

const ROOT = new URL("../storybook-static/", import.meta.url).pathname;
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".woff": "font/woff" };

if (!existsSync(join(ROOT, "index.json"))) {
  console.error("storybook-static/ is missing: run `npm run build-storybook` first.");
  process.exit(2);
}

const server = createServer((req, res) => {
  const path = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^(\.\.[/\\])+/, "");
  let file = join(ROOT, path);
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, "index.html");
  if (!existsSync(file)) {
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream" });
  createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const index = JSON.parse(readFileSync(join(ROOT, "index.json"), "utf8"));
const stories = Object.values(index.entries).filter((e) => e.type === "story");
const screen = (s) => s.title.startsWith("Pages/") || s.title.startsWith("Shell/");

const browser = await chromium.launch(process.env.KPIGO_CHROMIUM ? { executablePath: process.env.KPIGO_CHROMIUM } : {});
const failures = [];

async function open(page, story) {
  const errors = [];
  page.removeAllListeners("pageerror");
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${base}/iframe.html?id=${story.id}&viewMode=story`);
  await page.waitForSelector("#storybook-root > *, #storybook-root ~ *:not(script)", { timeout: 15000 });
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
  return errors;
}

const desktop = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const phone = await (await browser.newContext({ viewport: { width: 390, height: 844 } })).newPage();
for (const story of stories) {
  const label = `${story.title} › ${story.name}`;
  try {
    const errors = await open(desktop, story);
    if (errors.length) failures.push(`${label}: page error: ${errors.join("; ")}`);
    const result = await new AxeBuilder({ page: desktop }).include("#storybook-root").withTags(TAGS).analyze();
    for (const v of result.violations) {
      const targets = v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ");
      failures.push(`${label}: ${v.id} (${v.impact}) ${v.help} at ${targets}`);
    }
    if (screen(story)) {
      await open(phone, story);
      const width = await phone.evaluate(() => document.documentElement.scrollWidth);
      if (width > 391) failures.push(`${label}: scrolls horizontally at phone width (${width}px)`);
    }
    process.stdout.write(".");
  } catch (e) {
    failures.push(`${label}: ${e.message.split("\n")[0]}`);
  }
}
await browser.close();
server.close();

console.log(`\n${stories.length} stories checked against ${TAGS.join(", ")}.`);
if (failures.length) {
  console.error(`${failures.length} problem(s):\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("No accessibility violations.");
