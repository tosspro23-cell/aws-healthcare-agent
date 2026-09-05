/**
 * Authorization Code + PKCE flow against the deployed Cognito Hosted UI --
 * the browser-native equivalent of what infra/scripts/get_dev_token.py does
 * with a local Python HTTP server standing in for a real redirect. This is
 * the actual point of Phase 6 per docs/AWS_ROADMAP.md: run this flow through
 * an in-browser redirect, not a script pretending to be one.
 */
import { config } from "./config";
import { clearHistory } from "./history";

const CODE_VERIFIER_KEY = "care_agent_pkce_code_verifier";
const ACCESS_TOKEN_KEY = "care_agent_access_token";
const EXPIRES_AT_KEY = "care_agent_access_token_expires_at";

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function makePkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifierBytes = crypto.getRandomValues(new Uint8Array(64));
  const verifier = base64UrlEncode(verifierBytes);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  const challenge = base64UrlEncode(new Uint8Array(digest));
  return { verifier, challenge };
}

/** Any stored token used to be treated as a valid session regardless of
 * age -- a second independent review found that an expired token just
 * kept failing every API call with a 401 the app never noticed, leaving
 * the signed-in Workbench displayed indefinitely. Expiry is recorded
 * alongside the token at exchange time (see `completeSignIn`) and
 * checked here; an expired token is treated the same as no token,
 * clearing itself out rather than leaving stale state around. */
export function getAccessToken(): string | null {
  const token = sessionStorage.getItem(ACCESS_TOKEN_KEY);
  if (!token) return null;
  const expiresAt = Number(sessionStorage.getItem(EXPIRES_AT_KEY));
  if (expiresAt && Date.now() >= expiresAt) {
    clearSession();
    return null;
  }
  return token;
}

function clearSession(): void {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  sessionStorage.removeItem(EXPIRES_AT_KEY);
  sessionStorage.removeItem(CODE_VERIFIER_KEY);
}

/** Called by `api.ts` when a request comes back 401 -- covers the case
 * where the token is revoked or otherwise rejected server-side before
 * its own recorded expiry (`getAccessToken`'s check alone doesn't catch
 * that). Clears the session and reloads to "/", the simplest reliable
 * way to force the app back to its signed-out state from anywhere
 * (`AskForm`'s polling loop included) without threading a callback
 * through every component that can make an API call. */
export function handleSessionExpired(): void {
  clearSession();
  window.location.href = "/";
}

export function signOut(): void {
  clearSession();
  // Run history (localStorage, not sessionStorage) persisted across
  // sign-out until this fix -- a second independent review found that
  // full question text (not just run_ids) was readable by whoever signs
  // into the same browser next. This is convenience state for the
  // current session's user, not something that should outlive them.
  clearHistory();
  const params = new URLSearchParams({ client_id: config.appClientId, logout_uri: config.logoutUri });
  window.location.href = `https://${config.cognitoDomain}/logout?${params.toString()}`;
}

export async function beginSignIn(): Promise<void> {
  const { verifier, challenge } = await makePkcePair();
  // Survives the full-page redirect to Cognito and back -- sessionStorage
  // (not a plain variable) is required for exactly that reason.
  sessionStorage.setItem(CODE_VERIFIER_KEY, verifier);
  const params = new URLSearchParams({
    response_type: "code",
    client_id: config.appClientId,
    redirect_uri: config.redirectUri,
    scope: "openid email profile",
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  window.location.href = `https://${config.cognitoDomain}/oauth2/authorize?${params.toString()}`;
}

/** Call this on the /callback route. Exchanges the code for tokens, then
 * scrubs the URL back to "/" so a page refresh doesn't try to reuse a
 * one-time authorization code. */
export async function completeSignIn(code: string): Promise<void> {
  const verifier = sessionStorage.getItem(CODE_VERIFIER_KEY);
  if (!verifier) {
    throw new Error("No PKCE code_verifier found -- the sign-in flow must be started from beginSignIn(), not this page directly.");
  }
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: config.appClientId,
    code,
    redirect_uri: config.redirectUri,
    code_verifier: verifier,
  });
  const response = await fetch(`https://${config.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!response.ok) {
    throw new Error(`Token exchange failed: ${response.status} ${await response.text()}`);
  }
  const tokens = (await response.json()) as { access_token: string; expires_in?: number };
  sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  // Cognito's default access-token lifetime is 1 hour; `expires_in` (in
  // the token response, seconds) is the authoritative value regardless.
  // Recorded so `getAccessToken` can detect an expired token itself,
  // rather than only finding out via a failed API call.
  if (tokens.expires_in) {
    sessionStorage.setItem(EXPIRES_AT_KEY, String(Date.now() + tokens.expires_in * 1000));
  }
  sessionStorage.removeItem(CODE_VERIFIER_KEY);
  window.history.replaceState({}, "", "/");
}
