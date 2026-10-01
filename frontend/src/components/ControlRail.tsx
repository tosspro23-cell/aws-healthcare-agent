import type { Engine, Persona } from "../api";
import type { Mode } from "./Workbench";
import { RunHistory } from "./RunHistory";
import type { HistoryEntry } from "../history";

// "Sync", not "Ask" -- the latter used to be this mode's label, but a
// segmented-control button named exactly "Ask" is indistinguishable
// (both visually and to assistive tech, where it collides on accessible
// name) from the composer's own "Ask" send button once the old "sync"
// meta text that used to disambiguate them was dropped. "Sync" also
// reads more consistently alongside "Step Fns"/"Queue" as a set of
// execution-mechanism names rather than one verb among two nouns.
const MODE_LABELS: Record<Mode, { label: string; title: string }> = {
  sync: { label: "Sync", title: "Ask (sync)" },
  step_functions: { label: "Step Fns", title: "Step Functions (async)" },
  queue: { label: "Queue", title: "Queue (async)" },
};

/** The Workbench's left-hand control rail: every request-shaping choice
 * (which engine, which persona, which of the three AWS execution paths)
 * lives here as its own clearly-labeled region, plus this browser's run
 * history -- replacing the old single-column layout's stacked
 * dropdowns and mode-tab row. Engine gets a color identity (blue = V1,
 * teal = V2) that reappears on every turn's badge and, for V2, on the
 * evidence panel itself, so the same color always means the same
 * engine everywhere in the app.
 *
 * Engine/Persona/Mode render as compact horizontal segmented controls
 * (a single row per section) rather than stacked full-width buttons --
 * found live, on a real wide/tall monitor, that the stacked version ate
 * so much vertical space the run history below it had almost no room
 * left. A segmented row says the same thing in a third the height. */
export function ControlRail({
  engine,
  setEngine,
  persona,
  setPersona,
  mode,
  setMode,
  disabled,
  historyVersion,
  onSelectHistoryEntry,
}: {
  engine: Engine;
  setEngine: (e: Engine) => void;
  persona: Persona;
  setPersona: (p: Persona) => void;
  mode: Mode;
  setMode: (m: Mode) => void;
  disabled: boolean;
  historyVersion: number;
  onSelectHistoryEntry: (entry: HistoryEntry) => void;
}) {
  return (
    <aside className="rail">
      <div>
        <div className="rail-section-label">Engine</div>
        <div className="segmented" role="tablist" aria-label="Engine">
          <button
            type="button"
            className={`segmented-option ${engine === "v1" ? "active" : ""}`}
            onClick={() => setEngine("v1")}
            disabled={disabled}
            title="V1 -- heuristic pipeline"
          >
            <span className="engine-dot v1" />
            V1
          </button>
          <button
            type="button"
            className={`segmented-option ${engine === "v2" ? "active" : ""}`}
            onClick={() => setEngine("v2")}
            disabled={disabled}
            title="V2 -- tool-calling agent"
          >
            <span className="engine-dot v2" />
            V2
          </button>
        </div>
      </div>

      <div>
        <div className="rail-section-label">Persona</div>
        <div className="segmented" role="tablist" aria-label="Persona">
          <button
            type="button"
            className={`segmented-option ${persona === "patient" ? "active" : ""}`}
            onClick={() => setPersona("patient")}
            disabled={disabled}
            title="Patient"
          >
            <svg className="persona-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <circle cx="12" cy="8" r="3.4" />
              <path d="M5 20c0-3.9 3.1-7 7-7s7 3.1 7 7" />
            </svg>
            Patient
          </button>
          <button
            type="button"
            className={`segmented-option ${persona === "clinician" ? "active" : ""}`}
            onClick={() => setPersona("clinician")}
            disabled={disabled}
            title="Clinician"
          >
            <svg className="persona-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M9 3v4a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2V3" />
              <rect x="5" y="7" width="14" height="14" rx="2" />
              <path d="M12 12v5M9.5 14.5h5" />
            </svg>
            Clinician
          </button>
        </div>
      </div>

      <div>
        <div className="rail-section-label">Mode</div>
        <div className="segmented" role="tablist" aria-label="Mode">
          {(Object.keys(MODE_LABELS) as Mode[]).map((m) => (
            <button
              type="button"
              key={m}
              className={`segmented-option ${mode === m ? "active" : ""}`}
              onClick={() => setMode(m)}
              disabled={disabled}
              title={MODE_LABELS[m].title}
            >
              {MODE_LABELS[m].label}
            </button>
          ))}
        </div>
      </div>

      <div className="rail-divider" />

      <div className="rail-history-wrap">
        <div className="rail-section-label">Recent runs</div>
        <RunHistory version={historyVersion} onSelect={onSelectHistoryEntry} disabled={disabled} />
      </div>
    </aside>
  );
}
