import type { AgentTrace, ToolCall } from "../api";

const DISPOSITION_LABEL: Record<AgentTrace["disposition"], string> = {
  answered: "Answered -- no fallback",
  answered_after_hard_fallback: "Answered -- hard fallback (policy violation caught)",
  answered_after_soft_fallback: "Answered -- soft fallback (grounding check only)",
};

function formatDuration(ms: number | undefined): string | null {
  if (ms === undefined) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

interface OrchestrationStep {
  toolName: string;
  args: Record<string, unknown>;
  accepted: boolean;
  gateReason?: string;
  gateDurationMs?: number;
  execution?: ToolCall;
}

interface OrchestrationRound {
  index: number;
  repairReason?: string;
  planSummary: string;
  planDurationMs?: number;
  steps: OrchestrationStep[];
}

function argsSignature(name: string, args: Record<string, unknown>): string {
  const entries = Object.entries(args).sort(([a], [b]) => a.localeCompare(b));
  return `${name}|${entries.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(",")}`;
}

/** The flat `trace.tool_calls` array already has an implicit round structure
 * (see `run_compound_reasoning`, orchestrator.py): each `propose_plan` entry
 * starts a round, followed by one `capability_gate` entry per proposed call,
 * followed by the execution entries for calls newly run this round (a repair
 * round's re-proposed-but-already-executed calls get a gate entry but no new
 * execution entry). This is a pure frontend parse of data the backend already
 * produces -- no new fields needed beyond `duration_ms`. */
function parseOrchestration(toolCalls: ToolCall[]): { rounds: OrchestrationRound[]; retrieval?: ToolCall } {
  const rounds: OrchestrationRound[] = [];
  let retrieval: ToolCall | undefined;
  let current: OrchestrationRound | null = null;

  for (const call of toolCalls) {
    if (call.name === "propose_plan") {
      current = {
        index: rounds.length + 1,
        repairReason: typeof call.args.repair_reason === "string" ? call.args.repair_reason : undefined,
        planSummary: call.result_summary,
        planDurationMs: call.duration_ms,
        steps: [],
      };
      rounds.push(current);
      continue;
    }
    if (call.name === "retrieve_knowledge") {
      retrieval = call;
      continue;
    }
    if (call.name === "capability_gate") {
      if (!current) continue;
      const { tool_name, ...rest } = call.args;
      current.steps.push({
        toolName: typeof tool_name === "string" ? tool_name : "unknown",
        args: rest,
        accepted: call.ok,
        gateReason: call.ok ? undefined : call.result_summary.replace(/^rejected: /, ""),
        gateDurationMs: call.duration_ms,
      });
      continue;
    }
    if (!current) continue;
    const sig = argsSignature(call.name, call.args);
    const step = current.steps.find((s) => s.accepted && !s.execution && argsSignature(s.toolName, s.args) === sig);
    if (step) step.execution = call;
  }

  return { rounds, retrieval };
}

function OrchestrationView({ rounds, retrieval }: { rounds: OrchestrationRound[]; retrieval?: ToolCall }) {
  return (
    <section className="orchestration">
      <h3>How this was computed (V2)</h3>
      <ol className="orchestration-rounds">
        {rounds.map((round) => (
          <li key={round.index} className="orchestration-round">
            <div className="orchestration-round-header">
              <span className="orchestration-round-title">
                Round {round.index}
                {round.repairReason ? " (repair)" : ""}
              </span>
              {formatDuration(round.planDurationMs) && (
                <span className="duration-chip">{formatDuration(round.planDurationMs)}</span>
              )}
            </div>
            {round.repairReason && <p className="orchestration-repair-reason">Repairing: {round.repairReason}</p>}
            <p className="orchestration-plan-summary">{round.planSummary}</p>
            <ul className="orchestration-steps">
              {round.steps.map((step, i) => (
                <li key={i} className={step.accepted ? "accepted" : "rejected"}>
                  <div className="orchestration-step-head">
                    <span className="badge" aria-label={step.accepted ? "accepted" : "rejected"}>
                      {step.accepted ? "✓" : "✕"}
                    </span>
                    <code>{step.toolName}</code>
                    {Object.keys(step.args).length > 0 && (
                      <span className="orchestration-args">
                        {Object.entries(step.args)
                          .map(([k, v]) => `${k}=${String(v)}`)
                          .join(", ")}
                      </span>
                    )}
                    {formatDuration(step.gateDurationMs) && (
                      <span className="duration-chip">{formatDuration(step.gateDurationMs)}</span>
                    )}
                  </div>
                  {!step.accepted && <div className="orchestration-step-detail">{step.gateReason}</div>}
                  {step.execution && (
                    <div className="orchestration-step-detail">
                      {step.execution.result_summary}
                      {formatDuration(step.execution.duration_ms) && (
                        <span className="duration-chip">{formatDuration(step.execution.duration_ms)}</span>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      {retrieval && (
        <div className="orchestration-retrieval">
          <span className="orchestration-round-title">Automatic knowledge retrieval</span>
          {formatDuration(retrieval.duration_ms) && <span className="duration-chip">{formatDuration(retrieval.duration_ms)}</span>}
          <div className="orchestration-step-detail">{retrieval.result_summary}</div>
        </div>
      )}
    </section>
  );
}

export function TraceView({ trace }: { trace: AgentTrace }) {
  const fallback = trace.safety_checks.find((c) => c.name === "narrator_fallback");
  const isV2 = trace.tool_calls.some((c) => c.name === "propose_plan");
  const { rounds, retrieval } = isV2 ? parseOrchestration(trace.tool_calls) : { rounds: [], retrieval: undefined };

  return (
    <div className="trace">
      {isV2 && rounds.length > 0 && <OrchestrationView rounds={rounds} retrieval={retrieval} />}

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
              <span className="badge" aria-label={check.passed ? "passed" : "failed"}>
                {check.passed ? "✓" : "✕"}
              </span>
              <div>
                <span className="check-name">{check.name}</span>
                {/* narrator_fallback is a synthetic informational entry, not
                    one of the four real checks -- it has no meaningful
                    severity of its own, so skip the tag for it. */}
                {check.name !== "narrator_fallback" && (
                  <span className={`severity severity-${check.severity}`}> {check.severity}</span>
                )}
                {check.detail && <span className="check-detail">{check.detail}</span>}
              </div>
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
              <span className="citation-mark">[{i + 1}]</span>
              <div>
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

      {/* V2 traces get the round-grouped orchestration view above instead --
          this flat list stays only for V1, which has no round structure. */}
      {!isV2 && trace.tool_calls.length > 0 && (
        // Open by default: this is the step-by-step record of what the
        // agent actually did (which tools, in what order, with what
        // result) -- collapsed-by-default made it easy to miss entirely,
        // which is exactly the part a V2 (tool-calling) answer's reader
        // most wants to see. See docs/DECISIONS.md, 2026-09-21 entry.
        <details open>
          <summary>Tool calls ({trace.tool_calls.length})</summary>
          <ul className="tool-calls">
            {trace.tool_calls.map((call, i) => (
              <li key={i}>
                <code>{call.name}</code>: {call.result_summary}
              </li>
            ))}
          </ul>
        </details>
      )}

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
