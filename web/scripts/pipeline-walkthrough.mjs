// Two people, two roles, one pipeline. Seeds a workspace over the API, then drives the
// browser as the owner and as an interviewer and screenshots what each one sees.
//   node scripts/pipeline-walkthrough.mjs <out-dir> <strong-cv.pdf> <weak-cv.pdf>
// Run against a keyless stack: everything here is offline and free.
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const [OUT, STRONG, WEAK] = process.argv.slice(2);
const API = "http://localhost:8000";
const WEB = "http://localhost:3000";
const SUFFIX = Math.random().toString(36).slice(2, 7);
const PASSWORD = "correct-horse-battery-staple";

async function call(token, method, path, body) {
  const init = { method, headers: token ? { Authorization: `Bearer ${token}` } : {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(`${API}${path}`, init);
  const json = await response.json().catch(() => null);
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status} ${JSON.stringify(json)}`);
  return json;
}
const login = async (email) => (await call(null, "POST", "/auth/login", { email, password: PASSWORD })).access_token;

// -- seed ---------------------------------------------------------------------------
const ownerEmail = `priya+${SUFFIX}@acme.com`;
await call(null, "POST", "/auth/register", {
  tenant_name: `Acme ${SUFFIX}`, tenant_slug: `acme-${SUFFIX}`, email: ownerEmail,
  display_name: "Priya Sharma", password: PASSWORD,
});
const owner = await login(ownerEmail);
const adaEmail = `ada+${SUFFIX}@acme.com`;
const invitation = await call(owner, "POST", "/members/invitations", { email: adaEmail, role: "interviewer" });
const ada = await call(null, "POST", "/auth/invitations/accept", { token: invitation.token, display_name: "Ada Mensah", password: PASSWORD });
const adaToken = await login(adaEmail);

async function uploadCv(path, title) {
  const form = new FormData();
  form.append("file", new Blob([await readFile(path)], { type: "application/pdf" }), `${title}.pdf`);
  form.append("title", title);
  const result = await call(owner, "POST", "/documents", form);
  for (let i = 0; i < 60; i++) {
    const status = await call(owner, "GET", `/documents/${result.document.id}/status`);
    if (status.status === "ready") return result.document.id;
    if (status.status === "failed") throw new Error(status.failure_reason);
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("ingestion timed out");
}
const ashaDoc = await uploadCv(STRONG, "Asha Rao CV");
const benDoc = await uploadCv(WEAK, "Ben Okafor CV");

const requisition = await call(owner, "POST", "/requisitions", { title: "Senior Payments Engineer", description: "Owns settlement and reconciliation." });
const rubric = await call(owner, "POST", `/requisitions/${requisition.id}/rubrics`, { criteria: [
  { label: "Python backend services", kind: "must_have", weight: 1, evidence_hint: "services built in Python", disqualifying: true },
  { label: "Payment processing", kind: "strong_preference", weight: 0.7, evidence_hint: "settlement, reconciliation" },
  { label: "Kubernetes in production", kind: "nice_to_have", weight: 0.3 },
] });
await call(owner, "POST", `/rubrics/${rubric.id}/approve`);
const asha = await call(owner, "POST", `/requisitions/${requisition.id}/candidates`, { name: "Asha Rao", email: "asha@mail.example.com", document_ids: [ashaDoc] });
const ben = await call(owner, "POST", `/requisitions/${requisition.id}/candidates`, { name: "Ben Okafor", document_ids: [benDoc] });
for (const a of [asha, ben]) await call(owner, "POST", `/applications/${a.application.id}/assessments`);
const app = asha.application.id;
await call(owner, "POST", `/applications/${app}/stage`, { to: "screened" });
await call(owner, "POST", `/applications/${app}/stage`, { to: "interview" });
const me = (await call(owner, "GET", "/members")).find((m) => m.user.email === ownerEmail).user.id;
await call(owner, "POST", `/applications/${app}/panel`, { user_id: ada.user.id });
await call(owner, "POST", `/applications/${app}/panel`, { user_id: me });
const detail = await call(owner, "GET", `/applications/${app}`);
await call(owner, "POST", `/scorecards/${detail.scorecards.own.id}/submit`, {
  ratings: detail.rubric.criteria.map((c) => ({ criterion_id: c.id, rating: 4, note: "" })),
  recommendation: "strong_yes",
  summary: "Walked through a reconciliation outage end to end; owned the fix and the post-mortem.",
});

// -- browse ---------------------------------------------------------------------------
const browser = await chromium.launch();
const problems = [];
async function as(token, email) {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
  await context.addInitScript(([t, e]) => {
    sessionStorage.setItem("insightlm.token", t);
    sessionStorage.setItem("insightlm.email", e);
  }, [token, email]);
  const page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  return page;
}
const shot = async (page, name) => { await page.waitForTimeout(700); await page.screenshot({ path: `${OUT}/${name}.png` }); };

const ownerPage = await as(owner, ownerEmail);
await ownerPage.goto(`${WEB}/hiring/${requisition.id}`, { waitUntil: "networkidle" });
await shot(ownerPage, "p01-owner-shortlist");
await ownerPage.goto(`${WEB}/applications/${app}`, { waitUntil: "networkidle" });
await shot(ownerPage, "p02-owner-application-blocked");

const adaPage = await as(adaToken, adaEmail);
await adaPage.goto(`${WEB}/interviews`, { waitUntil: "networkidle" });
await shot(adaPage, "p03-interviewer-queue");
await adaPage.getByText("Asha Rao").click();
await adaPage.waitForURL("**/applications/*");
await adaPage.waitForLoadState("networkidle");
await shot(adaPage, "p04-interviewer-blind");
for (const label of ["Python backend services", "Payment processing", "Kubernetes in production"]) {
  await adaPage.getByRole("radiogroup", { name: label }).getByRole("radio", { name: "Solid" }).click();
}
await adaPage.getByRole("radio", { name: "Yes", exact: true }).click();
await adaPage.getByLabel("Summary").fill("Strong on reconciliation; less depth on running Kubernetes herself.");
await shot(adaPage, "p05-interviewer-filled");
await adaPage.getByRole("button", { name: "Submit scorecard" }).click();
await adaPage.getByText("Walked through a reconciliation outage").waitFor();
await shot(adaPage, "p06-interviewer-unlocked");

await ownerPage.reload({ waitUntil: "networkidle" });
await shot(ownerPage, "p07-owner-offer-unblocked");
await ownerPage.getByRole("button", { name: "Move to Offer" }).click();
await ownerPage.getByText("Offer for").waitFor();
await shot(ownerPage, "p08-owner-offer");

await ownerPage.goto(`${WEB}/applications/${ben.application.id}`, { waitUntil: "networkidle" });
await ownerPage.getByRole("button", { name: "Reject…" }).click();
await ownerPage.getByRole("button", { name: "Skills gap" }).click();
await ownerPage.getByPlaceholder("A note for the record").fill("No backend services experience.");
await shot(ownerPage, "p09-owner-reject");
await ownerPage.getByRole("button", { name: "Reject", exact: true }).click();
await ownerPage.getByText("Rejected — Skills gap").waitFor();
await shot(ownerPage, "p10-owner-rejected");

await ownerPage.goto(`${WEB}/talent`, { waitUntil: "networkidle" });
await ownerPage.getByRole("button", { name: "Kubernetes in production" }).click();
await ownerPage.waitForTimeout(1500);
await shot(ownerPage, "p11-talent-search");

await browser.close();
console.log(problems.length ? problems.join("\n") : "no console errors");
