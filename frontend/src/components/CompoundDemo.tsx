import { useRef, useState } from "react";
import { config } from "../config";
import { TraceView } from "./TraceView";
import { Markdown } from "./Markdown";
import type { AskResponse } from "../api";

/** V2 (compound-reasoning, tool-calling) demo -- talks to the *local*
 * dev server (`scripts/dev_server.py`), never the real deployed
 * `VITE_API_BASE_URL`. See that script's own module docstring for why:
 * this is a local-only, unauthenticated demo/test surface, deliberately
 * kept separate from the production Cognito-authenticated path rather
 * than folding real AWS deployment into a demo pass.
 *
 * Progress is shown live via Server-Sent Events as each `on_stage`
 * checkpoint fires server-side (planning, tool calls, narration) --
 * not a single long wait followed by a sudden result. See
 * `orchestrator.run_compound_reasoning`'s own docstring: this project's
 * process is a small, fixed number of coarse stages, not a long
 * autonomous loop, so stage-level progress (not token streaming) is the
 * right amount of infrastructure for it. */

const DEV_SERVER_URL = "http://localhost:8000";

const EXAMPLE_QUESTIONS = [
  "Compare my LDL and A1C trends and tell me if my reported diet change is helping.",
  "Given my kidney and liver markers, is there anything I should prioritize together?",
  "Is a vitamin D supplement safe for me given my allergies and current level?",
];

type Persona = "patient" | "clinician";

export function CompoundDemo() {
  const [userId, setUserId] = useState(config.demoUserId);
  const [question, setQuestion] = useState(EXAMPLE_QUESTIONS[0]);
  const [persona, setPersona] = useState<Persona>("clinician");
  const [stages, setStages] = useState<string[]>([]);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);

  function stop() {
    sourceRef.current?.close();
    sourceRef.current = null;
    setRunning(false);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    stop();
    setStages([]);
    setResult(null);
    setError(null);
    setRunning(true);

    const url = `${DEV_SERVER_URL}/ask_compound/stream?${new URLSearchParams({ user_id: userId, question, persona })}`;
    const source = new EventSource(url);
    sourceRef.current = source;

    source.addEventListener("stage", (event) => {
      const { message } = JSON.parse((event as MessageEvent).data);
      setStages((prev) => [...prev, message]);
    });
    source.addEventListener("done", (event) => {
      setResult(JSON.parse((event as MessageEvent).data));
      stop();
    });
    source.addEventListener("error", (event) => {
      const raw = (event as MessageEvent).data;
      if (raw) setError(JSON.parse(raw).error ?? raw);
    });
    // The native EventSource `onerror` (connection-level -- e.g. the dev
    // server isn't running at all) is distinct from the `error` *named*
    // SSE event above (a real, server-reported failure inside
    // `ask_compound` -- see `dev_server.py`'s `run()`). Both need
    // surfacing; only this one implies the connection itself is gone.
    source.onerror = () => {
      setError((prev) => prev ?? `Could not reach the local dev server at ${DEV_SERVER_URL}. Is scripts/dev_server.py running?`);
      stop();
    };
  }

  return (
    <div className="compound-demo">
      <h2>V2 demo: compound reasoning (local, tool-calling)</h2>
      <p className="meta">
        Talks to a local dev server (<code>scripts/dev_server.py</code>), not the deployed AWS backend above. Real Bedrock calls for
        planning and narration.
      </p>

      <form onSubmit={handleSubmit} className="ask-form">
        <label>
          User ID
          <input value={userId} onChange={(e) => setUserId(e.target.value)} disabled={running} />
        </label>
        <label>
          Persona
          <select value={persona} onChange={(e) => setPersona(e.target.value as Persona)} disabled={running}>
            <option value="patient">Patient</option>
            <option value="clinician">Clinician</option>
          </select>
        </label>
        <label>
          Question
          <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} disabled={running} />
        </label>
        <div className="example-questions">
          {EXAMPLE_QUESTIONS.map((q) => (
            <button type="button" key={q} className="example-question" onClick={() => setQuestion(q)} disabled={running}>
              {q}
            </button>
          ))}
        </div>
        <button type="submit" disabled={running}>
          {running ? "Working..." : "Ask (V2)"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {stages.length > 0 && (
        <ul className="stage-log">
          {stages.map((s, i) => (
            <li key={i} className={i === stages.length - 1 && running ? "current" : "done"}>
              {s}
            </li>
          ))}
        </ul>
      )}

      {result && (
        <div className="result">
          <div className={`safe-badge ${result.safe ? "safe" : "unsafe"}`}>{result.safe ? "SAFE" : "UNSAFE -- rejected"}</div>
          <Markdown text={result.answer} />
          <p className="meta">
            narrator: <code>{result.trace.narrator_backend}</code> &middot; disposition: <code>{result.trace.disposition}</code>
          </p>
          <TraceView trace={result.trace} />
        </div>
      )}
    </div>
  );
}
