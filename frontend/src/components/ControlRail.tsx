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
 * unlike a `RunHistory` row (which loads a *different* conversation). */
export function ControlRail({
  disabled,
  historyVersion,
  activeConversationId,
  onSelectConversation,
}: {
  disabled: boolean;
  historyVersion: number;
  activeConversationId: string | null;
  onSelectConversation: (conversation: Conversation) => void;
}) {
  const [tab, setTab] = useState<RailTab>("conversations");

  return (
    <aside className="rail">
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
