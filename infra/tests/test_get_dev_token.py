"""Tests for the pure/testable parts of get_dev_token.py: PKCE generation
and URL/request construction. The interactive parts (opening a browser,
running a local HTTP server, and the actual token exchange over the
network) are deliberately not covered here -- same reasoning as
docs/AWS_SETUP.md being a manual walkthrough rather than something this
session can run for you: they require a real human login in a real browser.
"""

import base64
import hashlib
import sys
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import get_dev_token as gdt  # noqa: E402


def test_pkce_pair_verifier_is_url_safe_and_correct_length():
    verifier, _challenge = gdt._make_pkce_pair()
    # RFC 7636: 43-128 characters, unreserved URL-safe charset.
    assert 43 <= len(verifier) <= 128
    assert all(c.isalnum() or c in "-._~" for c in verifier)


def test_pkce_challenge_is_sha256_of_verifier():
    verifier, challenge = gdt._make_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_pkce_pairs_are_unique_each_call():
    pair_a = gdt._make_pkce_pair()
    pair_b = gdt._make_pkce_pair()
    assert pair_a != pair_b


def test_authorization_url_contains_pkce_and_client_params():
    url = gdt._build_authorization_url(domain="example.auth.us-east-1.amazoncognito.com", client_id="abc123", code_challenge="xyz789")
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "example.auth.us-east-1.amazoncognito.com"
    assert parsed.path == "/oauth2/authorize"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["abc123"]
    assert query["code_challenge"] == ["xyz789"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [gdt.REDIRECT_URI]


def test_token_request_body_is_form_encoded_with_pkce_verifier():
    body = gdt._build_token_request_body(client_id="abc123", code="the-code", code_verifier="the-verifier")
    parsed = urllib.parse.parse_qs(body.decode("utf-8"))

    assert parsed["grant_type"] == ["authorization_code"]
    assert parsed["client_id"] == ["abc123"]
    assert parsed["code"] == ["the-code"]
    assert parsed["code_verifier"] == ["the-verifier"]
    assert parsed["redirect_uri"] == [gdt.REDIRECT_URI]


def test_stack_outputs_parses_cloudformation_response():
    fake_client = MagicMock()
    fake_client.describe_stacks.return_value = {
        "Stacks": [
            {
                "Outputs": [
                    {"OutputKey": "AppClientId", "OutputValue": "client-abc"},
                    {"OutputKey": "HostedUiDomain", "OutputValue": "example.auth.us-east-1.amazoncognito.com"},
                ]
            }
        ]
    }
    with patch("boto3.client", return_value=fake_client):
        outputs = gdt._stack_outputs("CareAgentAuthStack", "us-east-1")

    assert outputs == {
        "AppClientId": "client-abc",
        "HostedUiDomain": "example.auth.us-east-1.amazoncognito.com",
    }
    fake_client.describe_stacks.assert_called_once_with(StackName="CareAgentAuthStack")
