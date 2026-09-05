import type { RunRecord } from "../api";
import { Markdown } from "./Markdown";
import { TraceView } from "./TraceView";

const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"]);

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** Renders a run record from `GET /runs/{run_id}` -- covers all three
 * execution paths. `trace` is present once the run's Lambda has written
 * its evidence to S3 (see api.ts's `RunRecord` doc comment); a run still
 * in progress, or one from before that evidence write existed, simply
 * doesn't have one yet. */
export function RunResultView({ run }: { run: RunRecord }) {
  const pending = !isTerminal(run.status);

  return (
    <div className="result">
      <div className={`status-badge ${pending ? "pending" : run.status.toLowerCase()}`}>{run.status}</div>
      {typeof run.safe === "boolean" && (
        <div className={`safe-badge ${run.safe ? "safe" : "unsafe"}`}>{run.safe ? "SAFE" : "UNSAFE -- rejected"}</div>
      )}
      {run.answer && <Markdown text={run.answer} />}
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
      {run.trace ? (
        <TraceView trace={run.trace} />
      ) : (
        !pending && (
          <p className="meta rejected-label">No grounding trace was found for this run (it may predate evidence persistence).</p>
        )
      )}
    </div>
  );
}
