import { useEffect, useRef, useState } from "react";
import { beginSignIn, completeSignIn, getAccessToken, signOut } from "./auth";
import { AskForm } from "./components/AskForm";

type AuthState = "checking" | "signed-out" | "signed-in" | "error";

export function App() {
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [authError, setAuthError] = useState<string | null>(null);
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
      <AskForm />
    </main>
  );
}
