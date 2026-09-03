"""Assembles a self-contained Lambda deployment directory.

`care_agent` has zero third-party runtime dependencies for its default
(mock-narrator) path -- stdlib + sqlite3 only -- and `boto3` already ships
in the AWS Lambda Python runtime image. So this is a plain file copy, not a
`pip install`/Docker bundling step: copy the package, the sample dataset it
reads at runtime, and the handler into one flat directory that becomes the
Lambda deployment package (everything lands at the zip root, so
`import care_agent` and `import adapter` both resolve without any `src/`
nesting or PYTHONPATH tricks).

If a non-mock narrator backend is ever wired into the deployed Lambda (it
isn't yet -- Phase 1 only exercises the mock path), whichever optional SDK
that needs would have to be added to a real bundling step at that point.
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
    shutil.copy(LAMBDA_SRC_DIR / "adapter.py", BUILD_DIR / "adapter.py")

    return BUILD_DIR
