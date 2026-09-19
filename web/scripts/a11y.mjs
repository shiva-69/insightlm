// Accessibility (axe-core, WCAG 2.1 A/AA) on every page, plus phone-width screenshots.
//   node scripts/a11y.mjs <out-dir> <demo.json>
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";

const [OUT, DEMO] = process.argv.slice(2);
const demo = JSON.parse(await readFile(DEMO, "utf8"));
const WEB = "http://localhost:3000";
const API = "http://localhost:8000";

async function token(email) {
  const r = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password: demo.password }),
  });
  return (await r.json()).access_token;
}

const app = demo.application_ids[0];
const pages = {
  owner: ["/ask", "/documents", "/hiring", `/hiring/${demo.requisition_id}`, `/hiring/${demo.requisition_id}?candidate=${app}`,
          `/applications/${app}`, "/talent", "/team", "/audit", "/governance"],
  interviewer: ["/interviews"],
  anonymous: ["/login", "/join?token=x"],
};

const browser = await chromium.launch();
const results = [];
for (const [role, paths] of Object.entries(pages)) {
  const email = role === "anonymous" ? null : demo.emails[role];
  const tok = email ? await token(email) : null;
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const ctx = await browser.newContext({ viewport });
    if (tok) await ctx.addInitScript(([t, e]) => { sessionStorage.setItem("insightlm.token", t); sessionStorage.setItem("insightlm.email", e); }, [tok, email]);
    const page = await ctx.newPage();
    for (const path of paths) {
      await page.goto(WEB + path, { waitUntil: "networkidle" });
      await page.waitForTimeout(800);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (viewport.width === 390) {
        await page.screenshot({ path: `${OUT}/m-${path.replace(/[^a-z0-9]+/gi, "_").slice(0, 40)}.png` });
      }
      const axe = viewport.width === 1440
        ? await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze()
        : { violations: [] };
      results.push({ role, path, width: viewport.width, overflow, violations: axe.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help, target: v.nodes[0]?.target?.join(" ") })) });
    }
    await ctx.close();
  }
}
await browser.close();
for (const r of results) {
  if (r.width === 1440 && r.violations.length) {
    console.log(`${r.path}`);
    for (const v of r.violations) console.log(`   ${v.impact.padEnd(9)} ${v.id} x${v.nodes}  (${v.target})`);
  }
  if (r.width === 390 && r.overflow > 0) console.log(`OVERFLOW at 390px: ${r.path} by ${r.overflow}px`);
}
const total = results.reduce((n, r) => n + r.violations.length, 0);
console.log(`\n${results.filter((r) => r.width === 1440).length} pages checked; ${total} violation types`);
