import { useEffect, useRef, useState } from "react";
import { beginSignIn, completeSignIn, getAccessToken, signOut } from "./auth";
import { AskForm } from "./components/AskForm";
import { CompoundDemo } from "./components/CompoundDemo";

type AuthState = "checking" | "signed-out" | "signed-in" | "error";
type View = "workbench" | "local-stream-demo";

// CompoundDemo talks only to `scripts/dev_server.py` on localhost:8000 (see
// that component's own docstring) -- it is unauthenticated local dev
// tooling, not a production feature. Surfacing it as a top-level tab on the
// real deployed site let a real user click it and hit a raw connection
// error ("Could not reach the local dev server..."), which read as a
// broken product feature rather than what it actually is. Gating it on the
// page's own hostname means it simply cannot render anywhere it can't
// work; the real, deployed way to exercise V2 is the Engine selector
// inside AskForm's "Ask" mode, which goes through the actual production API.
const IS_LOCAL_DEV = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";

export function App() {
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [authError, setAuthError] = useState<string | null>(null);
  const [view, setView] = useState<View>("workbench");
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

  if (authState === "checking") {
    return (
      <div className="app-shell">
        <TopBar />
        <main>
          <p className="meta">Loading...</p>
        </main>
      </div>
    );
  }

  if (authState === "error") {
    return (
      <div className="app-shell">
        <TopBar />
        <main>
          <p className="error">Sign-in failed: {authError}</p>
          <button onClick={() => void beginSignIn()}>Try again</button>
        </main>
      </div>
    );
  }

  if (authState === "signed-out") {
    return (
      <div className="app-shell">
        <TopBar />
        <main>
          <div className="card signin-card">
            <p>Sign in to ask the health Q&amp;A agent a question against the deployed AWS backend.</p>
            <button onClick={() => void beginSignIn()}>Sign in</button>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <TopBar signedIn onSignOut={signOut} />
      <main>
        {IS_LOCAL_DEV && (
          <div className="view-toggle" role="tablist" aria-label="View">
            <button
              type="button"
              role="tab"
              aria-selected={view === "workbench"}
              className={`view-tab ${view === "workbench" ? "active" : ""}`}
              onClick={() => setView("workbench")}
            >
              Workbench
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "local-stream-demo"}
              className={`view-tab ${view === "local-stream-demo" ? "active" : ""}`}
              onClick={() => setView("local-stream-demo")}
            >
              Local streaming demo (dev only)
            </button>
          </div>
        )}

        {view === "workbench" || !IS_LOCAL_DEV ? <AskForm /> : <CompoundDemo />}
      </main>
    </div>
  );
}

function TopBar({ signedIn, onSignOut }: { signedIn?: boolean; onSignOut?: () => void }) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            CA
          </span>
          <div className="brand-text">
            <span className="brand-name">Care Agent</span>
            <span className="brand-tag">Workbench</span>
          </div>
        </div>
        {signedIn && (
          <button className="topbar-signout" onClick={onSignOut}>
            Sign out
          </button>
        )}
      </div>
    </header>
  );
}
