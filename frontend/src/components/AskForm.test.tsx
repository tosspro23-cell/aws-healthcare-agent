/**
 * Regression coverage for the polling generation counter in
 * AskForm.tsx -- a second independent review found the original
 * `setInterval(async () => ...)` polling loop didn't wait for the
 * previous tick's request to resolve before firing the next one, so a
 * slower, earlier response could resolve after a faster, later one and
 * overwrite already-current state with stale data (see
 * docs/DECISIONS.md, 2026-09-05). The fix (a self-scheduling setTimeout
 * plus a monotonic generation counter) was verified live in a browser at
 * the time, but had no automated test; this pins down the actual
 * supersession scenario the counter exists for. Note it's *not*
 * reachable by re-submitting the form -- the submit button is correctly
 * disabled while a run is pending -- so this reproduces it the way the
 * real code does: selecting a *different* run from history while the
 * current one's poll still has a request in flight.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AskForm } from "./AskForm";
import * as api from "../api";
import { addHistoryEntry } from "../history";
import type { RunRecord } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    askQuestion: vi.fn(),
    startRun: vi.fn(),
    enqueueJob: vi.fn(),
    getRun: vi.fn(),
    cancelRun: vi.fn(),
  };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function runRecord(overrides: Partial<RunRecord> & { run_id: string }): RunRecord {
  return {
    status: "RUNNING",
    execution_type: "STEP_FUNCTIONS",
    user_id: "user_demo_001",
    question: "What should I focus on first in my results?",
    ...overrides,
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("AskForm polling supersession", () => {
  it("never lets a superseded poll's late-arriving response overwrite a newer run's state", async () => {
    // A second, unrelated past run already in history -- selecting it
    // while run-a is still polling is what actually triggers
    // supersession (the submit button itself is disabled while pending).
    addHistoryEntry({ run_id: "run-b", question: "a second question", execution_type: "STEP_FUNCTIONS", submitted_at: "2026-01-01T00:00:00Z" });

    // Each run_id gets its own queue of deferreds -- both
    // handleSelectHistoryEntry's own direct getRun call *and* the poll
    // tick it schedules afterward call getRun("run-b") in turn.
    const getRunDeferreds: Record<string, ReturnType<typeof deferred<RunRecord>>[]> = {};
    vi.mocked(api.getRun).mockImplementation((runId: string) => {
      const d = deferred<RunRecord>();
      (getRunDeferreds[runId] ??= []).push(d);
      return d.promise;
    });
    vi.mocked(api.startRun).mockResolvedValueOnce({ run_id: "run-a", status: "RUNNING" });

    const { container } = render(<AskForm />);
    const submitButton = () => container.querySelector('button[type="submit"]') as HTMLButtonElement;

    fireEvent.click(screen.getByRole("button", { name: "Start run (Step Functions)" }));
    fireEvent.click(submitButton());
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("run-a")).toBeInTheDocument();

    // The poll tick fires and calls getRun("run-a"); hold it pending.
    await vi.advanceTimersByTimeAsync(1000);
    expect(getRunDeferreds["run-a"]).toHaveLength(1);

    // Before run-a's poll resolves, the user selects a *different* run
    // from history -- this is the exact supersession the generation
    // counter guards (stopPolling() bumps the generation before the
    // newly-selected run's own fetch/poll starts).
    fireEvent.click(screen.getByText("a second question"));
    await vi.advanceTimersByTimeAsync(0);
    expect(getRunDeferreds["run-b"]).toHaveLength(1);

    // handleSelectHistoryEntry's own direct getRun call resolves,
    // non-terminal, so it schedules its own poll tick.
    getRunDeferreds["run-b"][0].resolve(runRecord({ run_id: "run-b", status: "RUNNING" }));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("run-b")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(1000);
    expect(getRunDeferreds["run-b"]).toHaveLength(2);

    // run-b's poll tick resolves, reaching a terminal state.
    getRunDeferreds["run-b"][1].resolve(runRecord({ run_id: "run-b", status: "SUCCEEDED", answer: "b's real answer", safe: true }));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("b's real answer")).toBeInTheDocument();

    // The superseded run-a poll finally resolves -- must not overwrite
    // the now-current run-b state.
    getRunDeferreds["run-a"][0].resolve(runRecord({ run_id: "run-a", status: "RUNNING" }));
    await vi.advanceTimersByTimeAsync(0);

    expect(screen.getByText("b's real answer")).toBeInTheDocument();
    expect(screen.queryByText("run-a")).not.toBeInTheDocument();
  });
});
