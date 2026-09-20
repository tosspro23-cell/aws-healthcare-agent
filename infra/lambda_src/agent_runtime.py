"""Shared `HealthAgent` construction, used by every Lambda that needs to
actually run `care_agent` (`adapter.py`'s synchronous path, `agent_task.py`'s
Step Functions task). Kept in one place so both paths resolve the dataset
location identically instead of duplicating the same three lines.

`_DATA_DIR` is overridable via `CARE_AGENT_DATA_DIR` so this module can be
imported and tested locally (pointed at the repo's real `data/` directory)
without needing the Lambda packaging step run first -- in the deployed
package, `build_lambda_asset.py` places `data/` next to every handler file,
and the default (no env var) picks that up correctly.
"""

from __future__ import annotations

import os
from pathlib import Path

from care_agent.agent import HealthAgent
from care_agent.tool_planner import BedrockToolPlanner

_DATA_DIR = Path(os.environ.get("CARE_AGENT_DATA_DIR", str(Path(__file__).resolve().parent / "data")))

# Constructed once per Lambda execution environment (warm-start reuse), not
# per invocation -- matches how the CLI/tests construct one HealthAgent and
# call .ask() repeatedly.
agent = HealthAgent(
    data_dir=_DATA_DIR,
    catalog_path=_DATA_DIR / "mock_biomarker_catalog.sqlite",
    kb_path=_DATA_DIR / "knowledge_base.jsonl",
)

# V2 (`ask_compound`, `engine="v2"` requests -- see `adapter.py`). Same
# lazy-boto3-client pattern as `HealthAgent`'s own narrator construction:
# building this never makes a network call or needs real credentials, only
# an actual `.propose_plan()` call does -- constructing it unconditionally
# here is exactly as safe as `agent` above, and `infra/tests/test_adapter.py`
# monkeypatches this with a scripted fake for its `engine="v2"` tests, the
# same way `tests/test_orchestrator.py` does for the `care_agent` package's
# own tests, rather than making a real Bedrock call in CI.
tool_planner = BedrockToolPlanner()
