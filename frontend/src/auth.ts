/**
 * Authorization Code + PKCE flow against the deployed Cognito Hosted UI --
 * the browser-native equivalent of what infra/scripts/get_dev_token.py does
 * with a local Python HTTP server standing in for a real redirect. This is
 * the actual point of Phase 6 per docs/AWS_ROADMAP.md: run this flow through
 * an in-browser redirect, not a script pretending to be one.
 */
import { config } from "./config";

const CODE_VERIFIER_KEY = "care_agent_pkce_code_verifier";
const ACCESS_TOKEN_KEY = "care_agent_access_token";

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

export function getAccessToken(): string | null {
  return sessionStorage.getItem(ACCESS_TOKEN_KEY);
}

export function signOut(): void {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  sessionStorage.removeItem(CODE_VERIFIER_KEY);
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
  const tokens = (await response.json()) as { access_token: string };
  sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  sessionStorage.removeItem(CODE_VERIFIER_KEY);
  window.history.replaceState({}, "", "/");
}
