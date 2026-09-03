"""Sets env vars adapter.py reads at *import* time, before any test module
imports it. Points CARE_AGENT_DATA_DIR at the repo's real data/ directory so
tests don't need the Lambda packaging step (build_lambda_asset.py) run
first.
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INFRA_DIR = _REPO_ROOT / "infra"

# adapter.py imports `care_agent` (lives at <repo_root>/src for local dev;
# the deployed Lambda package flattens it alongside adapter.py instead --
# see build_lambda_asset.py) and is itself imported as a bare module
# (`import adapter`), matching how the Lambda runtime loads it.
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_INFRA_DIR / "lambda_src"))

os.environ.setdefault("CARE_AGENT_DATA_DIR", str(_REPO_ROOT / "data"))
os.environ.setdefault("RUNS_TABLE_NAME", "test-runs-table")
os.environ.setdefault("EVIDENCE_BUCKET_NAME", "test-evidence-bucket")

# moto convention: fake credentials so a misconfigured test environment
# can never accidentally touch a real AWS account.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
