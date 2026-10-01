/**
 * Regression coverage for the polling generation counter, ported from
 * AskForm.tsx's own version of this test (see docs/DECISIONS.md) to
 * this component's per-turn architecture. The original scenario
 * ("select a different run from history while the current one's poll
 * still has a request in flight") is no longer reachable here -- every
 * control that could start a second concurrent turn, history included,
 * is disabled while one is pending (see Workbench.tsx's own comment on
 * why this app only ever tracks one poll loop at a time). The
 * still-reachable, still-real version of the same race is cancelling a
 * run while its own poll has a request in flight: the stale, now-
 * superseded poll response must not overwrite the cancellation's own
 * fresh result when it finally resolves.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Workbench } from "./Workbench";
import * as api from "../api";
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

describe("Workbench polling supersession", () => {
  it("never lets a superseded poll response overwrite a cancellation's own fresh result", async () => {
    const getRunDeferreds: ReturnType<typeof deferred<RunRecord>>[] = [];
    vi.mocked(api.getRun).mockImplementation(() => {
      const d = deferred<RunRecord>();
      getRunDeferreds.push(d);
      return d.promise;
    });
    vi.mocked(api.startRun).mockResolvedValueOnce({ run_id: "run-a", status: "RUNNING" });
    vi.mocked(api.cancelRun).mockResolvedValueOnce({ run_id: "run-a", status: "CANCELLED" });

    render(<Workbench />);

    fireEvent.click(screen.getByRole("button", { name: /Step Fns/ }));
    fireEvent.change(screen.getByPlaceholderText(/Ask a question/), { target: { value: "What should I focus on first in my results?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await vi.advanceTimersByTimeAsync(0);

    // The poll tick fires and calls getRun; hold it pending.
    await vi.advanceTimersByTimeAsync(1000);
    expect(getRunDeferreds).toHaveLength(1);

    // Cancel while that poll request is still in flight -- this is the
    // real, still-reachable version of the supersession race: stopPolling()
    // bumps the generation counter before the cancel's own getRun call.
    fireEvent.click(screen.getByRole("button", { name: /Cancel this run/ }));
    await vi.advanceTimersByTimeAsync(0);
    expect(getRunDeferreds).toHaveLength(2);

    // The cancellation's own fresh fetch resolves first.
    getRunDeferreds[1].resolve(runRecord({ run_id: "run-a", status: "CANCELLED" }));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("Cancelled")).toBeInTheDocument();

    // The original, now-superseded poll tick finally resolves -- must not
    // overwrite the cancellation back to a running/pending state.
    getRunDeferreds[0].resolve(runRecord({ run_id: "run-a", status: "RUNNING" }));
    await vi.advanceTimersByTimeAsync(0);

    expect(screen.getByText("Cancelled")).toBeInTheDocument();
    expect(screen.queryByText("Working…")).not.toBeInTheDocument();
  });
});
