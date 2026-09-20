import type { AgentTrace } from "../api";

const DISPOSITION_LABEL: Record<AgentTrace["disposition"], string> = {
  answered: "Answered -- no fallback",
  answered_after_hard_fallback: "Answered -- hard fallback (policy violation caught)",
  answered_after_soft_fallback: "Answered -- soft fallback (grounding check only)",
};

export function TraceView({ trace }: { trace: AgentTrace }) {
  const fallback = trace.safety_checks.find((c) => c.name === "narrator_fallback");

  return (
    <div className="trace">
      <section>
        {/* Disposition is set once the fallback decision is final (see
            AgentTrace.disposition's own docstring) -- it never changes
            what was returned, it just makes the *why* reviewable at a
            glance instead of re-deriving it from safety_checks. */}
        <span className={`disposition disposition-${trace.disposition}`}>{DISPOSITION_LABEL[trace.disposition]}</span>
      </section>

      <section>
        <h3>Safety checks</h3>
        <ul className="checks">
          {trace.safety_checks.map((check) => (
            <li key={check.name} className={check.passed ? "pass" : "fail"}>
              <span className="badge">{check.passed ? "PASS" : "FAIL"}</span>
              <span className="check-name">{check.name}</span>
              {/* narrator_fallback is a synthetic informational entry, not
                  one of the four real checks -- it has no meaningful
                  severity of its own, so skip the tag for it. */}
              {check.name !== "narrator_fallback" && (
                <span className={`severity severity-${check.severity}`}>{check.severity}</span>
              )}
              {check.detail && <span className="check-detail">{check.detail}</span>}
            </li>
          ))}
        </ul>
      </section>

      {fallback && trace.rejected_draft && (
        <section className="rejected-draft">
          <h3>⚠ A draft was rejected and replaced</h3>
          <p className="meta">{fallback.detail}</p>
          <p className="rejected-label">
            The text below is <strong>not</strong> the answer shown above -- it's the discarded draft, kept here only so you can see
            what was rejected and why.
          </p>
          <pre className="rejected-text">{trace.rejected_draft}</pre>
        </section>
      )}

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
