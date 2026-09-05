import { useState } from "react";
import { config } from "../config";
import { askQuestion, ApiError, type AskResponse } from "../api";
import { TraceView } from "./TraceView";

export function AskForm() {
  const [userId, setUserId] = useState(config.demoUserId);
  const [question, setQuestion] = useState("What should I focus on first in my results?");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AskResponse | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await askQuestion(userId, question));
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <form onSubmit={handleSubmit} className="ask-form">
        <label>
          User ID
          <input value={userId} onChange={(e) => setUserId(e.target.value)} />
        </label>
        <label>
          Question
          <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} />
        </label>
        <button type="submit" disabled={loading}>
          {loading ? "Asking..." : "Ask"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {result && (
        <div className="result">
          <div className={`safe-badge ${result.safe ? "safe" : "unsafe"}`}>{result.safe ? "SAFE" : "UNSAFE -- rejected"}</div>
          <p className="answer">{result.answer}</p>
          <p className="meta">
            run_id: <code>{result.run_id}</code> &middot; narrator: <code>{result.trace.narrator_backend}</code> &middot; intent:{" "}
            <code>{result.trace.intent}</code>
          </p>
          <TraceView trace={result.trace} />
        </div>
      )}
    </div>
  );
}
