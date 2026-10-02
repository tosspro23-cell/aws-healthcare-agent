import { useState } from "react";
import { RunHistory } from "./RunHistory";
import { PatientDataPanel } from "./PatientDataPanel";
import type { Conversation } from "../history";

type RailTab = "conversations" | "patient-data";

/** The Workbench's left-hand rail: two tabs -- this browser's conversation
 * history (unchanged from before), and the patient source-data viewer
 * (`PatientDataPanel`) the user asked for as the next step after
 * Engine/Persona/Mode moved out to `ControlBar`. Switching tabs is a
 * plain conditional mount, not a CSS-hidden dual-mount: `PatientDataPanel`
 * re-fetches on every mount, which is fine for one cheap Lambda call
 * reading small local JSON files (not Bedrock-billed, not a DynamoDB
 * read) -- simpler than adding a caching layer for data that's static for
 * the whole demo session. The tab buttons are never `disabled`; switching
 * which view the rail shows has no side effect on any in-flight run,
 * unlike a `RunHistory` row (which loads a *different* conversation).
 *
 * "New conversation" lives here too, as a compact icon button beside the
 * tab row -- moved from a full-width dashed row of its own (looked out of
 * place next to everything else in this rail) to match where ChatGPT/
 * Claude Code put the same affordance: a small action right at the top of
 * the history list, not a standalone button. Same wiring as before
 * (`onNewConversation`/`disabled`/`hasActiveConversation`), just relocated
 * and restyled -- it acts on the conversation list immediately below it. */
export function ControlRail({
  disabled,
  historyVersion,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  hasActiveConversation,
}: {
  disabled: boolean;
  historyVersion: number;
  activeConversationId: string | null;
  onSelectConversation: (conversation: Conversation) => void;
  onNewConversation: () => void;
  hasActiveConversation: boolean;
}) {
  const [tab, setTab] = useState<RailTab>("conversations");

  return (
    <aside className="rail">
      <div className="rail-tab-row">
        <div className="segmented" role="tablist" aria-label="Rail view">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "conversations"}
            className={`segmented-option ${tab === "conversations" ? "active" : ""}`}
            onClick={() => setTab("conversations")}
          >
            Conversations
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "patient-data"}
            className={`segmented-option ${tab === "patient-data" ? "active" : ""}`}
            onClick={() => setTab("patient-data")}
          >
            Patient Data
          </button>
        </div>
        <button
          type="button"
          className="new-conversation-btn"
          onClick={onNewConversation}
          disabled={disabled || !hasActiveConversation}
          title="New conversation"
          aria-label="New conversation"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </div>

      {tab === "conversations" && (
        <div className="rail-history-wrap">
          <div className="rail-section-label">Conversations</div>
          <RunHistory version={historyVersion} onSelect={onSelectConversation} disabled={disabled} activeConversationId={activeConversationId} />
        </div>
      )}

      {tab === "patient-data" && <PatientDataPanel />}
    </aside>
  );
}
