import { loadHistory, type HistoryEntry } from "../history";

/** `version` is bumped by the parent after every new submission purely to
 * force this component to re-render (it's read in the dependency-less
 * render body below, not stored) -- simpler than lifting the whole
 * history array into shared state for what's otherwise a read-mostly,
 * per-viewer list backed by localStorage, not React state. */
export function RunHistory({ version: _version, onSelect }: { version: number; onSelect: (entry: HistoryEntry) => void }) {
  const entries = loadHistory();

  if (entries.length === 0) return null;

  return (
    <details className="run-history" open>
      <summary>Run history ({entries.length}) -- this browser only, from localStorage</summary>
      <ul>
        {entries.map((entry) => (
          <li key={entry.run_id}>
            <button type="button" className="history-entry" onClick={() => onSelect(entry)}>
              <span className={`execution-type-tag ${entry.execution_type.toLowerCase()}`}>{entry.execution_type}</span>
              <span className="history-question">{entry.question}</span>
              <span className="history-time">{new Date(entry.submitted_at).toLocaleTimeString()}</span>
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
