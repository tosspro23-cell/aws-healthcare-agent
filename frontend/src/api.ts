import { config } from "./config";
import { getAccessToken } from "./auth";

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

export async function askQuestion(userId: string, question: string): Promise<AskResponse> {
  const token = getAccessToken();
  if (!token) throw new Error("Not signed in.");

  const response = await fetch(`${config.apiBaseUrl}/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ user_id: userId, question }),
  });

  const body = await response.json();
  if (!response.ok) {
    throw new ApiError(response.status, body.error ?? `Request failed with status ${response.status}`);
  }
  return body as AskResponse;
}
