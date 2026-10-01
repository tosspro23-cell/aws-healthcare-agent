import { useEffect, useRef } from "react";
import type { Turn } from "./Workbench";
import { Markdown } from "./Markdown";

const EXAMPLE_QUESTIONS = [
  "What should I focus on first in my results?",
  "How is my LDL trending?",
  "Compare my LDL and A1C trends and tell me if my reported diet change is helping.",
];

const MODE_TAG: Record<Turn["mode"], string> = { sync: "sync", step_functions: "step functions", queue: "queue" };

function TurnCard({ turn, selected, onSelect }: { turn: Turn; selected: boolean; onSelect: () => void }) {
  return (
    <div className={`turn ${selected ? "selected" : ""}`}>
      <div className="turn-question">{turn.question}</div>
      <button type="button" className="turn-answer-card" onClick={onSelect}>
        <div className="turn-badges">
          {turn.status === "pending" && <span className="badge-pill pending">Working&hellip;</span>}
          {turn.status === "failed" && <span className="badge-pill unsafe">Failed</span>}
          {turn.status === "cancelled" && <span className="badge-pill neutral">Cancelled</span>}
          {turn.status === "succeeded" && typeof turn.safe === "boolean" && (
            <span className={`badge-pill ${turn.safe ? "safe" : "unsafe"}`}>
              {turn.safe ? (
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              ) : null}
              {turn.safe ? "Safe" : "Unsafe"}
            </span>
          )}
          <span className={`badge-pill engine-${turn.engine}`}>
            <span className="dot" />
            {turn.engine.toUpperCase()}
          </span>
          <span className="badge-pill persona">{turn.persona}</span>
          <span className="badge-pill mode">{MODE_TAG[turn.mode]}</span>
        </div>
        {turn.status === "pending" && <p className="meta">{turn.currentStage ?? "Working…"}</p>}
        {turn.status === "failed" && <p className="error">{turn.errorMessage ?? "This run failed."}</p>}
        {turn.status === "cancelled" && <p className="meta">This run was cancelled.</p>}
        {turn.status === "succeeded" && turn.answer && <Markdown text={turn.answer} />}
        <div className="turn-hint">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v4l3 2" />
          </svg>
          {selected ? "Showing this answer's evidence" : "View evidence"}
        </div>
      </button>
    </div>
  );
}

/** The Workbench's center region: a running conversation thread (each
 * `Ask` appends a turn rather than replacing the previous one -- see
 * `Workbench.tsx`'s own note on what "multi-turn" does and doesn't mean
 * here yet) plus the composer. Selecting any turn's answer card is how
 * the right-hand evidence panel knows what to show. */
export function ConversationPanel({
  turns,
  selectedTurnId,
  onSelectTurn,
  question,
  setQuestion,
  onSubmit,
  disabled,
  pendingTurn,
  onCancel,
  cancelling,
  error,
}: {
  turns: Turn[];
  selectedTurnId: string | null;
  onSelectTurn: (id: string) => void;
  question: string;
  setQuestion: (q: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  pendingTurn: Turn | undefined;
  onCancel: () => void;
  cancelling: boolean;
  error: string | null;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Keep the newest turn in view as the thread grows -- a chat-shaped
  // panel that silently left you scrolled up after every answer would
  // defeat the point of a running conversation.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns.length]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && question.trim()) onSubmit();
    }
  }

  return (
    <section className="conversation-col">
      <div className="conv-scroll" ref={scrollRef}>
        {turns.length === 0 && (
          <p className="conv-empty meta">Ask a question below, or try one of the examples, to get started.</p>
        )}
        {turns.map((turn) => (
          <TurnCard key={turn.id} turn={turn} selected={turn.id === selectedTurnId} onSelect={() => onSelectTurn(turn.id)} />
        ))}
      </div>

      {error && <p className="error composer-error">{error}</p>}

      {pendingTurn && pendingTurn.mode !== "sync" && (
        <div className="composer-cancel-row">
          <button type="button" className="cancel-button" onClick={onCancel} disabled={cancelling}>
            {cancelling ? "Cancelling…" : "Cancel this run"}
          </button>
        </div>
      )}

      <div className="example-row">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button type="button" key={q} className="example-chip" onClick={() => setQuestion(q)} disabled={disabled}>
            {q}
          </button>
        ))}
      </div>

      <div className="composer">
        <div className="composer-inner">
          <textarea
            rows={1}
            placeholder="Ask a question about your results…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
          />
          <button type="button" className="send-btn" onClick={onSubmit} disabled={disabled || !question.trim()}>
            {disabled ? "Working…" : "Ask"}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
              <path d="M5 12h14M13 5l7 7-7 7" />
            </svg>
          </button>
        </div>
      </div>
    </section>
  );
}
