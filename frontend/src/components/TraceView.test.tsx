/** Regression guard for the V2-only orchestration-trace view (see
 * TraceView.tsx's `parseOrchestration`): a V1 trace (no `propose_plan`
 * entry) must render exactly as before -- the flat "Tool calls" list, no
 * round-grouped section -- and a V2 trace must get the new round view
 * instead of (not in addition to) that flat list. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { TraceView } from "./TraceView";
import type { AgentTrace } from "../api";

afterEach(() => {
  cleanup();
});

function baseTrace(overrides: Partial<AgentTrace> = {}): AgentTrace {
  return {
    question_id: null,
    user_id: "user_demo_001",
    intent: "general_bloodwork_question",
    tool_calls: [],
    retrieved_chunks: [],
    grounded_facts: [],
    limitations: [],
    safety_checks: [],
    rejected_draft: null,
    narrator_backend: "mock",
    disposition: "answered",
    ...overrides,
  };
}

describe("TraceView V1 traces (unchanged)", () => {
  it("renders the flat tool-calls list and no orchestration section", () => {
    const trace = baseTrace({
      tool_calls: [{ name: "get_marker_trend", args: { concept_id: "ldl_c_mg_dl" }, result_summary: "LDL-C up", ok: true }],
    });
    render(<TraceView trace={trace} />);

    expect(screen.queryByText("How this was computed (V2)")).not.toBeInTheDocument();
    expect(screen.getByText("Tool calls (1)")).toBeInTheDocument();
  });
});

describe("TraceView V2 traces (orchestration view)", () => {
  function v2Trace(): AgentTrace {
    return baseTrace({
      total_duration_ms: 3842,
      tool_calls: [
        { name: "classify_intent", args: { question_text: "..." }, result_summary: "compound_reasoning", ok: true, duration_ms: 0.2 },
        { name: "get_user_profile", args: { user_id: "user_demo_001" }, result_summary: "display_name='Alex'", ok: true, duration_ms: 0.1 },
        { name: "get_bloodwork", args: { user_id: "user_demo_001" }, result_summary: "latest_panel=present", ok: true, duration_ms: 0.1 },
        { name: "propose_plan", args: {}, result_summary: "2 call(s) proposed: ['get_marker_trend', 'made_up_tool']", ok: true, duration_ms: 420 },
        { name: "capability_gate", args: { tool_name: "get_marker_trend", concept_id: "ldl_c_mg_dl" }, result_summary: "accepted", ok: true, duration_ms: 0.1 },
        { name: "capability_gate", args: { tool_name: "made_up_tool" }, result_summary: "rejected: Unknown tool 'made_up_tool'.", ok: false, duration_ms: 0.1 },
        { name: "get_marker_trend", args: { concept_id: "ldl_c_mg_dl" }, result_summary: "LDL-C trending up", ok: true, duration_ms: 3.2 },
        {
          name: "propose_plan",
          args: { repair_reason: "Unknown tool 'made_up_tool'." },
          result_summary: "1 call(s) proposed: ['get_marker_snapshot']",
          ok: true,
          duration_ms: 380,
        },
        { name: "capability_gate", args: { tool_name: "get_marker_snapshot", concept_id: "hba1c_percent" }, result_summary: "accepted", ok: true, duration_ms: 0.1 },
        { name: "get_marker_snapshot", args: { concept_id: "hba1c_percent" }, result_summary: "HbA1c is 6.1%", ok: true, duration_ms: 2.5 },
        {
          name: "retrieve_knowledge",
          args: { query: "LDL HbA1c", topic_filter: ["lipids"] },
          result_summary: "3 chunks: ['kb_lipid_001']",
          ok: true,
          duration_ms: 5.0,
        },
        { name: "compose_answer", args: { narrator_backend: "bedrock" }, result_summary: "412 chars", ok: true, duration_ms: 1850.4 },
        { name: "verify_safety_checks", args: {}, result_summary: "passed", ok: true, duration_ms: 0.4 },
      ],
    });
  }

  it("renders a round-grouped step list instead of the flat tool-calls list", () => {
    render(<TraceView trace={v2Trace()} />);

    expect(screen.getByText("How this was computed (V2)")).toBeInTheDocument();
    expect(screen.queryByText(/^Tool calls \(/)).not.toBeInTheDocument();

    expect(screen.getByText("Round 1")).toBeInTheDocument();
    expect(screen.getByText("Round 2 (repair)")).toBeInTheDocument();
    expect(screen.getByText(/Repairing: Unknown tool 'made_up_tool'\./)).toBeInTheDocument();
  });

  it("shows each proposed call's gate verdict, and only accepted calls get an execution result", () => {
    render(<TraceView trace={v2Trace()} />);

    expect(screen.getByText("get_marker_trend")).toBeInTheDocument();
    expect(screen.getByText("LDL-C trending up")).toBeInTheDocument();

    expect(screen.getByText("made_up_tool")).toBeInTheDocument();
    expect(screen.getByText("Unknown tool 'made_up_tool'.")).toBeInTheDocument();

    expect(screen.getByText("get_marker_snapshot")).toBeInTheDocument();
    expect(screen.getByText("HbA1c is 6.1%")).toBeInTheDocument();
  });

  it("renders the trailing automatic-retrieval step separately from the rounds", () => {
    render(<TraceView trace={v2Trace()} />);

    expect(screen.getByText("Automatic knowledge retrieval")).toBeInTheDocument();
    expect(screen.getByText("3 chunks: ['kb_lipid_001']")).toBeInTheDocument();
  });

  it("formats durations under and over one second differently", () => {
    render(<TraceView trace={v2Trace()} />);

    expect(screen.getByText("420ms")).toBeInTheDocument();
    expect(screen.getByText("5ms")).toBeInTheDocument();
  });

  it("renders a Setup section for the pre-planning steps, above Round 1", () => {
    render(<TraceView trace={v2Trace()} />);

    expect(screen.getByText("Classify intent")).toBeInTheDocument();
    expect(screen.getByText("Load user profile")).toBeInTheDocument();
    expect(screen.getByText("Load bloodwork")).toBeInTheDocument();
    expect(screen.getByText("display_name='Alex'")).toBeInTheDocument();
  });

  it("renders the composition/verification steps in the trailing section, after retrieval", () => {
    render(<TraceView trace={v2Trace()} />);

    expect(screen.getByText("Compose answer")).toBeInTheDocument();
    expect(screen.getByText("412 chars")).toBeInTheDocument();
    expect(screen.getByText("1.9s")).toBeInTheDocument();
    expect(screen.getByText("Verify safety checks")).toBeInTheDocument();
    expect(screen.getByText("passed")).toBeInTheDocument();
  });

  it("shows the ground-truth total latency next to the section header", () => {
    render(<TraceView trace={v2Trace()} />);
    expect(screen.getByText("Total: 3.8s")).toBeInTheDocument();
  });

  it("omits the total-latency chip when total_duration_ms is absent (an older trace)", () => {
    const trace = v2Trace();
    delete trace.total_duration_ms;
    render(<TraceView trace={trace} />);
    expect(screen.queryByText(/^Total:/)).not.toBeInTheDocument();
  });
});

describe("TraceView cross-turn evidence (prior_evidence)", () => {
  it("renders prior_evidence in its own section, separate from this turn's grounded facts", () => {
    const trace = baseTrace({
      grounded_facts: [{ claim: "LDL-C trend: 148 -> 162 mg/dL", source_type: "bloodwork", source_ref: "trend:ldl_c_mg_dl", numeric_values: [162, 148], unit: "mg/dL" }],
      prior_evidence: [
        { claim: "HDL-C = 47 mg/dL (adequate)", source_type: "bloodwork", source_ref: "panel:hdl_c_mg_dl", numeric_values: [47], unit: "mg/dL", source_run_id: "run-1" },
      ],
    });
    render(<TraceView trace={trace} />);

    expect(screen.getByText("Grounded facts (1)")).toBeInTheDocument();
    expect(screen.getByText("Carried over from earlier turns (1)")).toBeInTheDocument();
    expect(screen.getByText("HDL-C = 47 mg/dL (adequate)")).toBeInTheDocument();
    expect(screen.getByText(/from run run-1/)).toBeInTheDocument();
  });

  it("omits the carried-over section entirely when prior_evidence is empty or absent", () => {
    render(<TraceView trace={baseTrace()} />);
    expect(screen.queryByText(/^Carried over from earlier turns/)).not.toBeInTheDocument();
  });
});
