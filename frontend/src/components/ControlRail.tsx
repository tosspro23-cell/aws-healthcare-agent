import type { Engine, Persona } from "../api";
import type { Mode } from "./Workbench";
import { RunHistory } from "./RunHistory";
import type { HistoryEntry } from "../history";

const MODE_LABELS: Record<Mode, { label: string; meta: string }> = {
  sync: { label: "Ask", meta: "sync" },
  step_functions: { label: "Step Functions", meta: "async" },
  queue: { label: "Queue", meta: "async" },
};

/** The Workbench's left-hand control rail: every request-shaping choice
 * (which engine, which persona, which of the three AWS execution paths)
 * lives here as its own clearly-labeled region, plus this browser's run
 * history -- replacing the old single-column layout's stacked
 * dropdowns and mode-tab row. Engine gets a color identity (blue = V1,
 * teal = V2) that reappears on every turn's badge and, for V2, on the
 * evidence panel itself, so the same color always means the same
 * engine everywhere in the app. */
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
        <div className="rail-options">
          <button
            type="button"
            className={`rail-option ${engine === "v1" ? "active" : ""}`}
            onClick={() => setEngine("v1")}
            disabled={disabled}
          >
            <span className="engine-dot v1" />
            V1 &middot; heuristic pipeline
          </button>
          <button
            type="button"
            className={`rail-option ${engine === "v2" ? "active" : ""}`}
            onClick={() => setEngine("v2")}
            disabled={disabled}
          >
            <span className="engine-dot v2" />
            V2 &middot; tool-calling agent
          </button>
        </div>
      </div>

      <div>
        <div className="rail-section-label">Persona</div>
        <div className="rail-options">
          <button
            type="button"
            className={`rail-option ${persona === "patient" ? "active" : ""}`}
            onClick={() => setPersona("patient")}
            disabled={disabled}
          >
            <svg className="persona-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <circle cx="12" cy="8" r="3.4" />
              <path d="M5 20c0-3.9 3.1-7 7-7s7 3.1 7 7" />
            </svg>
            Patient
          </button>
          <button
            type="button"
            className={`rail-option ${persona === "clinician" ? "active" : ""}`}
            onClick={() => setPersona("clinician")}
            disabled={disabled}
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

      <div className="rail-divider" />

      <div>
        <div className="rail-section-label">Mode</div>
        <div className="rail-options">
          {(Object.keys(MODE_LABELS) as Mode[]).map((m) => (
            <button
              type="button"
              key={m}
              className={`rail-option ${mode === m ? "active" : ""}`}
              onClick={() => setMode(m)}
              disabled={disabled}
            >
              {MODE_LABELS[m].label}
              <span className="option-meta">{MODE_LABELS[m].meta}</span>
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
