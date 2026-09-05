import { useEffect, useRef, useState } from "react";
import { config } from "../config";
import { askQuestion, startRun, enqueueJob, getRun, cancelRun, ApiError, type AskResponse, type RunRecord } from "../api";
import { TraceView } from "./TraceView";
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

  const pollHandle = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopPolling() {
    if (pollHandle.current !== null) {
      clearInterval(pollHandle.current);
      pollHandle.current = null;
    }
  }

  // Stop any in-flight poll if the component unmounts.
  useEffect(() => stopPolling, []);

  function pollUntilTerminal(runId: string) {
    stopPolling();
    // The Step Functions path returns from `POST /runs` as soon as
    // `start_execution` is accepted, *before* the state machine's first
    // task (mark_running.py) has actually written the DynamoDB record --
    // polling immediately can genuinely 404 for the first tick or two.
    // (The SQS path writes its record synchronously before returning
    // 202, so this race doesn't apply there, but tolerating it uniformly
    // is simpler than branching on execution_type here.) Found live
    // testing this exact polling loop.
    let consecutiveNotFound = 0;
    const MAX_NOT_FOUND_TICKS = 10;
    pollHandle.current = setInterval(async () => {
      try {
        const run = await getRun(runId);
        consecutiveNotFound = 0;
        setAsyncResult(run);
        if (isTerminal(run.status)) stopPolling();
      } catch (err) {
        if (err instanceof ApiError && err.status === 404 && ++consecutiveNotFound <= MAX_NOT_FOUND_TICKS) {
          return; // not written yet -- keep polling rather than erroring out
        }
        setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
        stopPolling();
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    stopPolling();
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
    } catch (err) {
      // A 409 here is informative, not fatal -- the run may have already
      // finished naturally, racing the cancel request. Show it and keep
      // polling; the next poll tick will reflect whatever really happened.
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setCancelling(false);
    }
  }

  async function handleSelectHistoryEntry(entry: HistoryEntry) {
    stopPolling();
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

  const pending = asyncResult !== null && !isTerminal(asyncResult.status);

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
          <p className="answer">{syncResult.answer}</p>
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
