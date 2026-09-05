/**
 * Client-side run history: a per-viewer, per-browser record of run_ids
 * this session has submitted, so past runs can be revisited via
 * `GET /runs/{run_id}` after a page reload -- a lightweight way to
 * demonstrate that the backend genuinely persists runs (DynamoDB, not
 * just an in-memory response) without building a new "list my runs"
 * backend endpoint (the table's only key is `run_id`; querying by owner
 * would need a new GSI + Lambda + route, a larger, separate piece of
 * infra work than wiring up the already-existing endpoints).
 *
 * This is convenience, not a source of truth -- a different browser or a
 * cleared site data will show an empty history even though the runs
 * themselves are still sitting in DynamoDB, retrievable directly by
 * run_id if you know it.
 *
 * Cleared on sign-out (see `auth.ts`'s `signOut()`), not just the access
 * token: an independent review found that signing out only cleared
 * `sessionStorage` (the token, the PKCE verifier), leaving this
 * `localStorage` history -- including full question text -- readable by
 * whoever signs into the same browser next. The backend's ownership
 * check still stops a second account from *fetching* the first
 * account's run result, but the question text itself was already
 * exposed here without any backend call at all.
 */

const STORAGE_KEY = "care_agent_run_history";
const MAX_ENTRIES = 50;

export interface HistoryEntry {
  run_id: string;
  question: string;
  execution_type: "SYNC" | "STEP_FUNCTIONS" | "SQS";
  submitted_at: string;
}

export function loadHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as HistoryEntry[]) : [];
  } catch {
    return [];
  }
}

export function addHistoryEntry(entry: HistoryEntry): HistoryEntry[] {
  const next = [entry, ...loadHistory().filter((e) => e.run_id !== entry.run_id)].slice(0, MAX_ENTRIES);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // best-effort only (e.g. private browsing may block storage) -- the
    // run itself is still safely in DynamoDB regardless
  }
  return next;
}

export function clearHistory(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // best-effort, same as addHistoryEntry above
  }
}
