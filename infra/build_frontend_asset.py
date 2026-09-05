"""Builds the Workbench frontend (`frontend/dist`) for `FrontendStack`'s
S3 deployment -- the browser-app equivalent of `build_lambda_asset.py`.

Needs `frontend/.env.local` to carry the real deployed Cognito/API values
for the resulting bundle to actually work once loaded in a browser (see
`frontend/.env.example`) -- `vite build` itself succeeds without it (Vite
just inlines `undefined` for any missing `VITE_*` value), so a `cdk
synth`/test run with no `.env.local` present (true in CI, which never
serves this bundle in a real browser) still produces a valid `dist/` for
CDK to package, it just wouldn't function correctly if actually deployed
and opened. A real `cdk deploy` needs `.env.local` filled in first.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

INFRA_DIR = Path(__file__).resolve().parent
REPO_ROOT = INFRA_DIR.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
BUILD_DIR = FRONTEND_DIR / "dist"


def build_frontend_asset() -> Path:
    if not (FRONTEND_DIR / "node_modules").exists():
        subprocess.run(["npm", "install"], cwd=FRONTEND_DIR, check=True)
    subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)
    return BUILD_DIR
