import { chromium } from "playwright";

const OUT = process.argv[2];
const SUFFIX = Math.random().toString(36).slice(2, 7);
const EMAIL = `priya+${SUFFIX}@acme.com`;
const PASSWORD = "correct-horse-battery-staple";
const CV = process.argv[3];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));

const shot = async (name) => { await page.waitForTimeout(500); await page.screenshot({ path: `${OUT}/${name}.png` }); };

await page.goto("http://localhost:3000/login", { waitUntil: "networkidle" });
await page.waitForTimeout(900);
await shot("01-login");

await page.getByRole("button", { name: "Create one" }).click();
await page.getByPlaceholder("Acme Talent").fill(`Acme Talent ${SUFFIX}`);
await page.getByPlaceholder("Priya Sharma").fill("Priya Sharma");
await page.getByPlaceholder("you@company.com").fill(EMAIL);
await page.getByPlaceholder("••••••••••••").fill(PASSWORD);
await shot("02-create-workspace");
await page.getByRole("button", { name: "Create workspace" }).click();
await page.waitForURL("**/ask", { timeout: 25000 });
await shot("03-ask-empty");

await page.goto("http://localhost:3000/documents", { waitUntil: "networkidle" });
await shot("04-library-empty");

await page.setInputFiles('input[type="file"]', CV);
await page.waitForTimeout(1200);
await shot("05-library-processing");

// Wait for ingestion to complete.
for (let i = 0; i < 40; i++) {
  const ready = await page.locator("text=/\\d+ page/").count();
  if (ready > 0) break;
  await page.waitForTimeout(1000);
}
await shot("06-library-ready");

await page.goto("http://localhost:3000/ask", { waitUntil: "networkidle" });
await page.waitForTimeout(800);
await page.locator("textarea").fill("What payment systems experience is there?");
await shot("07-ask-typed");
await page.getByRole("button", { name: "Ask", exact: true }).click();

await page.waitForSelector("text=Evidence", { timeout: 30000 });
await page.waitForTimeout(2500);
await shot("08-answer");

// Click a citation chip and capture the highlight
const chip = page.locator("button", { hasText: /^C1$/ }).first();
if (await chip.count()) { await chip.click(); await page.waitForTimeout(900); }
await shot("09-citation-active");

await page.goto("http://localhost:3000/team", { waitUntil: "networkidle" });
await shot("10-team");
await page.getByPlaceholder("colleague@company.com").fill("arjun@acme.com");
await page.getByRole("button", { name: "Create invitation" }).click();
await page.waitForTimeout(1200);
await shot("11-team-invited");

await page.goto("http://localhost:3000/audit", { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Verify chain" }).click();
await page.waitForTimeout(1200);
await shot("12-audit");

// Dark theme on the main view
await page.goto("http://localhost:3000/ask", { waitUntil: "networkidle" });
await page.evaluate(() => { document.documentElement.setAttribute("data-theme", "dark"); localStorage.setItem("insightlm.theme", "dark"); });
await page.locator("textarea").fill("What was reduced by batching ledger writes?");
await page.getByRole("button", { name: "Ask", exact: true }).click();
await page.waitForSelector("text=Evidence", { timeout: 30000 });
await page.waitForTimeout(2500);
await shot("13-dark-answer");

console.log(problems.length ? problems.join("\n") : "no console errors");
await browser.close();
