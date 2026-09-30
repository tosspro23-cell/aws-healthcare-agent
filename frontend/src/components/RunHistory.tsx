import { loadHistory, type HistoryEntry } from "../history";

/** `version` is bumped by the parent after every new submission purely to
 * force this component to re-render (it's read in the dependency-less
 * render body below, not stored) -- simpler than lifting the whole
 * history array into shared state for what's otherwise a read-mostly,
 * per-viewer list backed by localStorage, not React state.
 *
 * Lives in the Workbench's left-hand control rail, which already has its
 * own persistent section labels, so this renders a plain always-visible
 * list rather than a collapsible `<details>` -- there's no reason to make
 * "see your recent runs" a second click in a rail whose whole point is
 * to be glanceable. `disabled` mirrors every other rail control: a run
 * is being polled right now, and this app only ever tracks one poll loop
 * at a time (see `Workbench.tsx`), so opening a second run from history
 * mid-poll isn't supported. */
export function RunHistory({ version: _version, onSelect, disabled }: { version: number; onSelect: (entry: HistoryEntry) => void; disabled: boolean }) {
  const entries = loadHistory();

  if (entries.length === 0) return <p className="rail-history-empty">No runs yet this session.</p>;

  return (
    <ul className="rail-history">
      {entries.map((entry) => (
        <li key={entry.run_id}>
          <button type="button" className="history-row" onClick={() => onSelect(entry)} disabled={disabled}>
            <span className="h-q">{entry.question}</span>
            <span className="h-meta">
              {entry.engine && <span className={`engine-dot ${entry.engine} small`} />}
              {entry.execution_type} &middot; {new Date(entry.submitted_at).toLocaleTimeString()}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
