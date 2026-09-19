// 50 concurrent users working a requisition: the SLO load test.
//   make load     (seeds first, runs k6 in the compose network, keyless)
// Thresholds are the SLOs; k6 exits non-zero if any is missed.
import http from "k6/http";
import { check, sleep } from "k6";

const API = __ENV.API || "http://api:8000";
const demo = JSON.parse(open("/data/demo.json"));
const USERS = Number(__ENV.USERS || 50);
const HOLD = __ENV.HOLD || "2m";

export const options = {
  scenarios: {
    recruiters: {
      executor: "ramping-vus",
      stages: [
        { duration: "30s", target: USERS },
        { duration: HOLD, target: USERS },
        { duration: "10s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.005"],
    "http_req_duration{kind:read}": ["p(95)<300"],
    "http_req_duration{kind:ask}": ["p(95)<1000"],
    "http_req_duration{kind:search}": ["p(95)<800"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export function setup() {
  const res = http.post(
    `${API}/auth/login`,
    JSON.stringify({ email: demo.emails.recruiter, password: demo.password }),
    { headers: { "Content-Type": "application/json" } },
  );
  check(res, { "signed in": (r) => r.status === 200 });
  return { token: res.json("access_token") };
}

const QUESTIONS = [
  "Who has run Kubernetes in production?",
  "Which candidates have payment reconciliation experience?",
  "Who built backend services in Python?",
];
const SEARCHES = ["payment processing", "Kubernetes", "Python services", "team leadership"];

const pick = (list) => list[Math.floor(Math.random() * list.length)];

export default function (data) {
  const auth = { headers: { Authorization: `Bearer ${data.token}`, "Content-Type": "application/json" } };
  const roll = Math.random();
  let res;
  if (roll < 0.3) {
    res = http.get(`${API}/requisitions/${demo.requisition_id}/shortlist`, { ...auth, tags: { kind: "read", name: "shortlist" } });
  } else if (roll < 0.5) {
    res = http.get(`${API}/applications/${pick(demo.application_ids)}`, { ...auth, tags: { kind: "read", name: "application" } });
  } else if (roll < 0.65) {
    res = http.get(`${API}/documents`, { ...auth, tags: { kind: "read", name: "documents" } });
  } else if (roll < 0.75) {
    res = http.get(`${API}/requisitions`, { ...auth, tags: { kind: "read", name: "requisitions" } });
  } else if (roll < 0.9) {
    res = http.post(`${API}/ask`, JSON.stringify({ question: pick(QUESTIONS) }), { ...auth, tags: { kind: "ask", name: "ask" } });
  } else {
    res = http.get(`${API}/talent/search?q=${encodeURIComponent(pick(SEARCHES))}`, { ...auth, tags: { kind: "search", name: "talent" } });
  }
  check(res, { "2xx": (r) => r.status >= 200 && r.status < 300 });
  sleep(0.5 + Math.random());
}
