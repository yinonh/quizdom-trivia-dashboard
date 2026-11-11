// Dispatches one hardcoded GitHub Actions workflow.
// The GitHub token is read by index.ts from the DASHBOARD_GH_TOKEN secret.
// This module never accepts a repo, workflow, ref, or inputs from the caller.

export const ALLOWED_ORIGIN = "https://yinonh.github.io";
export const GITHUB_OWNER = "yinonh";
export const GITHUB_REPO = "quizdom-trivia-dashboard";
export const GITHUB_WORKFLOW = "refresh-dashboard.yml";
export const GITHUB_REF = "main";
export const COOLDOWN_MS = 3 * 60 * 1000;
export const GITHUB_API_VERSION = "2022-11-28";

const GITHUB_API = "https://api.github.com";
const MAX_BODY_BYTES = 2048;
const ACTIVE_STATUSES = new Set([
  "queued",
  "in_progress",
  "waiting",
  "requested",
  "pending",
  "action_required",
]);

const COOLDOWN_MESSAGE =
  "A refresh is already running or was started in the last few minutes. Please wait and try again.";
const FAILURE_MESSAGE = "Could not start a refresh. Try again in a few minutes.";
const STARTED_MESSAGE = "Refresh started. The page will update in about 1–2 minutes.";

// Fine-grained PATs (github_pat_…) and classic PATs (ghp_…) fit this shape.
// Reject anything else so a malformed secret cannot inject header fields.
const TOKEN_PATTERN = /^[A-Za-z0-9._\-]{20,300}$/;

export interface WorkflowRun {
  status: string | null;
  created_at: string | null;
}

export interface CooldownDecision {
  limited: boolean;
  retryAfterSeconds: number;
}

export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export interface HandlerDeps {
  token: string;
  now?: () => number;
  fetch?: FetchLike;
}

class GithubError extends Error {
  reason: "list_failed" | "dispatch_failed" | "redirect";
  status: number;

  constructor(reason: "list_failed" | "dispatch_failed" | "redirect", status: number) {
    super(reason);
    this.name = "GithubError";
    this.reason = reason;
    this.status = status;
  }
}

export function assessRuns(runs: WorkflowRun[], nowMs: number): CooldownDecision {
  let limited = false;
  let retryAfterSeconds = 0;
  for (const run of runs) {
    const status = (run.status ?? "").trim().toLowerCase();
    const createdMs = Date.parse(run.created_at ?? "");
    const active = ACTIVE_STATUSES.has(status);
    const recent = Number.isFinite(createdMs) && nowMs - createdMs < COOLDOWN_MS;
    if (!active && !recent) continue;
    limited = true;
    const elapsed = Number.isFinite(createdMs) ? nowMs - createdMs : 0;
    const waitMs = recent ? COOLDOWN_MS - elapsed : COOLDOWN_MS;
    retryAfterSeconds = Math.max(retryAfterSeconds, Math.max(1, Math.ceil(waitMs / 1000)));
  }
  return { limited, retryAfterSeconds };
}

function corsHeaders(): Headers {
  const headers = new Headers();
  headers.set("Access-Control-Allow-Origin", ALLOWED_ORIGIN);
  headers.set("Access-Control-Allow-Methods", "POST, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "content-type");
  headers.set("Access-Control-Max-Age", "86400");
  headers.set("Vary", "Origin");
  return headers;
}

function jsonResponse(
  origin: string | null,
  status: number,
  body: { ok: boolean; message: string },
  extra?: HeadersInit,
): Response {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  if (origin === ALLOWED_ORIGIN) {
    corsHeaders().forEach((value, key) => headers.set(key, value));
  }
  if (extra) {
    new Headers(extra).forEach((value, key) => headers.set(key, value));
  }
  return new Response(JSON.stringify(body), { status, headers });
}

function workflowRunsUrl(): string {
  return `${GITHUB_API}/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${GITHUB_WORKFLOW}/runs?per_page=10&exclude_pull_requests=true`;
}

function dispatchUrl(): string {
  return `${GITHUB_API}/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${GITHUB_WORKFLOW}/dispatches`;
}

function githubHeaders(token: string): Headers {
  const headers = new Headers();
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("Accept", "application/vnd.github+json");
  headers.set("X-GitHub-Api-Version", GITHUB_API_VERSION);
  headers.set("User-Agent", "quizdom-trivia-dashboard-refresh");
  return headers;
}

async function githubFetch(fetchImpl: FetchLike, token: string, url: string, init: RequestInit): Promise<Response> {
  const headers = githubHeaders(token);
  new Headers(init.headers).forEach((value, key) => headers.set(key, value));
  const response = await fetchImpl(url, {
    ...init,
    headers,
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (response.status >= 300 && response.status < 400) {
    await response.body?.cancel();
    throw new GithubError("redirect", response.status);
  }
  return response;
}

function asRuns(payload: unknown): WorkflowRun[] {
  if (!payload || typeof payload !== "object" || !("workflow_runs" in payload)) {
    throw new GithubError("list_failed", 200);
  }
  const runs = (payload as { workflow_runs?: unknown }).workflow_runs;
  if (!Array.isArray(runs)) throw new GithubError("list_failed", 200);
  return runs.slice(0, 10).map((run) => {
    const record = run && typeof run === "object" ? run as Record<string, unknown> : {};
    return {
      status: typeof record.status === "string" ? record.status : null,
      created_at: typeof record.created_at === "string" ? record.created_at : null,
    };
  });
}

async function listRecentRuns(fetchImpl: FetchLike, token: string): Promise<WorkflowRun[]> {
  const response = await githubFetch(fetchImpl, token, workflowRunsUrl(), { method: "GET" });
  if (!response.ok) {
    const status = response.status;
    await response.body?.cancel();
    throw new GithubError("list_failed", status);
  }
  try {
    return asRuns(await response.json());
  } catch (err) {
    if (err instanceof GithubError) throw err;
    throw new GithubError("list_failed", response.status);
  }
}

async function dispatchWorkflow(fetchImpl: FetchLike, token: string): Promise<void> {
  const response = await githubFetch(fetchImpl, token, dispatchUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: GITHUB_REF }),
  });
  const status = response.status;
  await response.body?.cancel();
  if (status < 200 || status >= 300) {
    throw new GithubError("dispatch_failed", status);
  }
}

export async function handleRefresh(req: Request, deps: HandlerDeps): Promise<Response> {
  const origin = req.headers.get("origin");

  if (req.method === "OPTIONS") {
    if (origin !== ALLOWED_ORIGIN) {
      return new Response(null, { status: 403, headers: { "Cache-Control": "no-store" } });
    }
    const headers = corsHeaders();
    headers.set("Cache-Control", "no-store");
    return new Response(null, { status: 204, headers });
  }

  if (req.method !== "POST") {
    return jsonResponse(origin, 405, { ok: false, message: "Only POST is accepted." }, {
      Allow: "POST, OPTIONS",
    });
  }

  if (origin !== ALLOWED_ORIGIN) {
    return jsonResponse(null, 403, {
      ok: false,
      message: "Refresh is only available from the dashboard.",
    });
  }

  const declaredLength = Number(req.headers.get("content-length") ?? "0");
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    await req.body?.cancel();
    return jsonResponse(origin, 413, { ok: false, message: "Request is too large." });
  }
  // Repo, workflow, ref, and inputs are fixed above. The body is ignored on purpose.
  await req.body?.cancel();

  const token = deps.token.trim();
  if (!TOKEN_PATTERN.test(token)) {
    console.error("refresh-dashboard missing_token");
    return jsonResponse(origin, 500, { ok: false, message: FAILURE_MESSAGE });
  }

  const fetchImpl = deps.fetch ?? fetch;
  const now = deps.now ?? Date.now;

  try {
    const runs = await listRecentRuns(fetchImpl, token);
    const decision = assessRuns(runs, now());
    if (decision.limited) {
      return jsonResponse(origin, 429, { ok: false, message: COOLDOWN_MESSAGE }, {
        "Retry-After": String(decision.retryAfterSeconds),
      });
    }
    await dispatchWorkflow(fetchImpl, token);
    return jsonResponse(origin, 200, { ok: true, message: STARTED_MESSAGE });
  } catch (err) {
    const reason = err instanceof GithubError ? `${err.reason}:${err.status}` : "exception";
    console.error("refresh-dashboard", reason);
    return jsonResponse(origin, 502, { ok: false, message: FAILURE_MESSAGE });
  }
}
