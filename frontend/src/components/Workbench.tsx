import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { config } from "../config";
import {
  askQuestion,
  startRun,
  enqueueJob,
  getRun,
  cancelRun,
  ApiError,
  type AgentTrace,
  type RunRecord,
  type Engine,
  type Persona,
} from "../api";
import { ControlBar } from "./ControlBar";
import { ControlRail } from "./ControlRail";
import { ConversationPanel } from "./ConversationPanel";
import { EvidencePanel } from "./EvidencePanel";
import { startConversation, addEntryToConversation, type Conversation, type ConversationEntry } from "../history";

export type Mode = "sync" | "step_functions" | "queue";

const RAIL_MIN = 180;
const RAIL_MAX = 340;
const RAIL_DEFAULT = 220;
const EVIDENCE_MIN = 280;
const EVIDENCE_MAX = 560;
const EVIDENCE_DEFAULT = 340;
const LAYOUT_STORAGE_KEY = "care_agent_workbench_layout";

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

function loadColumnWidths(): { railWidth: number; evidenceWidth: number } {
  try {
    const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!raw) return { railWidth: RAIL_DEFAULT, evidenceWidth: EVIDENCE_DEFAULT };
    const parsed = JSON.parse(raw) as { railWidth?: unknown; evidenceWidth?: unknown };
    return {
      railWidth: clamp(Number(parsed.railWidth) || RAIL_DEFAULT, RAIL_MIN, RAIL_MAX),
      evidenceWidth: clamp(Number(parsed.evidenceWidth) || EVIDENCE_DEFAULT, EVIDENCE_MIN, EVIDENCE_MAX),
    };
  } catch {
    // Corrupt or inaccessible localStorage is a per-viewer convenience
    // lost, not a reason to break the page -- fall back to the defaults.
    return { railWidth: RAIL_DEFAULT, evidenceWidth: EVIDENCE_DEFAULT };
  }
}

/** A thin draggable strip between two grid columns. Uses pointer capture
 * (not window-level mousemove/mouseup listeners) so the drag keeps
 * tracking correctly even if the cursor leaves the 6px hit target --
 * standard behavior for a resize handle, and far less code than manually
 * wiring up global listeners. */
function ColumnResizeHandle({ onDrag, label }: { onDrag: (deltaX: number) => void; label: string }) {
  const lastX = useRef<number | null>(null);

  function handlePointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    lastX.current = e.clientX;
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function handlePointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (lastX.current === null) return;
    const delta = e.clientX - lastX.current;
    lastX.current = e.clientX;
    if (delta !== 0) onDrag(delta);
  }
  function handlePointerUp(e: ReactPointerEvent<HTMLDivElement>) {
    lastX.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
  }
  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "ArrowLeft") onDrag(-10);
    else if (e.key === "ArrowRight") onDrag(10);
  }

  return (
    <div
      className="col-resize-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onKeyDown={handleKeyDown}
    />
  );
}

export interface Turn {
  id: string;
  question: string;
  engine: Engine;
  persona: Persona;
  mode: Mode;
  status: "pending" | "succeeded" | "failed" | "cancelled";
  runId?: string;
  answer?: string;
  safe?: boolean;
  trace?: AgentTrace;
  currentStage?: string;
  errorMessage?: string;
  /** Set when polling gave up on a non-tolerated error without ever
   * reaching a terminal status -- see `pollUntilTerminal`'s catch
   * branch. Without this, a turn stuck in "pending" would permanently
   * disable the composer and every rail control for no recoverable
   * reason. Found by a second independent review of the single-turn
   * predecessor of this component (`AskForm.tsx`) -- ported forward,
   * not rediscovered. */
  pollStalled?: boolean;
}

const POLL_INTERVAL_MS = 1000;
const MAX_NOT_FOUND_TICKS = 10;

const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"]);
function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** Pure mapping from a `RunRecord` to the `Turn` fields it determines --
 * shared by the live-polling path (`applyRunRecord`, one turn at a time)
 * and restoring a whole conversation from history (`handleSelectConversation`,
 * many turns at once from `Promise.allSettled`), so the two paths can't
 * silently drift into mapping the same status differently. */
function runRecordToPatch(run: RunRecord): Partial<Turn> {
  return {
    status: !isTerminal(run.status) ? "pending" : run.status === "CANCELLED" ? "cancelled" : run.status === "SUCCEEDED" ? "succeeded" : "failed",
    answer: run.answer,
    safe: run.safe,
    trace: run.trace,
    currentStage: run.current_stage,
    errorMessage: run.error_message,
  };
}

function executionTypeFor(mode: Mode): ConversationEntry["execution_type"] {
  if (mode === "sync") return "SYNC";
  if (mode === "step_functions") return "STEP_FUNCTIONS";
  return "SQS";
}

function modeFromExecutionType(executionType: ConversationEntry["execution_type"]): Mode {
  if (executionType === "SYNC") return "sync";
  if (executionType === "STEP_FUNCTIONS") return "step_functions";
  return "queue";
}

const MAX_CONTEXT_TURNS = 4;

/** Folds recent turns from the *same conversation* into the question
 * text actually sent to the backend -- the real point of the feature,
 * not cosmetic: the model genuinely sees prior Q&A, not just a UI that
 * looks like a conversation while every call stays independent. Chosen
 * over a backend session (a new DynamoDB table + API surface) because
 * every execution path here is already stateless per-call; this works
 * within that without any infra change. The explicit instruction not to
 * restate old figures is a mitigation, not a guarantee -- if the model
 * does repeat an earlier number without this turn's own tool calls
 * re-grounding it, `numeric_grounding` won't recognize it and the answer
 * falls back to the deterministic template, exactly like any other
 * ungrounded claim. That's an accepted tradeoff (see docs/DECISIONS.md),
 * not a gap nobody noticed. `Turn.question` itself is never touched --
 * only the text actually sent to the API includes this context, so the
 * question bubble in the UI keeps showing exactly what the user typed. */
function buildContextualQuestion(priorTurns: Turn[], newQuestion: string): string {
  const relevant = priorTurns.filter((t) => t.status === "succeeded" && t.answer).slice(-MAX_CONTEXT_TURNS);
  if (relevant.length === 0) return newQuestion;
  const transcript = relevant.map((t) => `Q: ${t.question}\nA: ${t.answer}`).join("\n\n");
  return (
    "Context from earlier in this conversation, for continuity only -- base any new numeric claims on your own " +
    "fresh data lookups for this question, not by repeating earlier figures unless they're reconfirmed now:\n\n" +
    `${transcript}\n\nNew question: ${newQuestion}`
  );
}

/** The Workbench redesign's real point: a three-zone layout (control
 * rail, a running conversation thread, a persistent evidence panel)
 * instead of the old single scrolling column of stacked
 * dropdowns/results (`AskForm.tsx`/`RunResultView.tsx`, retired by this
 * component). See docs/DECISIONS.md for the design discussion and the
 * annotated mockup this was built from.
 *
 * Multi-turn memory is real, not cosmetic, but it's client-side context
 * injection rather than a backend session: every execution path here
 * (`ask()`/`ask_compound()` via `/ask`, `/runs`, `/jobs`) is stateless
 * per call, with no session concept on the backend at all, so
 * `buildContextualQuestion` folds the active conversation's recent Q&A
 * into the text actually sent for a follow-up (see its own docstring for
 * why this was chosen over a new backend session, and the one real
 * tradeoff it accepts). `Turn.question` itself always stays exactly what
 * the user typed -- only the text sent to the API carries the extra
 * context, so the question bubble in the UI isn't cluttered by it.
 *
 * At most one turn is ever `pending` at a time, by construction (every
 * control that could start a second one -- the composer, the rail, run
 * history -- is disabled while one is in flight). This isn't a
 * technical ceiling, just the smallest scope that keeps the polling
 * model exactly as simple as `AskForm.tsx`'s already-twice-reviewed one
 * (a single generation counter + self-scheduling `setTimeout`, ported
 * here almost unchanged) -- true concurrent turns would need a
 * generation counter *per turn id* instead of one global one. */
export function Workbench() {
  const [engine, setEngine] = useState<Engine>("v1");
  const [persona, setPersona] = useState<Persona>("patient");
  const [mode, setMode] = useState<Mode>("sync");
  const [userId] = useState(config.demoUserId);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [railWidth, setRailWidth] = useState(() => loadColumnWidths().railWidth);
  const [evidenceWidth, setEvidenceWidth] = useState(() => loadColumnWidths().evidenceWidth);

  useEffect(() => {
    try {
      localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify({ railWidth, evidenceWidth }));
    } catch {
      // Best-effort only -- a lost layout preference isn't worth surfacing.
    }
  }, [railWidth, evidenceWidth]);

  const pollGeneration = useRef(0);
  const pollTimeoutHandle = useRef<ReturnType<typeof setTimeout> | null>(null);

  function stopPolling() {
    pollGeneration.current += 1;
    if (pollTimeoutHandle.current !== null) {
      clearTimeout(pollTimeoutHandle.current);
      pollTimeoutHandle.current = null;
    }
  }

  useEffect(() => stopPolling, []);

  function updateTurn(id: string, patch: Partial<Turn>) {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }

  function applyRunRecord(id: string, run: RunRecord) {
    updateTurn(id, runRecordToPatch(run));
  }

  // A self-scheduling setTimeout, not setInterval, plus a monotonic
  // generation counter -- see AskForm.tsx's original, twice-independently-
  // reviewed version of this exact function for the two real races this
  // shape prevents (an overlapping in-flight request for the same poll
  // loop; a slower, superseded response overwriting a newer turn's
  // state). Ported here unchanged in spirit, writing into `turns` by id
  // instead of a single shared `asyncResult`.
  function pollUntilTerminal(id: string, runId: string) {
    stopPolling();
    const myGeneration = pollGeneration.current;
    let consecutiveNotFound = 0;

    async function tick() {
      if (pollGeneration.current !== myGeneration) return;
      try {
        const run = await getRun(runId);
        if (pollGeneration.current !== myGeneration) return;
        consecutiveNotFound = 0;
        applyRunRecord(id, run);
        if (!isTerminal(run.status)) {
          pollTimeoutHandle.current = setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (pollGeneration.current !== myGeneration) return;
        // The Step Functions path returns from `POST /runs` as soon as
        // `start_execution` is accepted, before the state machine's
        // first task has actually written the DynamoDB record -- polling
        // immediately can genuinely 404 for the first tick or two.
        if (err instanceof ApiError && err.status === 404 && ++consecutiveNotFound <= MAX_NOT_FOUND_TICKS) {
          pollTimeoutHandle.current = setTimeout(tick, POLL_INTERVAL_MS);
          return;
        }
        updateTurn(id, { pollStalled: true });
        setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
      }
    }

    pollTimeoutHandle.current = setTimeout(tick, POLL_INTERVAL_MS);
  }

  async function handleSubmit() {
    const q = question.trim();
    if (!q || disabled) return;
    setError(null);
    setQuestion("");

    // A fresh conversation starts (and is persisted) the moment its first
    // question is asked -- not deferred until the answer comes back -- so
    // a conversation that fails or is still pending still shows up in
    // history, the same as any individual run always has.
    const conversationId = activeConversationId ?? startConversation(q).id;
    if (!activeConversationId) setActiveConversationId(conversationId);

    const sentQuestion = buildContextualQuestion(turns, q);

    const id = crypto.randomUUID();
    const turn: Turn = { id, question: q, engine, persona, mode, status: "pending" };
    setTurns((prev) => [...prev, turn]);
    setSelectedTurnId(id);

    try {
      if (mode === "sync") {
        const result = await askQuestion(userId, sentQuestion, engine, persona);
        updateTurn(id, { status: "succeeded", runId: result.run_id, answer: result.answer, safe: result.safe, trace: result.trace });
        addEntryToConversation(conversationId, { run_id: result.run_id, question: q, execution_type: "SYNC", submitted_at: new Date().toISOString(), engine, persona });
      } else {
        const starter = mode === "step_functions" ? startRun : enqueueJob;
        const started = await starter(userId, sentQuestion, undefined, persona, engine);
        updateTurn(id, { runId: started.run_id });
        addEntryToConversation(conversationId, {
          run_id: started.run_id,
          question: q,
          execution_type: executionTypeFor(mode),
          submitted_at: new Date().toISOString(),
          engine,
          persona,
        });
        pollUntilTerminal(id, started.run_id);
      }
      setHistoryVersion((v) => v + 1);
    } catch (err) {
      updateTurn(id, { status: "failed", errorMessage: err instanceof ApiError ? `${err.status}: ${err.message}` : String(err) });
    }
  }

  async function handleCancel() {
    if (!pendingTurn?.runId) return;
    setCancelling(true);
    try {
      await cancelRun(pendingTurn.runId);
      stopPolling();
      applyRunRecord(pendingTurn.id, await getRun(pendingTurn.runId));
    } catch (err) {
      // A 409 here is informative, not fatal -- the run may have already
      // finished naturally, racing the cancel request. Refresh from the
      // real current state either way instead of leaving stale data.
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
      try {
        stopPolling();
        applyRunRecord(pendingTurn.id, await getRun(pendingTurn.runId));
      } catch {
        // Best-effort refresh only; the error above is already shown.
      }
    } finally {
      setCancelling(false);
    }
  }

  /** Restores an entire past conversation, not just one run -- clicking a
   * thread in history re-fetches every one of its turns (in parallel;
   * they're independent reads) and rebuilds the full back-and-forth, the
   * same "pick a thread back up" pattern ChatGPT/Claude Code's own
   * history sidebars use. Only the *last* entry can still be non-terminal
   * when revisited: by construction only one turn is ever pending at a
   * time, so every earlier entry in this conversation must already have
   * finished before the next one could have been submitted. */
  async function handleSelectConversation(conversation: Conversation) {
    if (disabled) return;
    setError(null);
    stopPolling();

    const restored: Turn[] = conversation.entries.map((entry) => ({
      id: crypto.randomUUID(),
      question: entry.question,
      engine: entry.engine ?? "v1",
      persona: entry.persona ?? "patient",
      mode: modeFromExecutionType(entry.execution_type),
      status: "pending",
    }));
    setTurns(restored);
    setActiveConversationId(conversation.id);
    setSelectedTurnId(restored[restored.length - 1]?.id ?? null);

    const results = await Promise.allSettled(conversation.entries.map((entry) => getRun(entry.run_id)));

    setTurns((prev) =>
      prev.map((turn, i) => {
        const result = results[i];
        if (result.status === "fulfilled") {
          return { ...turn, runId: conversation.entries[i].run_id, ...runRecordToPatch(result.value) };
        }
        const err = result.reason as unknown;
        return { ...turn, status: "failed", errorMessage: err instanceof ApiError ? `${err.status}: ${err.message}` : String(err) };
      }),
    );

    const lastIndex = conversation.entries.length - 1;
    const lastResult = results[lastIndex];
    if (lastResult?.status === "fulfilled" && !isTerminal(lastResult.value.status)) {
      pollUntilTerminal(restored[lastIndex].id, conversation.entries[lastIndex].run_id);
    }
  }

  function handleNewConversation() {
    if (disabled) return;
    stopPolling();
    setError(null);
    setQuestion("");
    setTurns([]);
    setSelectedTurnId(null);
    setActiveConversationId(null);
  }

  const pendingTurn = turns.find((t) => t.status === "pending" && !t.pollStalled);
  const disabled = pendingTurn !== undefined;
  const selectedTurn = turns.find((t) => t.id === selectedTurnId);

  return (
    <>
      <ControlBar
        engine={engine}
        setEngine={setEngine}
        persona={persona}
        setPersona={setPersona}
        mode={mode}
        setMode={setMode}
        disabled={disabled}
        onNewConversation={handleNewConversation}
        hasActiveConversation={turns.length > 0}
      />
      <div
        className="workspace"
        style={{ "--rail-w": `${railWidth}px`, "--evidence-w": `${evidenceWidth}px` } as React.CSSProperties}
      >
        <ControlRail
          disabled={disabled}
          historyVersion={historyVersion}
          activeConversationId={activeConversationId}
          onSelectConversation={handleSelectConversation}
        />
        <ColumnResizeHandle label="Resize control rail" onDrag={(dx) => setRailWidth((w) => clamp(w + dx, RAIL_MIN, RAIL_MAX))} />
        <ConversationPanel
          turns={turns}
          selectedTurnId={selectedTurnId}
          onSelectTurn={setSelectedTurnId}
          question={question}
          setQuestion={setQuestion}
          onSubmit={handleSubmit}
          disabled={disabled}
          pendingTurn={pendingTurn}
          onCancel={handleCancel}
          cancelling={cancelling}
          error={error}
        />
        <ColumnResizeHandle label="Resize evidence panel" onDrag={(dx) => setEvidenceWidth((w) => clamp(w - dx, EVIDENCE_MIN, EVIDENCE_MAX))} />
        <EvidencePanel turn={selectedTurn} />
      </div>
    </>
  );
}
