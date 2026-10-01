import { loadConversations, type Conversation } from "../history";

/** `version` is bumped by the parent after every new submission purely to
 * force this component to re-render (it's read in the dependency-less
 * render body below, not stored) -- simpler than lifting the whole
 * history array into shared state for what's otherwise a read-mostly,
 * per-viewer list backed by localStorage, not React state.
 *
 * Lives in the Workbench's left-hand control rail, which already has its
 * own persistent section labels, so this renders a plain always-visible
 * list rather than a collapsible `<details>` -- there's no reason to make
 * "see your recent conversations" a second click in a rail whose whole
 * point is to be glanceable. `disabled` mirrors every other rail control:
 * a run is being polled right now, and this app only ever tracks one poll
 * loop at a time (see `Workbench.tsx`), so opening a different
 * conversation mid-poll isn't supported.
 *
 * Grouped by conversation, not a flat list of individual runs -- found
 * live: the user wanted the same "click a past thread, see the whole
 * thing again" pattern ChatGPT/Claude Code's own sidebars use, not a
 * list of isolated answers with no sense of which ones were actually one
 * back-and-forth. */
export function RunHistory({
  version: _version,
  onSelect,
  disabled,
  activeConversationId,
}: {
  version: number;
  onSelect: (conversation: Conversation) => void;
  disabled: boolean;
  activeConversationId: string | null;
}) {
  const conversations = loadConversations();

  if (conversations.length === 0) return <p className="rail-history-empty">No conversations yet this session.</p>;

  return (
    <ul className="rail-history">
      {conversations.map((conversation) => {
        const latestEngine = conversation.entries[conversation.entries.length - 1]?.engine;
        const turnCount = conversation.entries.length;
        return (
          <li key={conversation.id}>
            <button
              type="button"
              className={`history-row ${conversation.id === activeConversationId ? "active" : ""}`}
              onClick={() => onSelect(conversation)}
              disabled={disabled}
            >
              <span className="h-q">{conversation.title}</span>
              <span className="h-meta">
                {latestEngine && <span className={`engine-dot ${latestEngine} small`} />}
                {turnCount} turn{turnCount === 1 ? "" : "s"} &middot; {new Date(conversation.lastActiveAt).toLocaleTimeString()}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
