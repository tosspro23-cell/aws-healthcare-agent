import type { Turn } from "./Workbench";
import { TraceView } from "./TraceView";

/** The Workbench's dedicated right-hand region: whichever turn is
 * currently selected in the conversation gets its full trace shown
 * here, persistently, instead of requiring a scroll down past the
 * answer the way the single-column layout used to. Selecting a
 * different turn swaps this panel's content -- the core interaction the
 * three-zone layout exists for (see docs/DECISIONS.md). */
export function EvidencePanel({ turn }: { turn: Turn | undefined }) {
  return (
    <aside className="evidence">
      <div className="evidence-header">
        <div className="evidence-title">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 11.5 11 13.5 15 9" />
            <path d="M12 3 4 6v6c0 5 3.4 8.5 8 9 4.6-.5 8-4 8-9V6l-8-3Z" />
          </svg>
          Evidence
        </div>
      </div>
      <div className="evidence-scroll">
        {!turn && <p className="meta evidence-empty">Ask a question, or select a past answer, to see what grounds it.</p>}
        {turn && turn.status === "pending" && (
          <p className="meta evidence-empty">{turn.currentStage ?? "Working…"}</p>
        )}
        {turn && turn.status === "failed" && <p className="error">{turn.errorMessage ?? "This run failed."}</p>}
        {turn && turn.trace && <TraceView trace={turn.trace} />}
        {turn && turn.status === "succeeded" && !turn.trace && (
          <p className="meta evidence-empty">No grounding trace was found for this run (it may predate evidence persistence).</p>
        )}
      </div>
    </aside>
  );
}
