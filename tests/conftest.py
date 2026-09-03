from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from care_agent.agent import HealthAgent
from care_agent.catalog import DEFAULT_CATALOG_PATH
from care_agent.data_store import DEFAULT_DATA_DIR
from care_agent.retrieval import DEFAULT_KB_PATH

REPO_DATA_DIR = DEFAULT_DATA_DIR


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return REPO_DATA_DIR


@pytest.fixture(scope="session")
def agent() -> HealthAgent:
    return HealthAgent()


@pytest.fixture()
def dataset_builder(tmp_path: Path):
    """Builds a self-contained data directory for edge-case scenarios.

    Copies the real knowledge base + biomarker catalog (shared reference
    data) and lets the test override profile/bloodwork/questionnaire JSON.
    """

    def _build(
        *,
        profile: dict | None = None,
        bloodwork: dict | None = None,
        questionnaire: dict | None = None,
    ) -> Path:
        target = tmp_path / "dataset"
        target.mkdir(exist_ok=True)
        shutil.copy(DEFAULT_KB_PATH, target / "knowledge_base.jsonl")
        shutil.copy(DEFAULT_CATALOG_PATH, target / "mock_biomarker_catalog.sqlite")

        default_profile = json.loads((REPO_DATA_DIR / "sample_user_profile.json").read_text())
        default_bloodwork = json.loads((REPO_DATA_DIR / "sample_bloodwork.json").read_text())
        default_questionnaire = json.loads((REPO_DATA_DIR / "sample_questionnaire_context.json").read_text())

        (target / "sample_user_profile.json").write_text(json.dumps(profile or default_profile))
        (target / "sample_bloodwork.json").write_text(json.dumps(bloodwork or default_bloodwork))
        (target / "sample_questionnaire_context.json").write_text(json.dumps(questionnaire or default_questionnaire))
        return target

    return _build
