/**
 * Regression coverage for auth.ts's session-expiry tracking -- a second
 * independent review found a stored access token was treated as valid
 * regardless of age, leaving the signed-in Workbench displayed
 * indefinitely once it expired (see docs/DECISIONS.md, 2026-09-05). This
 * is the first frontend test file in this project: the logic here has
 * already been the source of several real bugs found only by manual
 * live-browser verification, worth pinning down with fast, repeatable
 * tests rather than relying on that every time it's touched again.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAccessToken, handleSessionExpired, signOut } from "./auth";

const ACCESS_TOKEN_KEY = "care_agent_access_token";
const EXPIRES_AT_KEY = "care_agent_access_token_expires_at";
const CODE_VERIFIER_KEY = "care_agent_pkce_code_verifier";
const HISTORY_KEY = "care_agent_run_history";

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getAccessToken", () => {
  it("returns null when no token is stored", () => {
    expect(getAccessToken()).toBeNull();
  });

  it("returns the token when its recorded expiry is still in the future", () => {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, "real-token");
    sessionStorage.setItem(EXPIRES_AT_KEY, String(Date.now() + 60_000));
    expect(getAccessToken()).toBe("real-token");
  });

  it("returns the token when no expiry was ever recorded", () => {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, "no-expiry-token");
    expect(getAccessToken()).toBe("no-expiry-token");
  });

  it("returns null and clears the session once the recorded expiry has passed", () => {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, "stale-token");
    sessionStorage.setItem(EXPIRES_AT_KEY, String(Date.now() - 1000));
    sessionStorage.setItem(CODE_VERIFIER_KEY, "leftover-verifier");

    expect(getAccessToken()).toBeNull();
    expect(sessionStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
    expect(sessionStorage.getItem(EXPIRES_AT_KEY)).toBeNull();
    expect(sessionStorage.getItem(CODE_VERIFIER_KEY)).toBeNull();
  });
});

describe("handleSessionExpired", () => {
  it("clears the session and forces the app back to the signed-out root", () => {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, "real-token");
    sessionStorage.setItem(EXPIRES_AT_KEY, String(Date.now() + 60_000));
    const location = { href: "" };
    vi.stubGlobal("location", location);

    handleSessionExpired();

    expect(sessionStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
    expect(location.href).toBe("/");
  });
});

describe("signOut", () => {
  it("clears the session and run history, then redirects to the Cognito logout URL", () => {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, "real-token");
    localStorage.setItem(HISTORY_KEY, JSON.stringify([{ run_id: "r1", question: "hi" }]));
    const location = { href: "" };
    vi.stubGlobal("location", location);

    signOut();

    expect(sessionStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
    expect(localStorage.getItem(HISTORY_KEY)).toBeNull();
    expect(location.href).toContain("/logout?");
  });
});
