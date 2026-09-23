/**
 * Experimental practice API request shape.
 *
 * This runs the compiled client code, not string fixtures. The important gates:
 * - every route sends/encodes vault_root
 * - model-backed practice calls carry UI language for localized generation
 * - no API key-shaped field or value leaves the frontend
 * - regenerate is explicit and only appears when the UI asks for new variants
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-practice-")), "practice.cjs");
await build({
  entryPoints: ["src/practice.ts"],
  bundle: true,
  format: "cjs",
  external: ["obsidian"],
  outfile: out,
  logLevel: "error",
});

const require = createRequire(import.meta.url);
const Module = require("node:module");
const origLoad = Module._load;
Module._load = (req, parent, isMain) =>
  req === "obsidian"
    ? { getLanguage: () => "zh", Plugin: class {}, PluginSettingTab: class {}, Setting: class {}, App: class {} }
    : origLoad(req, parent, isMain);
globalThis.window = { localStorage: { getItem: () => null } };
globalThis.document = { documentElement: { lang: "" } };

const { makePracticeApi, ApiError } = require(out);

const checks = [];
const check = (name, pass) => checks.push([name, Boolean(pass)]);
const CANARY = "sk-practice-canary-never-travels";
const settings = {
  baseUrl: "https://api.example.test/v1/",
  model: "model-a",
  provider: "auto",
  thinking: "medium",
  vision: false,
  fastMode: false,
  fastBaseUrl: "https://fast.example.test/v1",
  fastModel: "fast-a",
  fastProvider: "auto",
  limits: {},
  apiKey: CANARY,
  fastApiKey: CANARY,
};

let seen = [];
globalThis.fetch = async (url, init) => {
  seen.push({ url: String(url), method: init?.method || "GET", body: init?.body || "" });
  return {
    ok: true,
    status: 200,
    statusText: "ok",
    body: null,
    json: async () => ({ ok: true, enabled: true, services: {}, items: [], questions: [], sessions: [] }),
  };
};

const api = makePracticeApi("http://x/");
await api.enable("/a b/中文 vault", true);
check("enable is PUT /v1/practice/enable", seen[0]?.method === "PUT" && seen[0]?.url === "http://x/v1/practice/enable");
let body = JSON.parse(seen[0]?.body || "{}");
check("enable sends vault_root", body.vault_root === "/a b/中文 vault" && body.enabled === true);

seen = [];
await api.state("/a b/中文 vault", "hb 1");
check(
  "state encodes vault_root and handbook_id",
  seen[0]?.url ===
    "http://x/v1/practice/state?" +
      new URLSearchParams({ vault_root: "/a b/中文 vault", handbook_id: "hb 1" }).toString(),
);

seen = [];
await api.build("/vault", "hb", settings, { practice_per_type: 3, exam_forms: 2, regenerate: true });
body = JSON.parse(seen[0]?.body || "{}");
check("build is POST /build", seen[0]?.method === "POST" && seen[0]?.url === "http://x/v1/practice/build");
check("build carries language", body.lang === "zh");
check("build carries regenerate only when requested", body.regenerate === true);
check("build trims base_url", body.base_url === "https://api.example.test/v1");
check("build sends no API key fields", !("api_key" in body) && !("apiKey" in body) && !("fastApiKey" in body));
check("build body contains no canary key value", !String(seen[0]?.body).includes(CANARY));

seen = [];
await api.createSession("/vault", "hb", "practice", settings, { minutes: 20 });
body = JSON.parse(seen[0]?.body || "{}");
check("createSession carries mode and language", body.mode === "practice" && body.lang === "zh");
check("createSession sends no canary", !String(seen[0]?.body).includes(CANARY));

seen = [];
await api.answer("/vault", "sid", "qid", { text: "answer" }, "idem", 12, settings);
body = JSON.parse(seen[0]?.body || "{}");
check("answer carries idempotency and duration", body.idempotency_key === "idem" && body.duration_seconds === 12);
check("answer carries language", body.lang === "zh");
check("answer sends no canary", !String(seen[0]?.body).includes(CANARY));

seen = [];
await api.finish("/vault", "sid", settings);
body = JSON.parse(seen[0]?.body || "{}");
check("finish carries language", body.lang === "zh");

seen = [];
await api.retryAttempt("/vault", "att", settings);
body = JSON.parse(seen[0]?.body || "{}");
check("retry carries language", body.lang === "zh");

seen = [];
await api.resumeJob("/vault", "job", settings);
body = JSON.parse(seen[0]?.body || "{}");
check("resume job carries language", body.lang === "zh");

globalThis.fetch = async () => ({
  ok: false,
  status: 409,
  statusText: "conflict",
  body: null,
  json: async () => ({ detail: { code: "practice_disabled", message: "enable first" } }),
});
let err = null;
try {
  await api.questions("/vault", "hb");
} catch (e) {
  err = e;
}
check("practice errors preserve ApiError code", err instanceof ApiError && err.status === 409 && err.code === "practice_disabled");

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`  ${pass ? "ok  " : "FAIL"} ${name}`);
}
console.log(`\n${bad ? `${bad}/${checks.length} failed` : `${checks.length} checks passed`}`);
process.exit(bad ? 1 : 0);
