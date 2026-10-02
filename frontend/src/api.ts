import { config } from "./config";
import { getAccessToken, handleSessionExpired } from "./auth";

export interface SafetyCheck {
  name: string;
  passed: boolean;
  detail: string;
  /** "hard" = unambiguous policy violation; "soft" = numeric_grounding,
   * the one check with a documented history of false positives. Doesn't
   * change what was returned -- see AgentTrace.disposition for that. */
  severity: "hard" | "soft";
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
  // V2 (compound-reasoning) only -- wall-clock time this one step took, in
  // milliseconds. V1's fixed-pipeline trace entries leave this undefined.
  duration_ms?: number;
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
  /** "answered": no fallback. "answered_after_hard_fallback": a policy
   * violation (diagnosis/dosing/empty) was caught and replaced.
   * "answered_after_soft_fallback": only numeric_grounding failed --
   * worth reviewing whether the check itself needs another fix. */
  disposition: "answered" | "answered_after_hard_fallback" | "answered_after_soft_fallback";
  // Ground-truth wall-clock time for the whole ask()/ask_compound() call,
  // in milliseconds -- independent of summing individual ToolCall
  // duration_ms entries. Undefined for any trace predating this field.
  total_duration_ms?: number;
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
  /** V2 (`engine: "v2"`) only -- a short human-readable checkpoint
   * ("Planning which tools to call...", "Calling tools: ...") written by
   * `agent_task.py`/`process_job.py` while the run is still `RUNNING`, via
   * the same `on_stage` hook `dev_server.py`'s local SSE demo uses.
   * Present only while a V2 async run is in flight; a terminal record may
   * still carry its last value, which the UI simply stops rendering once
   * `status` is terminal. */
  current_stage?: string;
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

export type Engine = "v1" | "v2";
export type Persona = "patient" | "clinician";

/** `engine`/`persona` are optional -- omitting either reproduces the
 * exact request shape from before they existed. `engine: "v2"` hits the
 * *real*, deployed `ask_compound` path (see `adapter.py`), not the local
 * dev server `CompoundDemo.tsx` talks to -- this is the production
 * verification path for V2. */
export async function askQuestion(
  userId: string,
  question: string,
  engine?: Engine,
  persona?: Persona,
  priorRunIds?: string[],
): Promise<AskResponse> {
  return (await authedFetch("/ask", {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      question,
      ...(engine ? { engine } : {}),
      ...(persona ? { persona } : {}),
      ...(priorRunIds && priorRunIds.length > 0 ? { prior_run_ids: priorRunIds } : {}),
    }),
  })) as AskResponse;
}

/** Starts the Step Functions-orchestrated async path. Returns immediately
 * with `status: "RUNNING"` (or the real current status, if `runId` was
 * already submitted before) -- poll with `getRun` to see it finish.
 * `persona`/`engine` are both optional (default server-side to "patient"/
 * "v1"). `engine: "v2"` now runs through this path too -- see
 * docs/DECISIONS.md, 2026-09-21 entry -- and its progress is visible via
 * `RunRecord.current_stage` while polling. */
export async function startRun(
  userId: string,
  question: string,
  runId?: string,
  persona?: Persona,
  engine?: Engine,
  priorRunIds?: string[],
): Promise<{ run_id: string; status: string }> {
  return (await authedFetch("/runs", {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      question,
      ...(runId ? { run_id: runId } : {}),
      ...(persona ? { persona } : {}),
      ...(engine ? { engine } : {}),
      ...(priorRunIds && priorRunIds.length > 0 ? { prior_run_ids: priorRunIds } : {}),
    }),
  })) as { run_id: string; status: string };
}

/** Starts the SQS-buffered async path (the direct comparison to `startRun`
 * above -- see docs/STRESS_TEST.md for the load-tested trade-off between
 * the two: this one trades latency under load for higher measured success
 * capacity). Returns immediately with `status: "QUEUED"`. See `startRun`'s
 * docstring for the same `persona`/`engine` notes. */
export async function enqueueJob(
  userId: string,
  question: string,
  runId?: string,
  persona?: Persona,
  engine?: Engine,
  priorRunIds?: string[],
): Promise<{ run_id: string; status: string }> {
  return (await authedFetch("/jobs", {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      question,
      ...(runId ? { run_id: runId } : {}),
      ...(persona ? { persona } : {}),
      ...(engine ? { engine } : {}),
      ...(priorRunIds && priorRunIds.length > 0 ? { prior_run_ids: priorRunIds } : {}),
    }),
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

// -- Patient data (the Workbench's "Patient Data" rail tab) ---------------
// Field names mirror `src/care_agent/models.py`'s dataclasses exactly --
// the wire format is each dataclass's own `.as_dict()` output, serialized
// as-is by `infra/lambda_src/patient_data.py`, not a separate DTO shape.

export interface Biomarker {
  concept_id: string;
  display_name: string;
  value: number;
  unit: string;
  classification: string | null;
  classification_basis: string | null;
  action_fields: string[];
  source: string | null;
}

export interface Panel {
  panel_id: string;
  measurement_date: string;
  biomarkers: Biomarker[];
  overall_flags: string[];
}

export interface Bloodwork {
  user_id: string;
  latest_panel: Panel | null;
  previous_panels: Panel[];
}

export interface Medication {
  name: string;
  source: string;
  confidence: string;
}

export interface Allergy {
  name: string;
  source: string;
  confidence: string;
}

export interface UserProfile {
  user_id: string;
  display_name: string;
  age: number | null;
  sex: string | null;
  country: string | null;
  height_cm: number | null;
  weight_kg: number | null;
  known_conditions: string[];
  medications: Medication[];
  allergies: Allergy[];
}

export interface QuestionnaireFact {
  field: string;
  value: string;
  state: string | null;
  source: string | null;
}

export interface QuestionnaireCaution {
  kind: string;
  detail: string;
  source: string | null;
}

export interface QuestionnaireContext {
  user_id: string;
  completed_at: string | null;
  facts: QuestionnaireFact[];
  cautions: QuestionnaireCaution[];
  style_hint: string | null;
}

export interface PatientData {
  user_id: string;
  profile: UserProfile;
  bloodwork: Bloodwork;
  questionnaire: QuestionnaireContext;
}

export async function getPatientData(userId: string): Promise<PatientData> {
  return (await authedFetch(`/patient-data/${encodeURIComponent(userId)}`)) as PatientData;
}
