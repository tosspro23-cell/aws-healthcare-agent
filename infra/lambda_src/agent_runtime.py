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

_DATA_DIR = Path(os.environ.get("CARE_AGENT_DATA_DIR", str(Path(__file__).resolve().parent / "data")))

# Constructed once per Lambda execution environment (warm-start reuse), not
# per invocation -- matches how the CLI/tests construct one HealthAgent and
# call .ask() repeatedly.
agent = HealthAgent(
    data_dir=_DATA_DIR,
    catalog_path=_DATA_DIR / "mock_biomarker_catalog.sqlite",
    kb_path=_DATA_DIR / "knowledge_base.jsonl",
)
