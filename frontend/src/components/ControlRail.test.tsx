/** Regression guard for the rail's new tab switcher (see ControlRail.tsx):
 * adding the "Patient Data" tab must not break "Conversations" -- clicking
 * a past conversation still has to fire `onSelectConversation` exactly as
 * before. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ControlRail } from "./ControlRail";
import * as api from "../api";
import type { Conversation } from "../history";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, getPatientData: vi.fn() };
});

vi.mock("../history", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../history")>();
  return {
    ...actual,
    loadConversations: vi.fn(() => [
      { id: "c1", title: "How is my LDL trending?", startedAt: "", lastActiveAt: "2026-01-01T00:00:00Z", entries: [{ run_id: "r1", question: "How is my LDL trending?", execution_type: "SYNC", submitted_at: "2026-01-01T00:00:00Z" }] },
    ] as Conversation[]),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ControlRail tab switcher", () => {
  it("switches between Conversations and Patient Data, and the conversation-click flow still fires", async () => {
    vi.mocked(api.getPatientData).mockResolvedValue({
      user_id: "user_demo_001",
      profile: { user_id: "user_demo_001", display_name: "Alex", age: 42, sex: "female", country: "PT", height_cm: null, weight_kg: null, known_conditions: [], medications: [], allergies: [] },
      bloodwork: { user_id: "user_demo_001", latest_panel: null, previous_panels: [] },
      questionnaire: { user_id: "user_demo_001", completed_at: null, facts: [], cautions: [], style_hint: null },
    });

    const onSelectConversation = vi.fn();
    render(
      <ControlRail
        disabled={false}
        historyVersion={0}
        activeConversationId={null}
        onSelectConversation={onSelectConversation}
        onNewConversation={vi.fn()}
        hasActiveConversation={true}
      />,
    );

    expect(screen.getByText("How is my LDL trending?")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Patient Data" }));
    expect(screen.queryByText("How is my LDL trending?")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Alex/)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("tab", { name: "Conversations" }));
    expect(screen.queryByText(/Alex/)).not.toBeInTheDocument();
    const conversationRow = screen.getByText("How is my LDL trending?");
    expect(conversationRow).toBeInTheDocument();

    fireEvent.click(conversationRow);
    expect(onSelectConversation).toHaveBeenCalledTimes(1);
  });

  it("renders a compact 'New conversation' button beside the tab row, wired the same as before relocation", () => {
    const onNewConversation = vi.fn();
    render(
      <ControlRail
        disabled={false}
        historyVersion={0}
        activeConversationId={null}
        onSelectConversation={vi.fn()}
        onNewConversation={onNewConversation}
        hasActiveConversation={true}
      />,
    );

    const button = screen.getByRole("button", { name: "New conversation" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(onNewConversation).toHaveBeenCalledTimes(1);
  });

  it("disables 'New conversation' when there is no active conversation to replace", () => {
    render(
      <ControlRail
        disabled={false}
        historyVersion={0}
        activeConversationId={null}
        onSelectConversation={vi.fn()}
        onNewConversation={vi.fn()}
        hasActiveConversation={false}
      />,
    );

    expect(screen.getByRole("button", { name: "New conversation" })).toBeDisabled();
  });
});
