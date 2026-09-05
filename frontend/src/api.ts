import { config } from "./config";
import { getAccessToken, handleSessionExpired } from "./auth";

export interface SafetyCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface GroundedFact {
  claim: string;
  source_type: string;
  source_ref: string;
  numeric_values: number[];
  unit: string | null;
}

export interface Limitation {
  kind: string;
  detail: string;
}

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  result_summary: string;
  ok: boolean;
}

export interface RetrievedChunk {
  chunk: { id: string; title: string; source_name: string; source_url: string };
  score: number;
  matched_terms: string[];
}

export interface AgentTrace {
  question_id: string | null;
  user_id: string;
  intent: string;
  tool_calls: ToolCall[];
  retrieved_chunks: RetrievedChunk[];
  grounded_facts: GroundedFact[];
  limitations: Limitation[];
  safety_checks: SafetyCheck[];
  rejected_draft: string | null;
  narrator_backend: string;
}

export interface AskResponse {
  run_id: string;
  answer: string;
  safe: boolean;
  trace: AgentTrace;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** `RunRecord` is the DynamoDB item `GET /runs/{run_id}` returns (see
 * `infra/lambda_src/get_run.py`), plus an opportunistic `trace` merged in
 * from S3 if one's been written for this run_id yet -- all three
 * execution paths (`adapter.py`, `agent_task.py`, `process_job.py`) now
 * persist one to the same `{run_id}.json` key once their run completes.
 * A run still in progress, or one that predates this evidence write,
 * simply has no `trace` field. */
export interface RunRecord {
  run_id: string;
  trace?: AgentTrace;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "TIMED_OUT" | "CANCELLED";
  execution_type: "SYNC" | "STEP_FUNCTIONS" | "SQS";
  user_id: string;
  question: string;
  started_at?: string;
  queued_at?: string;
  completed_at?: string;
  answer?: string;
  safe?: boolean;
  narrator_backend?: string;
  error_message?: string;
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<unknown> {
  const token = getAccessToken();
  if (!token) throw new Error("Not signed in.");

  const response = await fetch(`${config.apiBaseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  });

  const body = response.status === 204 ? {} : await response.json();
  if (!response.ok) {
    // A second independent review found that a 401 (the token expired or
    // was revoked server-side, distinct from `getAccessToken`'s own
    // expiry check, which only catches the token's *recorded* lifetime)
    // was treated as an ordinary error and left the app displaying its
    // signed-in state indefinitely, with every subsequent request
    // failing the same way. Force back to a real signed-out state
    // instead of leaving that stuck.
    if (response.status === 401) handleSessionExpired();
    throw new ApiError(response.status, body.error ?? `Request failed with status ${response.status}`);
  }
  return body;
}

export async function askQuestion(userId: string, question: string): Promise<AskResponse> {
  return (await authedFetch("/ask", { method: "POST", body: JSON.stringify({ user_id: userId, question }) })) as AskResponse;
}

/** Starts the Step Functions-orchestrated async path. Returns immediately
 * with `status: "RUNNING"` (or the real current status, if `runId` was
 * already submitted before) -- poll with `getRun` to see it finish. */
export async function startRun(userId: string, question: string, runId?: string): Promise<{ run_id: string; status: string }> {
  return (await authedFetch("/runs", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, question, ...(runId ? { run_id: runId } : {}) }),
  })) as { run_id: string; status: string };
}

/** Starts the SQS-buffered async path (the direct comparison to `startRun`
 * above -- see docs/STRESS_TEST.md for the load-tested trade-off between
 * the two: this one trades latency under load for higher measured success
 * capacity). Returns immediately with `status: "QUEUED"`. */
export async function enqueueJob(userId: string, question: string, runId?: string): Promise<{ run_id: string; status: string }> {
  return (await authedFetch("/jobs", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, question, ...(runId ? { run_id: runId } : {}) }),
  })) as { run_id: string; status: string };
}

export async function getRun(runId: string): Promise<RunRecord> {
  return (await authedFetch(`/runs/${encodeURIComponent(runId)}`)) as RunRecord;
}

/** Only meaningful for a Step-Functions-orchestrated run still `RUNNING`
 * or `QUEUED` -- see `infra/lambda_src/cancel_run.py`: a synchronous
 * `/ask` run can't be cancelled at all (there's no execution to stop, and
 * the caller is already blocked waiting for the response), and an
 * already-finished run returns 409 with its real terminal status. */
export async function cancelRun(runId: string): Promise<{ run_id: string; status: string; message?: string }> {
  return (await authedFetch(`/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" })) as {
    run_id: string;
    status: string;
    message?: string;
  };
}
