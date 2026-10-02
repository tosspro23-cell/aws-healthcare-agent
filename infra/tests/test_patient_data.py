"""Tests for infra/lambda_src/patient_data.py -- a plain local-file read,
no AWS resources to mock (unlike adapter.py's moto-mocked table/bucket)."""

import json

import patient_data  # noqa: E402 -- import after conftest sets sys.path/env vars


def _api_gateway_event(user_id: str | None) -> dict:
    event: dict = {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "cognito-sub-caller-1"}}}}}
    if user_id is not None:
        event["pathParameters"] = {"user_id": user_id}
    return event


def test_returns_profile_bloodwork_and_questionnaire_for_known_user():
    result = patient_data.handler(_api_gateway_event("user_demo_001"), None)

    assert result["statusCode"] == 200
    payload = json.loads(result["body"])
    assert payload["user_id"] == "user_demo_001"
    assert payload["profile"]["display_name"] == "Alex"

    biomarkers = payload["bloodwork"]["latest_panel"]["biomarkers"]
    ldl = next(b for b in biomarkers if b["concept_id"] == "ldl_c_mg_dl")
    assert ldl["value"] == 162
    assert ldl["unit"] == "mg/dL"

    facts = payload["questionnaire"]["facts"]
    assert any(f["field"] == "nutrition.sugary_foods" for f in facts)


def test_previous_panels_included():
    result = patient_data.handler(_api_gateway_event("user_demo_001"), None)
    payload = json.loads(result["body"])
    assert len(payload["bloodwork"]["previous_panels"]) >= 1


def test_unknown_user_returns_404():
    result = patient_data.handler(_api_gateway_event("someone_else"), None)
    assert result["statusCode"] == 404


def test_missing_user_id_path_param_returns_400():
    result = patient_data.handler(_api_gateway_event(None), None)
    assert result["statusCode"] == 400


def test_empty_user_id_path_param_returns_400():
    result = patient_data.handler(_api_gateway_event(""), None)
    assert result["statusCode"] == 400
