import type { RunRecord } from "../api";

const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"]);

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** Renders the *compact* record `GET /runs/{run_id}` returns for the
 * async paths -- deliberately simpler than `TraceView` (see api.ts's
 * `RunRecord` doc comment): only the synchronous `/ask` path persists a
 * full grounding trace, so an async run genuinely has less to show. */
export function RunResultView({ run }: { run: RunRecord }) {
  const pending = !isTerminal(run.status);

  return (
    <div className="result">
      <div className={`status-badge ${pending ? "pending" : run.status.toLowerCase()}`}>{run.status}</div>
      {typeof run.safe === "boolean" && (
        <div className={`safe-badge ${run.safe ? "safe" : "unsafe"}`}>{run.safe ? "SAFE" : "UNSAFE -- rejected"}</div>
      )}
      {run.answer && <p className="answer">{run.answer}</p>}
      {run.error_message && <p className="error">{run.error_message}</p>}
      <p className="meta">
        run_id: <code>{run.run_id}</code> &middot; execution_type: <code>{run.execution_type}</code>
        {run.narrator_backend && (
          <>
            {" "}
            &middot; narrator: <code>{run.narrator_backend}</code>
          </>
        )}
      </p>
      {!pending && (
        <p className="meta rejected-label">
          Only the synchronous /ask path persists a full grounding trace (safety checks, grounded facts, sources) -- this async
          path's DynamoDB record only ever stores the answer, safety verdict, and narrator backend shown above.
        </p>
      )}
    </div>
  );
}
