#!/usr/bin/env python3
"""Walks the OAuth2 Authorization Code + PKCE flow against the deployed
Cognito Hosted UI, entirely with stdlib (no new dependency): generates a
PKCE code_verifier/code_challenge pair, opens your browser to the Cognito
login/signup page, runs a tiny local HTTP server to catch the redirect,
and exchanges the resulting code for tokens.

Usage:

    python infra/scripts/get_dev_token.py

Prints an `export CARE_AGENT_ID_TOKEN=...` line you can eval/copy, and
optionally writes it to a local file if `--out` is given. The token is an
ID token (not an access token) -- API Gateway's Cognito JWT authorizer here
validates against the `aud` claim, which the ID token carries and the
access token does not.

Reads the User Pool / App Client / Hosted UI domain from the deployed
CareAgentAuthStack's CloudFormation outputs via boto3, rather than
hardcoding them, so this keeps working across redeploys.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

import boto3

REDIRECT_HOST = "localhost"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/callback"


def _stack_outputs(stack_name: str, region: str) -> dict[str, str]:
    client = boto3.client("cloudformation", region_name=region)
    response = client.describe_stacks(StackName=stack_name)
    outputs = response["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def _make_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) per RFC 7636."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures ?code=... from the OAuth redirect, then serves a static
    "you can close this tab" page and lets the caller retrieve the code."""

    received_code: str | None = None
    received_error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 -- required name by BaseHTTPRequestHandler
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.received_code = query.get("code", [None])[0]
        _CallbackHandler.received_error = query.get("error_description", query.get("error", [None]))[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        message = "Sign-in complete. You can close this tab." if _CallbackHandler.received_code else "Sign-in failed. Check the terminal."
        self.wfile.write(f"<html><body><p>{message}</p></body></html>".encode())

    def log_message(self, *args) -> None:  # silence default request logging
        pass


def _wait_for_callback(timeout_seconds: int = 180) -> str:
    server = http.server.HTTPServer((REDIRECT_HOST, REDIRECT_PORT), _CallbackHandler)
    server.timeout = timeout_seconds
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    server.server_close()

    if _CallbackHandler.received_error:
        raise RuntimeError(f"Cognito returned an error: {_CallbackHandler.received_error}")
    if not _CallbackHandler.received_code:
        raise TimeoutError(f"No redirect received on {REDIRECT_URI} within {timeout_seconds}s.")
    return _CallbackHandler.received_code


def _build_authorization_url(*, domain: str, client_id: str, code_challenge: str) -> str:
    return "https://" + domain + "/oauth2/authorize?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid email profile",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )


def _build_token_request_body(*, client_id: str, code: str, code_verifier: str) -> bytes:
    return urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")


def _exchange_code_for_tokens(*, domain: str, client_id: str, code: str, code_verifier: str) -> dict:
    token_url = f"https://{domain}/oauth2/token"
    request = urllib.request.Request(
        token_url,
        data=_build_token_request_body(client_id=client_id, code=code, code_verifier=code_verifier),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack-name", default="CareAgentAuthStack")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--out", help="Optional file path to also write the export line to.")
    args = parser.parse_args()

    outputs = _stack_outputs(args.stack_name, args.region)
    client_id = outputs["AppClientId"]
    domain = outputs["HostedUiDomain"]

    code_verifier, code_challenge = _make_pkce_pair()
    auth_url = _build_authorization_url(domain=domain, client_id=client_id, code_challenge=code_challenge)

    print("Opening your browser to sign in / sign up...")
    print(f"If it doesn't open automatically, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    print(f"Waiting for the redirect on {REDIRECT_URI} ...")
    code = _wait_for_callback()

    tokens = _exchange_code_for_tokens(domain=domain, client_id=client_id, code=code, code_verifier=code_verifier)
    id_token = tokens["id_token"]

    export_line = f"export CARE_AGENT_ID_TOKEN={id_token}"
    print("\nSuccess. Run this to use the token in this shell session:\n")
    print(export_line)

    if args.out:
        with open(args.out, "w") as f:
            f.write(export_line + "\n")
        print(f"\nAlso wrote it to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
