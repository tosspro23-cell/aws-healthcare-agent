import type { AgentTrace } from "../api";

export function TraceView({ trace }: { trace: AgentTrace }) {
  return (
    <div className="trace">
      <section>
        <h3>Safety checks</h3>
        <ul className="checks">
          {trace.safety_checks.map((check) => (
            <li key={check.name} className={check.passed ? "pass" : "fail"}>
              <span className="badge">{check.passed ? "PASS" : "FAIL"}</span>
              <span className="check-name">{check.name}</span>
              {check.detail && <span className="check-detail">{check.detail}</span>}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Grounded facts ({trace.grounded_facts.length})</h3>
        <ul className="facts">
          {trace.grounded_facts.map((fact, i) => (
            <li key={i}>
              <div className="claim">{fact.claim}</div>
              <div className="meta">
                source: {fact.source_type} / {fact.source_ref}
                {fact.numeric_values.length > 0 && (
                  <>
                    {" "}
                    &middot; values: {fact.numeric_values.join(", ")}
                    {fact.unit ? ` ${fact.unit}` : ""}
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      </section>

      {trace.limitations.length > 0 && (
        <section>
          <h3>Limitations</h3>
          <ul>
            {trace.limitations.map((limitation, i) => (
              <li key={i}>
                <strong>{limitation.kind}:</strong> {limitation.detail}
              </li>
            ))}
          </ul>
        </section>
      )}

      <details>
        <summary>Tool calls ({trace.tool_calls.length})</summary>
        <ul className="tool-calls">
          {trace.tool_calls.map((call, i) => (
            <li key={i}>
              <code>{call.name}</code>: {call.result_summary}
            </li>
          ))}
        </ul>
      </details>

      {trace.retrieved_chunks.length > 0 && (
        <details>
          <summary>Retrieved knowledge chunks ({trace.retrieved_chunks.length})</summary>
          <ul>
            {trace.retrieved_chunks.map((rc, i) => (
              <li key={i}>
                <a href={rc.chunk.source_url} target="_blank" rel="noreferrer">
                  {rc.chunk.title}
                </a>{" "}
                ({rc.chunk.source_name}, score {rc.score.toFixed(2)})
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
