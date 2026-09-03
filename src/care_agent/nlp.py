"""Small free-text -> biomarker concept_id resolver.

Combines the SQLite alias table (``kb.marker_alias``) with a short list of
colloquial synonyms that aren't in the formal alias table (e.g. a bare
"glucose" or "cholesterol"). This is intentionally simple: it is a lookup,
not a model, so its behavior is exhaustively testable.
"""

from __future__ import annotations

import re

from care_agent.catalog import BiomarkerCatalog

_COLLOQUIAL_SYNONYMS: dict[str, str] = {
    "glucose": "fasting_glucose_mg_dl",
    "blood sugar": "fasting_glucose_mg_dl",
    "cholesterol": "ldl_c_mg_dl",
    "bad cholesterol": "ldl_c_mg_dl",
    "good cholesterol": "hdl_c_mg_dl",
    "ldl": "ldl_c_mg_dl",
    "hdl": "hdl_c_mg_dl",
    "thyroid": "tsh_miu_l",
    "inflammation": "hs_crp_mg_l",
    "crp": "hs_crp_mg_l",
    "liver": "alt_u_l",
    "kidney": "egfr_ml_min_1_73m2",
    "vitamin d": "vitamin_d_25oh_ng_ml",
    "triglycerides": "triglycerides_mg_dl",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def _candidate_phrases(text: str) -> list[str]:
    """Generate 1-4 word lowercase n-gram candidates from the text.

    Hyphens and parentheses are treated as word separators, not part of a
    token -- this matches how the catalog's ``normalized_alias`` column was
    built (``"hs-CRP"`` -> ``"hs crp"``, ``"Lp(a)"`` -> ``"lp a"``,
    ``"Non-HDL cholesterol"`` -> ``"non hdl cholesterol"``). Without this,
    a hyphenated mention like "hs-CRP" tokenizes as the single word
    "hs-crp", which can never equal the space-joined alias "hs crp" and so
    silently never resolves -- caught by live testing against a real LLM
    narrator asked "Tell me about my hs-CRP result", which correctly (and
    confusingly) reported that no hs-CRP context had been given to it.
    """
    words = _WORD_RE.findall(text.lower())
    phrases: list[str] = []
    for n in (4, 3, 2, 1):
        for i in range(len(words) - n + 1):
            phrases.append(" ".join(words[i : i + n]))
    return phrases


def find_concept_mentions(text: str, catalog: BiomarkerCatalog) -> list[str]:
    """Return the ordered, de-duplicated list of concept_ids mentioned in text."""
    found: list[str] = []
    for phrase in _candidate_phrases(text):
        concept_id = None
        if phrase in _COLLOQUIAL_SYNONYMS:
            concept_id = _COLLOQUIAL_SYNONYMS[phrase]
        else:
            entry = catalog.search_by_alias(phrase)
            if entry is not None:
                concept_id = entry.biomarker_name
        if concept_id and concept_id not in found:
            found.append(concept_id)
    return found
