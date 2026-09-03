"""Loads the sample dataset bundle (JSON files) into typed models.

Design note: every accessor takes a ``user_id`` and raises ``UnknownUserError``
if the stored record belongs to a different user. The sample bundle only ships
one user, but this guard exists so the agent never silently answers with the
wrong person's health data if the dataset is ever extended -- a "wrong-user
data leakage" failure mode called out explicitly by this project's own
knowledge base (``kb_eval_001``).
"""

from __future__ import annotations

import json
from pathlib import Path

from care_agent.models import (
    Allergy,
    Biomarker,
    Bloodwork,
    Medication,
    Panel,
    QuestionnaireCaution,
    QuestionnaireContext,
    QuestionnaireDeclined,
    QuestionnaireFact,
    QuestionnairePreference,
    QuestionnaireUnknown,
    UserProfile,
)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class UnknownUserError(ValueError):
    """Raised when a data file's user_id does not match the requested user."""


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _check_user(record_user_id: str, requested_user_id: str, source: str) -> None:
    if record_user_id != requested_user_id:
        raise UnknownUserError(f"{source} belongs to user_id={record_user_id!r}, not the requested user_id={requested_user_id!r}")


class DataStore:
    """Read-only access to the profile, bloodwork, and questionnaire bundle."""

    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR):
        self.data_dir = Path(data_dir)

    # -- profile ---------------------------------------------------------
    def get_user_profile(self, user_id: str) -> UserProfile:
        raw = _load_json(self.data_dir / "sample_user_profile.json")
        _check_user(raw["user_id"], user_id, "sample_user_profile.json")
        return UserProfile(
            user_id=raw["user_id"],
            display_name=raw.get("display_name", ""),
            age=raw.get("age"),
            sex=raw.get("sex"),
            country=raw.get("country"),
            height_cm=raw.get("height_cm"),
            weight_kg=raw.get("weight_kg"),
            known_conditions=tuple(raw.get("known_conditions", [])),
            medications=tuple(
                Medication(name=m["name"], source=m.get("source", ""), confidence=m.get("confidence", ""))
                for m in raw.get("medications", [])
            ),
            allergies=tuple(
                Allergy(name=a["name"], source=a.get("source", ""), confidence=a.get("confidence", "")) for a in raw.get("allergies", [])
            ),
        )

    # -- bloodwork ---------------------------------------------------------
    def get_bloodwork(self, user_id: str) -> Bloodwork:
        raw = _load_json(self.data_dir / "sample_bloodwork.json")
        _check_user(raw["user_id"], user_id, "sample_bloodwork.json")

        def parse_panel(panel_raw: dict | None) -> Panel | None:
            if not panel_raw:
                return None
            biomarkers = tuple(
                Biomarker(
                    concept_id=b["concept_id"],
                    display_name=b.get("display_name", b["concept_id"]),
                    value=b["value"],
                    unit=b.get("unit", ""),
                    classification=b.get("classification"),
                    classification_basis=b.get("classification_basis"),
                    action_fields=tuple(b.get("action_fields", [])),
                    source=b.get("source"),
                )
                for b in panel_raw.get("biomarkers", [])
            )
            return Panel(
                panel_id=panel_raw["panel_id"],
                measurement_date=panel_raw["measurement_date"],
                biomarkers=biomarkers,
                overall_flags=tuple(panel_raw.get("overall_flags", [])),
            )

        latest = parse_panel(raw.get("latest_panel"))
        previous = tuple(p for p in (parse_panel(pp) for pp in raw.get("previous_panels", [])) if p is not None)
        return Bloodwork(user_id=raw["user_id"], latest_panel=latest, previous_panels=previous)

    # -- questionnaire -------------------------------------------------------
    def get_questionnaire_context(self, user_id: str) -> QuestionnaireContext:
        raw = _load_json(self.data_dir / "sample_questionnaire_context.json")
        _check_user(raw["user_id"], user_id, "sample_questionnaire_context.json")
        return QuestionnaireContext(
            user_id=raw["user_id"],
            completed_at=raw.get("completed_at"),
            facts=tuple(
                QuestionnaireFact(field=f["field"], value=f["value"], state=f.get("state"), source=f.get("source"))
                for f in raw.get("facts", [])
            ),
            cautions=tuple(
                QuestionnaireCaution(kind=c["kind"], detail=c["detail"], source=c.get("source")) for c in raw.get("cautions", [])
            ),
            preferences=tuple(
                QuestionnairePreference(field=p["field"], value=p["value"], source=p.get("source")) for p in raw.get("preferences", [])
            ),
            unknowns=tuple(
                QuestionnaireUnknown(field=u["field"], reason=u.get("reason", ""), source=u.get("source")) for u in raw.get("unknowns", [])
            ),
            declined=tuple(
                QuestionnaireDeclined(field=d["field"], instruction=d.get("instruction"), source=d.get("source"))
                for d in raw.get("declined", [])
            ),
            style_hint=raw.get("style_hint"),
        )

    # -- sample questions (for evals / examples) -----------------------------
    def get_sample_questions(self) -> list[dict]:
        raw = _load_json(self.data_dir / "sample_questions.json")
        return raw.get("questions", [])
