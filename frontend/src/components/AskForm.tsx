import { useEffect, useRef, useState } from "react";
import { config } from "../config";
import { askQuestion, startRun, enqueueJob, getRun, cancelRun, ApiError, type AskResponse, type RunRecord } from "../api";
import { TraceView } from "./TraceView";
import { Markdown } from "./Markdown";
import { RunResultView, isTerminal } from "./RunResultView";
import { addHistoryEntry, type HistoryEntry } from "../history";
import { RunHistory } from "./RunHistory";

type Mode = "sync" | "step_functions" | "queue";

const POLL_INTERVAL_MS = 1000;

const MODE_LABELS: Record<Mode, string> = {
  sync: "Ask",
  step_functions: "Start run (Step Functions)",
  queue: "Enqueue job (Queue)",
};

export function AskForm() {
  const [mode, setMode] = useState<Mode>("sync");
  const [userId, setUserId] = useState(config.demoUserId);
  const [question, setQuestion] = useState("What should I focus on first in my results?");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<AskResponse | null>(null);
  const [asyncResult, setAsyncResult] = useState<RunRecord | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [historyVersion, setHistoryVersion] = useState(0);
  // Set when polling had to give up (a non-tolerated error) without ever
  // reaching a terminal status -- see `pollUntilTerminal`'s catch branch.
  // Without this, `pending` (derived from the last-known, now-stale
  // status) stayed true forever, permanently disabling submit and mode
  // switching even though nothing was actually still in flight. Found by
  // a second independent review.
  const [pollingStalled, setPollingStalled] = useState(false);

  // A monotonic generation counter, bumped every time polling starts or
  // is explicitly stopped. Each poll loop closes over the generation it
  // was started with and checks it before ever touching state, so a
  // still-in-flight request from a *previous* loop (superseded by a new
  // submission or a different history-entry selection) can't overwrite
  // newer state when it finally resolves -- found by a second independent
  // review reproducing exactly that ordering.
  const pollGeneration = useRef(0);
  const pollTimeoutHandle = useRef<ReturnType<typeof setTimeout> | null>(null);

  function stopPolling() {
    pollGeneration.current += 1;
    if (pollTimeoutHandle.current !== null) {
      clearTimeout(pollTimeoutHandle.current);
      pollTimeoutHandle.current = null;
    }
  }

  // Stop any in-flight poll if the component unmounts.
  useEffect(() => stopPolling, []);

  function pollUntilTerminal(runId: string) {
    stopPolling();
    setPollingStalled(false);
    const myGeneration = pollGeneration.current;
    let consecutiveNotFound = 0;
    const MAX_NOT_FOUND_TICKS = 10;

    // A self-scheduling setTimeout, not setInterval: the next request is
    // only scheduled after the current one resolves, so two requests for
    // the same poll loop can never be in flight at once (the race a
    // second independent review reproduced with setInterval -- a slower
    // earlier tick resolving after a faster later one, undoing an
    // already-terminal state).
    async function tick() {
      if (pollGeneration.current !== myGeneration) return; // superseded before this tick even started
      try {
        const run = await getRun(runId);
        if (pollGeneration.current !== myGeneration) return; // superseded while this request was in flight
        consecutiveNotFound = 0;
        setAsyncResult(run);
        if (!isTerminal(run.status)) {
          pollTimeoutHandle.current = setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (pollGeneration.current !== myGeneration) return;
        // The Step Functions path returns from `POST /runs` as soon as
        // `start_execution` is accepted, *before* the state machine's
        // first task (mark_running.py) has actually written the
        // DynamoDB record -- polling immediately can genuinely 404 for
        // the first tick or two. (The SQS path writes its record
        // synchronously before returning 202, so this race doesn't
        // apply there, but tolerating it uniformly is simpler than
        // branching on execution_type here.) Found live testing this
        // exact polling loop.
        if (err instanceof ApiError && err.status === 404 && ++consecutiveNotFound <= MAX_NOT_FOUND_TICKS) {
          pollTimeoutHandle.current = setTimeout(tick, POLL_INTERVAL_MS);
          return;
        }
        setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
        setPollingStalled(true);
      }
    }

    pollTimeoutHandle.current = setTimeout(tick, POLL_INTERVAL_MS);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    stopPolling();
    setPollingStalled(false);
    setLoading(true);
    setError(null);
    setSyncResult(null);
    setAsyncResult(null);

    try {
      if (mode === "sync") {
        const result = await askQuestion(userId, question);
        setSyncResult(result);
        addHistoryEntry({ run_id: result.run_id, question, execution_type: "SYNC", submitted_at: new Date().toISOString() });
      } else {
        const executionType = mode === "step_functions" ? "STEP_FUNCTIONS" : "SQS";
        const starter = mode === "step_functions" ? startRun : enqueueJob;
        const started = await starter(userId, question);
        addHistoryEntry({ run_id: started.run_id, question, execution_type: executionType, submitted_at: new Date().toISOString() });
        // Show an optimistic pending state immediately rather than
        // blocking on a `getRun` call here -- the Step Functions path's
        // DynamoDB record doesn't exist yet at this exact moment (see
        // `pollUntilTerminal`'s comment), so a synchronous fetch right
        // now would reliably 404 for that path.
        setAsyncResult({
          run_id: started.run_id,
          status: started.status as RunRecord["status"],
          execution_type: executionType,
          user_id: userId,
          question,
        });
        pollUntilTerminal(started.run_id);
      }
      setHistoryVersion((v) => v + 1);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleCancel() {
    if (!asyncResult) return;
    setCancelling(true);
    try {
      await cancelRun(asyncResult.run_id);
      // Apply the result immediately rather than "wait for the next poll
      // tick" -- a second independent review found that if polling had
      // already stopped (e.g. `pollingStalled`, or the run raced to a
      // terminal state right as this fired), there might never be
      // another tick, leaving the screen stuck showing the pre-cancel
      // status indefinitely despite the cancellation having succeeded.
      stopPolling();
      const run = await getRun(asyncResult.run_id);
      setAsyncResult(run);
    } catch (err) {
      // A 409 here is informative, not fatal -- the run may have already
      // finished naturally, racing the cancel request. Refresh from the
      // real current state either way instead of leaving stale data
      // displayed.
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
      try {
        stopPolling();
        setAsyncResult(await getRun(asyncResult.run_id));
      } catch {
        // Best-effort refresh only; the error above is already shown.
      }
    } finally {
      setCancelling(false);
    }
  }

  async function handleSelectHistoryEntry(entry: HistoryEntry) {
    stopPolling();
    setPollingStalled(false);
    setError(null);
    setSyncResult(null);
    setAsyncResult(null);
    setLoading(true);
    try {
      const run = await getRun(entry.run_id);
      setAsyncResult(run);
      if (!isTerminal(run.status)) pollUntilTerminal(entry.run_id);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setLoading(false);
    }
  }

  // `pollingStalled` overrides this: a run whose last known status is
  // non-terminal but whose polling gave up (see `pollUntilTerminal`'s
  // catch branch) is *not* known to still be pending -- treating it as
  // pending forever would permanently lock submission and mode
  // switching for no recoverable reason. Found by a second independent
  // review.
  const pending = asyncResult !== null && !isTerminal(asyncResult.status) && !pollingStalled;

  return (
    <div>
      <form onSubmit={handleSubmit} className="ask-form">
        <div className="mode-tabs">
          {(Object.keys(MODE_LABELS) as Mode[]).map((m) => (
            <button
              type="button"
              key={m}
              className={`mode-tab ${mode === m ? "active" : ""}`}
              onClick={() => setMode(m)}
              disabled={loading || pending}
            >
              {MODE_LABELS[m]}
            </button>
          ))}
        </div>
        <label>
          User ID
          <input value={userId} onChange={(e) => setUserId(e.target.value)} />
        </label>
        <label>
          Question
          <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} />
        </label>
        <button type="submit" disabled={loading || pending}>
          {loading ? "Working..." : MODE_LABELS[mode]}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {pending && (
        <button className="cancel-button" onClick={handleCancel} disabled={cancelling}>
          {cancelling ? "Cancelling..." : "Cancel this run"}
        </button>
      )}

      {syncResult && (
        <div className="result">
          <div className={`safe-badge ${syncResult.safe ? "safe" : "unsafe"}`}>{syncResult.safe ? "SAFE" : "UNSAFE -- rejected"}</div>
          <Markdown text={syncResult.answer} />
          <p className="meta">
            run_id: <code>{syncResult.run_id}</code> &middot; narrator: <code>{syncResult.trace.narrator_backend}</code> &middot;
            intent: <code>{syncResult.trace.intent}</code>
          </p>
          <TraceView trace={syncResult.trace} />
        </div>
      )}

      {asyncResult && <RunResultView run={asyncResult} />}

      <RunHistory version={historyVersion} onSelect={handleSelectHistoryEntry} />
    </div>
  );
}
