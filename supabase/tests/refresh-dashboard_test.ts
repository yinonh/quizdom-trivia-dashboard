// node --experimental-strip-types --test supabase/tests/refresh-dashboard_test.ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  ALLOWED_ORIGIN,
  COOLDOWN_MS,
  GITHUB_REF,
  assessRuns,
  handleRefresh,
  type FetchLike,
  type WorkflowRun,
} from "../functions/refresh-dashboard/handler.ts";

const TOKEN = "localtesttokenvalue1234567890";
const ENDPOINT = "https://uhfsfedwteeoxsvixvtr.supabase.co/functions/v1/refresh-dashboard";
const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../..");
const FUNCTION_DIR = resolve(HERE, "../functions/refresh-dashboard");

function runsResponse(runs: WorkflowRun[], status = 200): Response {
  return new Response(JSON.stringify({ workflow_runs: runs }), { status });
}

function recordedFetch(handlers: Array<(url: string, init: RequestInit) => Response>) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({ url, init });
    const next = handlers.shift();
    if (!next) throw new Error(`unexpected fetch ${url}`);
    return next(url, init);
  };
  return { fetchImpl, calls };
}

function request(method: string, origin: string | null, body?: string, extra?: HeadersInit): Request {
  const headers = new Headers(extra);
  if (origin) headers.set("origin", origin);
  if (body !== undefined && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  return new Request(ENDPOINT, { method, headers, body });
}

async function textOf(response: Response): Promise<{ status: number; body: string; headers: Headers }> {
  return { status: response.status, body: await response.text(), headers: response.headers };
}

test("cooldown blocks an active run and a run created inside 3 minutes", () => {
  const now = Date.parse("2026-11-11T12:00:00Z");
  const active = assessRuns(
    [{ status: "in_progress", created_at: "2026-11-11T10:00:00Z" }],
    now,
  );
  assert.equal(active.limited, true);
  assert.equal(active.retryAfterSeconds, COOLDOWN_MS / 1000);

  const recent = assessRuns(
    [{ status: "completed", created_at: "2026-11-11T11:58:30Z" }],
    now,
  );
  assert.equal(recent.limited, true);
  assert.ok(recent.retryAfterSeconds > 60 && recent.retryAfterSeconds <= 180);

  const clear = assessRuns(
    [{ status: "completed", created_at: "2026-11-11T11:50:00Z" }],
    now,
  );
  assert.equal(clear.limited, false);

  for (const status of ["queued", "waiting", "pending", "requested", "action_required"]) {
    assert.equal(assessRuns([{ status, created_at: "2026-11-11T08:00:00Z" }], now).limited, true);
  }
});

test("preflight allows only the dashboard origin", async () => {
  const allowed = await handleRefresh(request("OPTIONS", ALLOWED_ORIGIN), { token: TOKEN });
  assert.equal(allowed.status, 204);
  assert.equal(allowed.headers.get("access-control-allow-origin"), ALLOWED_ORIGIN);
  assert.equal(allowed.headers.get("access-control-allow-methods"), "POST, OPTIONS");
  assert.equal(allowed.headers.get("access-control-allow-headers"), "content-type");
  assert.equal(await allowed.text(), "");

  const blocked = await handleRefresh(request("OPTIONS", "https://evil.example"), { token: TOKEN });
  assert.equal(blocked.status, 403);
  assert.equal(blocked.headers.get("access-control-allow-origin"), null);
});

test("rejects non-POST methods and other origins", async () => {
  const get = await textOf(await handleRefresh(request("GET", ALLOWED_ORIGIN), { token: TOKEN }));
  assert.equal(get.status, 405);
  assert.equal(get.headers.get("allow"), "POST, OPTIONS");
  assert.match(get.body, /Only POST is accepted/);
  assert.equal(get.headers.get("access-control-allow-origin"), ALLOWED_ORIGIN);

  const foreign = await textOf(
    await handleRefresh(request("POST", "https://evil.example", "{}"), { token: TOKEN }),
  );
  assert.equal(foreign.status, 403);
  assert.equal(foreign.headers.get("access-control-allow-origin"), null);
  assert.equal(JSON.parse(foreign.body).ok, false);

  const missingOrigin = await handleRefresh(request("POST", null, "{}"), { token: TOKEN });
  assert.equal(missingOrigin.status, 403);
});

test("does not dispatch while cooling down and does not call GitHub with the caller body", async () => {
  const { fetchImpl, calls } = recordedFetch([
    () => runsResponse([{ status: "queued", created_at: "2026-11-11T10:00:00Z" }]),
  ]);
  const response = await textOf(await handleRefresh(
    request("POST", ALLOWED_ORIGIN, JSON.stringify({
      repo: "someone/else",
      workflow: "deploy.yml",
      ref: "evil",
      inputs: { command: "rm -rf" },
    })),
    { token: TOKEN, now: () => Date.parse("2026-11-11T12:00:00Z"), fetch: fetchImpl },
  ));
  assert.equal(response.status, 429);
  const payload = JSON.parse(response.body);
  assert.deepEqual(Object.keys(payload).sort(), ["message", "ok"]);
  assert.equal(payload.ok, false);
  assert.match(payload.message, /already running or was started/);
  assert.equal(response.headers.get("retry-after"), "180");
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/repos\/yinonh\/quizdom-trivia-dashboard\/actions\/workflows\/refresh-dashboard\.yml\/runs/);
  assert.equal(calls[0].init.method, "GET");
});

test("dispatches only the fixed workflow on main when the last run is old", async () => {
  const { fetchImpl, calls } = recordedFetch([
    () => runsResponse([
      { status: "completed", created_at: "2026-11-11T11:00:00Z" },
      { status: "completed", created_at: "2026-11-10T11:00:00Z" },
    ]),
    () => new Response(null, { status: 204 }),
  ]);
  const response = await textOf(await handleRefresh(
    request("POST", ALLOWED_ORIGIN, JSON.stringify({ ref: "not-main", inputs: { leak: true } })),
    { token: TOKEN, now: () => Date.parse("2026-11-11T12:00:00Z"), fetch: fetchImpl },
  ));
  assert.equal(response.status, 200);
  assert.deepEqual(JSON.parse(response.body), {
    ok: true,
    message: "Refresh started. The page will update in about 1–2 minutes.",
  });
  assert.equal(response.headers.get("access-control-allow-origin"), ALLOWED_ORIGIN);
  assert.equal(calls.length, 2);
  assert.match(calls[1].url, /\/dispatches$/);
  assert.equal(calls[1].init.method, "POST");
  assert.equal(calls[1].init.body, JSON.stringify({ ref: GITHUB_REF }));
  assert.equal(calls[1].init.redirect, "manual");
  const headers = new Headers(calls[1].init.headers);
  assert.equal(headers.get("authorization"), `Bearer ${TOKEN}`);
  assert.equal(headers.get("accept"), "application/vnd.github+json");
  assert.equal(headers.get("x-github-api-version"), "2022-11-28");
  assert.equal(response.body.includes(TOKEN), false);
});

test("accepts a 200 dispatch response and never returns upstream error bodies", async () => {
  const secretBody = `token=${TOKEN} ghp_SUPERSECRET upstream-debug`;
  const { fetchImpl, calls } = recordedFetch([
    () => runsResponse([]),
    () => new Response(secretBody, { status: 500, headers: { "x-github-sso": "secret-header" } }),
  ]);
  const response = await textOf(await handleRefresh(
    request("POST", ALLOWED_ORIGIN, "{}"),
    { token: TOKEN, now: () => Date.now(), fetch: fetchImpl },
  ));
  assert.equal(response.status, 502);
  const payload = JSON.parse(response.body);
  assert.equal(payload.ok, false);
  assert.equal(payload.message, "Could not start a refresh. Try again in a few minutes.");
  assert.equal(response.body.includes(TOKEN), false);
  assert.equal(response.body.includes("ghp_SUPERSECRET"), false);
  assert.equal(response.body.includes("upstream-debug"), false);
  assert.equal(response.body.includes("secret-header"), false);
  assert.equal(calls.length, 2);

  const okFetch = recordedFetch([
    () => runsResponse([{ status: "completed", created_at: "2020-01-01T00:00:00Z" }]),
    () => new Response(JSON.stringify({ workflow_run_id: 1, html_url: "https://github.com/hidden" }), { status: 200 }),
  ]);
  const started = await textOf(await handleRefresh(
    request("POST", ALLOWED_ORIGIN, "{}"),
    { token: TOKEN, now: () => Date.parse("2026-11-11T12:00:00Z"), fetch: okFetch.fetchImpl },
  ));
  assert.equal(started.status, 200);
  assert.equal(started.body.includes("html_url"), false);
  assert.equal(started.body.includes("github.com"), false);
});

test("fails closed when the run list cannot be read", async () => {
  const { fetchImpl, calls } = recordedFetch([
    () => new Response("nope", { status: 401 }),
  ]);
  const response = await textOf(await handleRefresh(
    request("POST", ALLOWED_ORIGIN, "{}"),
    { token: TOKEN, fetch: fetchImpl },
  ));
  assert.equal(response.status, 502);
  assert.equal(calls.length, 1);
  assert.equal(response.body.includes("nope"), false);

  const redirect = recordedFetch([
    () => new Response(null, { status: 302, headers: { location: `https://evil.example/?token=${TOKEN}` } }),
  ]);
  const redirected = await textOf(await handleRefresh(
    request("POST", ALLOWED_ORIGIN, "{}"),
    { token: TOKEN, fetch: redirect.fetchImpl },
  ));
  assert.equal(redirected.status, 502);
  assert.equal(redirected.body.includes(TOKEN), false);
  assert.equal(redirected.body.includes("evil.example"), false);
  assert.equal(redirect.calls.length, 1);
});

test("refuses a missing token and an oversized body without calling GitHub", async () => {
  let called = false;
  const fetchImpl: FetchLike = async () => {
    called = true;
    return new Response("no");
  };
  const missing = await textOf(await handleRefresh(
    request("POST", ALLOWED_ORIGIN, "{}"),
    { token: "   ", fetch: fetchImpl },
  ));
  assert.equal(missing.status, 500);
  assert.equal(JSON.parse(missing.body).ok, false);
  assert.equal(called, false);

  const huge = await textOf(await handleRefresh(
    request("POST", ALLOWED_ORIGIN, "{}", { "content-length": "99999" }),
    { token: TOKEN, fetch: fetchImpl },
  ));
  assert.equal(huge.status, 413);
  assert.equal(called, false);
});

test("the page and the generator call this function and dropped the old route", () => {
  const html = readFileSync(resolve(REPO_ROOT, "index.html"), "utf8");
  const generator = readFileSync(resolve(REPO_ROOT, "update_trivia_dashboard.py"), "utf8");
  for (const source of [html, generator]) {
    assert.equal(source.includes(ENDPOINT), true);
    assert.equal(source.includes("'/refresh'"), false);
    assert.equal(source.includes('"/refresh"'), false);
    assert.equal(source.includes("Private Tailscale"), false);
    assert.equal(source.includes("Pulling live Supabase counts"), false);
    assert.equal(source.includes("GitHub Pages"), true);
    assert.equal(source.includes("Refresh started. The page will update in about 1–2 minutes."), true);
  }
  const config = readFileSync(resolve(REPO_ROOT, "supabase/config.toml"), "utf8");
  assert.match(config, /\[functions\.refresh-dashboard\]/);
  assert.match(config, /verify_jwt = false/);
  const entry = readFileSync(resolve(FUNCTION_DIR, "index.ts"), "utf8");
  assert.match(entry, /DASHBOARD_GH_TOKEN/);
  assert.equal(entry.includes("github_pat_"), false);
  assert.equal(entry.includes("ghp_"), false);
});
