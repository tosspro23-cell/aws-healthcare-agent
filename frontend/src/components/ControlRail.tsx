import { RunHistory } from "./RunHistory";
import type { Conversation } from "../history";

/** The Workbench's left-hand rail: now just this browser's conversation
 * history -- Engine/Persona/Mode moved out to `ControlBar` (the new top
 * bar) on the user's own suggestion, modeled on a reference console that
 * keeps request-shaping controls along the top and leaves the side panel
 * free for content. Freeing this rail is deliberate groundwork for a
 * later, separately-scoped "show the patient's own source data" panel
 * (bloodwork, questionnaire) -- not built yet. */
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
  return (
    <aside className="rail">
      <div className="rail-history-wrap">
        <div className="rail-section-label">Conversations</div>
        <RunHistory version={historyVersion} onSelect={onSelectConversation} disabled={disabled} activeConversationId={activeConversationId} />
      </div>
    </aside>
  );
}
