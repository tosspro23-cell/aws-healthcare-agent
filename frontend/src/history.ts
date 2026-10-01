/**
 * Client-side conversation history: a per-viewer, per-browser record of
 * this session's conversations (each a sequence of run_ids), so a past
 * conversation can be revisited -- its turns re-fetched via
 * `GET /runs/{run_id}` -- after a page reload. A lightweight way to
 * demonstrate that the backend genuinely persists runs (DynamoDB, not
 * just an in-memory response) without building a new "list my runs"
 * backend endpoint (the table's only key is `run_id`; querying by owner
 * would need a new GSI + Lambda + route, a larger, separate piece of
 * infra work than wiring up the already-existing endpoints).
 *
 * Grouped by conversation (not a flat list of individual runs) so the
 * rail can offer the same "click a past thread, see the whole thing
 * again" pattern as ChatGPT/Claude Code's own history sidebars --
 * `Workbench.tsx` now sends each follow-up with its own conversation's
 * prior turns folded into the question text (see its own `buildContextualQuestion`),
 * so a conversation here is also a real unit of shared context, not just
 * a cosmetic grouping.
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

import type { Engine, Persona } from "./api";

const STORAGE_KEY = "care_agent_conversations";
const MAX_CONVERSATIONS = 30;
const MAX_ENTRIES_PER_CONVERSATION = 50;
const TITLE_MAX_CHARS = 60;

export interface ConversationEntry {
  run_id: string;
  question: string;
  execution_type: "SYNC" | "STEP_FUNCTIONS" | "SQS";
  submitted_at: string;
  /** Recorded at submission time, when the client still has them --
   * `GET /runs/{run_id}` can't reliably supply either back (engine is
   * only stored in the DynamoDB record for the sync/queue paths, never
   * for Step Functions; persona is never stored anywhere server-side).
   * Optional so entries written before this field existed still load. */
  engine?: Engine;
  persona?: Persona;
}

export interface Conversation {
  id: string;
  /** The first question, truncated -- set once at creation and never
   * updated, so a conversation's own history entry doesn't change title
   * out from under you as it grows (the same reason chat products title
   * a thread from its opener, not its latest message). */
  title: string;
  startedAt: string;
  /** Bumped on every new entry so the rail can sort "most recently
   * active first" instead of "most recently created first" -- a thread
   * you just added a follow-up to should jump back to the top. */
  lastActiveAt: string;
  entries: ConversationEntry[];
}

function truncateTitle(question: string): string {
  const trimmed = question.trim();
  return trimmed.length > TITLE_MAX_CHARS ? `${trimmed.slice(0, TITLE_MAX_CHARS - 1)}…` : trimmed;
}

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Conversation[]) : [];
  } catch {
    return [];
  }
}

function saveConversations(conversations: Conversation[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.slice(0, MAX_CONVERSATIONS)));
  } catch {
    // best-effort only (e.g. private browsing may block storage) -- the
    // runs themselves are still safely in DynamoDB regardless
  }
}

/** Starts a new conversation (from its first question) and persists it
 * immediately -- callers then call `addEntryToConversation` with the
 * same id once that first question's run actually starts. Returns the
 * new conversation so the caller has its id without a second read. */
export function startConversation(firstQuestion: string): Conversation {
  const conversation: Conversation = {
    id: crypto.randomUUID(),
    title: truncateTitle(firstQuestion),
    startedAt: new Date().toISOString(),
    lastActiveAt: new Date().toISOString(),
    entries: [],
  };
  saveConversations([conversation, ...loadConversations()]);
  return conversation;
}

export function addEntryToConversation(conversationId: string, entry: ConversationEntry): void {
  const conversations = loadConversations();
  const target = conversations.find((c) => c.id === conversationId);
  if (!target) return; // Conversation was cleared (e.g. a sign-out) mid-flight -- nothing to append to.
  target.entries = [...target.entries, entry].slice(-MAX_ENTRIES_PER_CONVERSATION);
  target.lastActiveAt = entry.submitted_at;
  conversations.sort((a, b) => (a.id === conversationId ? -1 : b.id === conversationId ? 1 : 0));
  saveConversations(conversations);
}

export function clearConversations(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // best-effort, same as saveConversations above
  }
}
