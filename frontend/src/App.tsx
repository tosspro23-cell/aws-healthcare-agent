import { useEffect, useRef, useState } from "react";
import { beginSignIn, completeSignIn, getAccessToken, signOut } from "./auth";
import { AskForm } from "./components/AskForm";
import { CompoundDemo } from "./components/CompoundDemo";

type AuthState = "checking" | "signed-out" | "signed-in" | "error";
type Engine = "v1" | "v2";

const ENGINE_LABELS: Record<Engine, string> = {
  v1: "V1 -- heuristic + semantic planner (deployed AWS)",
  v2: "V2 -- tool-calling agent (local demo)",
};

export function App() {
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [authError, setAuthError] = useState<string | null>(null);
  const [engine, setEngine] = useState<Engine>("v1");
  // React 18 StrictMode deliberately double-invokes effects in dev to
  // surface exactly this class of bug: the callback's authorization code
  // is single-use, so a naive `useEffect(() => { init() }, [])` exchanges
  // it twice, and the second exchange fails with a real 400 from Cognito
  // (observed live during this project's own first test of the flow).
  // This ref makes the actual exchange run at most once per mount.
  const initStarted = useRef(false);

  useEffect(() => {
    if (initStarted.current) return;
    initStarted.current = true;

    async function init() {
      if (window.location.pathname === "/callback") {
        const code = new URLSearchParams(window.location.search).get("code");
        const oauthError = new URLSearchParams(window.location.search).get("error_description");
        if (oauthError) {
          setAuthError(oauthError);
          setAuthState("error");
          return;
        }
        if (!code) {
          setAuthError("No authorization code in the callback URL.");
          setAuthState("error");
          return;
        }
        try {
          await completeSignIn(code);
          setAuthState("signed-in");
        } catch (err) {
          setAuthError(String(err));
          setAuthState("error");
        }
        return;
      }
      setAuthState(getAccessToken() ? "signed-in" : "signed-out");
    }
    void init();
  }, []);

  if (authState === "checking") return <p>Loading...</p>;

  if (authState === "error") {
    return (
      <main>
        <h1>Care Agent Workbench</h1>
        <p className="error">Sign-in failed: {authError}</p>
        <button onClick={() => void beginSignIn()}>Try again</button>
      </main>
    );
  }

  if (authState === "signed-out") {
    return (
      <main>
        <h1>Care Agent Workbench</h1>
        <p>Sign in to ask the health Q&A agent a question against the deployed AWS backend.</p>
        <button onClick={() => void beginSignIn()}>Sign in</button>
      </main>
    );
  }

  return (
    <main>
      <header className="app-header">
        <h1>Care Agent Workbench</h1>
        <button onClick={signOut}>Sign out</button>
      </header>

      <div className="engine-toggle" role="tablist" aria-label="Engine">
        {(Object.keys(ENGINE_LABELS) as Engine[]).map((e) => (
          <button
            type="button"
            key={e}
            role="tab"
            aria-selected={engine === e}
            className={`engine-tab ${engine === e ? "active" : ""}`}
            onClick={() => setEngine(e)}
          >
            {ENGINE_LABELS[e]}
          </button>
        ))}
      </div>

      {engine === "v1" ? <AskForm /> : <CompoundDemo />}
    </main>
  );
}
