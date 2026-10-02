"""API handler for `GET /patient-data/{user_id}`: read-only access to the
sample dataset bundle (profile, bloodwork, questionnaire) backing the
Workbench's "Patient Data" rail tab -- lets a reviewer see the exact raw
source record an answer's `grounded_facts` cite, not just the citation.

Auth model matches `/ask` (`adapter.py`), not `/runs/{run_id}`
(`get_run.py`): the JWT authorizer (every route, see `../stacks/api_stack.py`)
proves the caller is a genuine authenticated user, but `user_id` here
names *whose synthetic health-data profile* to read, not a resource the
caller owns -- see `auth_context.py`'s own docstring on why `user_id` is
deliberately not treated as an identity/ownership claim. There is exactly
one demo `user_id` in this project's shipped dataset, and `/ask` already
lets any authenticated caller query it with no stricter check; this
handler intentionally does not add one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from care_agent.data_store import DataStore, UnknownUserError

# Not `DataStore()` with no args: its own `DEFAULT_DATA_DIR` resolves three
# parents up from `data_store.py`'s own location, correct for this repo's
# `src/care_agent/` layout but wrong once flattened into the deployed
# Lambda package (`build_lambda_asset.py` copies `care_agent/` and `data/`
# as siblings at the package root). Same resolution `agent_runtime.py`
# already uses for exactly this reason -- confirmed by reading it, not
# assumed -- so this would otherwise pass every local/CI test and 500 in
# production.
_DATA_DIR = Path(os.environ.get("CARE_AGENT_DATA_DIR", str(Path(__file__).resolve().parent / "data")))
_store = DataStore(_DATA_DIR)


def _json_response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=str),
    }


def handler(event: dict, context: object) -> dict:
    path_params = event.get("pathParameters") or {}
    user_id = path_params.get("user_id")
    if not user_id:
        return _json_response(400, {"error": "user_id path parameter is required."})

    try:
        profile = _store.get_user_profile(user_id)
        bloodwork = _store.get_bloodwork(user_id)
        questionnaire = _store.get_questionnaire_context(user_id)
    except UnknownUserError:
        return _json_response(404, {"error": f"No data on file for user_id={user_id!r}."})

    return _json_response(
        200,
        {
            "user_id": user_id,
            "profile": profile.as_dict(),
            "bloodwork": bloodwork.as_dict(),
            "questionnaire": questionnaire.as_dict(),
        },
    )
