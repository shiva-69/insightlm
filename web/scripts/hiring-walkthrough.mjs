// Drives the hiring flow end to end in a real browser and screenshots each step.
//   node scripts/hiring-walkthrough.mjs <out-dir> <cv.pdf> <cv.pdf>
// Run it against a keyless stack: it assesses with the offline judge and costs nothing.
import { chromium } from "playwright";

const [OUT, ...CVS] = process.argv.slice(2);
const SUFFIX = Math.random().toString(36).slice(2, 7);
const BASE = "http://localhost:3000";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
const shot = async (name) => { await page.waitForTimeout(600); await page.screenshot({ path: `${OUT}/${name}.png` }); };

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Create one" }).click();
await page.getByPlaceholder("Acme Talent").fill(`Acme Talent ${SUFFIX}`);
await page.getByPlaceholder("Priya Sharma").fill("Priya Sharma");
await page.getByPlaceholder("you@company.com").fill(`priya+${SUFFIX}@acme.com`);
await page.getByPlaceholder("••••••••••••").fill("correct-horse-battery-staple");
await page.getByRole("button", { name: "Create workspace" }).click();
await page.waitForURL("**/ask", { timeout: 25000 });

await page.goto(`${BASE}/documents`, { waitUntil: "networkidle" });
await page.setInputFiles('input[type="file"]', CVS);
for (let i = 0; i < 60; i++) {
  if ((await page.locator("text=/\\d+ page/").count()) >= CVS.length) break;
  await page.waitForTimeout(1000);
}

await page.goto(`${BASE}/hiring`, { waitUntil: "networkidle" });
await shot("h01-hiring-empty");
await page.getByPlaceholder("Senior Payments Engineer").fill("Senior Payments Engineer");
await page.getByPlaceholder("Owns settlement").fill("Owns settlement and reconciliation for card payments.");
await page.getByRole("button", { name: "Open", exact: true }).click();
await page.waitForURL("**/hiring/*");
await page.waitForLoadState("networkidle");

const criteria = [
  ["Python backend services", "must_have", "services built and run in Python", true],
  ["Payment processing", "strong_preference", "settlement, reconciliation, card payments", false],
  ["Kubernetes in production", "nice_to_have", "", false],
];
for (const [index, [label, kind, hint, flag]] of criteria.entries()) {
  if (index > 0) await page.getByRole("button", { name: "+ Add criterion" }).click();
  const row = page.locator("ol > li").nth(index);
  await row.getByLabel(`Criterion ${index + 1}`).fill(label);
  await row.getByRole("radio", { name: { must_have: "Must have", strong_preference: "Strong preference", nice_to_have: "Nice to have" }[kind] }).click();
  if (hint) await row.getByLabel("Evidence hint").fill(hint);
  if (flag) await row.getByLabel("Flag for review if absent").check();
}
await shot("h02-rubric-editor");
await page.getByRole("button", { name: "Propose for approval" }).click();
await page.getByRole("button", { name: /Approve v1/ }).waitFor();
await shot("h03-rubric-draft");
await page.getByRole("button", { name: /Approve v1/ }).click();
await page.getByText("Approved v1").waitFor();

for (const [name, match] of [["Asha Rao", /asha/i], ["Ben Okafor", /ben/i]]) {
  await page.getByRole("button", { name: "+ Add candidate" }).click();
  await page.getByPlaceholder("Asha Rao").fill(name);
  await page.locator("label", { hasText: match }).locator('input[type="checkbox"]').check();
  if (name === "Asha Rao") await shot("h04-add-candidate");
  await page.getByRole("button", { name: "Add candidate", exact: true }).click();
  await page.getByText(name).waitFor();
}
await shot("h05-candidates-unassessed");

for (let i = 0; i < 2; i++) {
  await page.getByRole("button", { name: "Assess", exact: true }).first().click();
  await page.waitForTimeout(2500);
}
await page.waitForLoadState("networkidle");
await shot("h06-shortlist");

await page.getByRole("button", { name: "Evidence" }).first().click();
await page.waitForURL("**?candidate=*");
await page.waitForTimeout(2500);
await shot("h07-assessment-evidence");

await page.getByRole("button", { name: /Payment processing/ }).click();
await page.waitForTimeout(1500);
await shot("h08-assessment-second-criterion");

await page.getByRole("button", { name: "← All candidates" }).click();
await page.getByRole("button", { name: "Evidence" }).nth(1).click();
await page.waitForTimeout(2500);
await shot("h09-flagged-candidate");

await browser.close();
console.log(problems.length ? problems.join("\n") : "no console errors");
