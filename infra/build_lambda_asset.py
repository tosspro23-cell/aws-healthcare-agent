"""Assembles a self-contained Lambda deployment directory, shared by every
Lambda function this app defines.

`care_agent` has zero third-party runtime dependencies for its default
(mock-narrator) path -- stdlib + sqlite3 only -- and `boto3` already ships
in the AWS Lambda Python runtime image. So this is a plain file copy, not a
`pip install`/Docker bundling step: copy the package, the sample dataset it
reads at runtime, and every handler module into one flat directory that
becomes the Lambda deployment package (everything lands at the zip root, so
`import care_agent`, `import agent_runtime`, and `import adapter` /
`agent_task` / `start_run` / `get_run` / `cancel_run` all resolve without
any `src/` nesting or PYTHONPATH tricks).

Every Lambda `Function` construct points its `handler` at a different
`<module>.handler` within this *same* asset directory (see `api_stack.py`
and `orchestration_stack.py`) -- one shared build, one `Code.from_asset()`
call, several entry points into it. Handler modules that don't need
`care_agent` at all (`start_run.py`, `get_run.py`, `cancel_run.py` only
touch Step Functions / DynamoDB) still get it bundled; the package is tiny
enough that this costs nothing meaningful and keeps this build step simple.

The deployed AskHandler's Bedrock narrator (`CARE_AGENT_NARRATOR_BACKEND=
bedrock`, see `api_stack.py`) and V2's `BedrockToolPlanner` (`engine="v2"`,
see `agent_runtime.py`) both only need `boto3` -- already in the Lambda
runtime image, so this stays a plain file copy for them too. A *different*
optional narrator/planner backend (Anthropic/OpenAI/Google's own SDKs)
would need a real bundling step (`pip install` or Docker) added here first;
none of those are wired into any deployed Lambda as of this writing.
"""

from __future__ import annotations

import shutil
from pathlib import Path

INFRA_DIR = Path(__file__).resolve().parent
REPO_ROOT = INFRA_DIR.parent
LAMBDA_SRC_DIR = INFRA_DIR / "lambda_src"
BUILD_DIR = INFRA_DIR / ".build" / "lambda_package"


def build_lambda_asset() -> Path:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)

    shutil.copytree(REPO_ROOT / "src" / "care_agent", BUILD_DIR / "care_agent")
    shutil.copytree(REPO_ROOT / "data", BUILD_DIR / "data")

    for py_file in LAMBDA_SRC_DIR.glob("*.py"):
        shutil.copy(py_file, BUILD_DIR / py_file.name)

    return BUILD_DIR
