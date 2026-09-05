function requireEnv(name: string): string {
  const value = import.meta.env[name];
  if (!value) {
    throw new Error(`Missing ${name} -- copy frontend/.env.example to frontend/.env.local and fill in the deployed stack outputs.`);
  }
  return value;
}

export const config = {
  cognitoDomain: requireEnv("VITE_COGNITO_DOMAIN"),
  appClientId: requireEnv("VITE_COGNITO_APP_CLIENT_ID"),
  apiBaseUrl: requireEnv("VITE_API_BASE_URL"),
  // Derived from wherever this page is actually being served from, so the
  // same build works unmodified both locally (http://localhost:8765) and
  // once hosted (the CloudFront URL) -- either way, it must exactly match
  // a callback/logout URL registered on the Cognito App Client
  // (infra/stacks/auth_stack.py's `callback_urls`/`logout_urls`), since
  // Cognito rejects any redirect_uri it doesn't recognize verbatim.
  redirectUri: `${window.location.origin}/callback`,
  logoutUri: `${window.location.origin}/logout`,
  // Fixed to the one user the shipped sample dataset actually has data for
  // (see data/sample_bloodwork.json) -- shown, not hidden, so it's clear
  // this is a synthetic-data demo, not a real multi-user login.
  demoUserId: "user_demo_001",
};
