import pytest

from care_agent.catalog import BiomarkerCatalog
from care_agent.nlp import find_concept_mentions


@pytest.fixture()
def catalog(data_dir):
    return BiomarkerCatalog(data_dir / "mock_biomarker_catalog.sqlite")


def test_finds_ldl_and_hba1c(catalog):
    mentions = find_concept_mentions("My LDL and HbA1c are high.", catalog)
    assert "ldl_c_mg_dl" in mentions
    assert "hba1c_percent" in mentions


def test_finds_glucose_colloquial(catalog):
    mentions = find_concept_mentions("Can you tell me if my glucose got worse?", catalog)
    assert mentions == ["fasting_glucose_mg_dl"]


def test_finds_cholesterol_colloquial(catalog):
    mentions = find_concept_mentions("Should I take supplements for cholesterol?", catalog)
    assert mentions == ["ldl_c_mg_dl"]


def test_case_insensitive(catalog):
    assert find_concept_mentions("what about LDL", catalog) == find_concept_mentions("what about ldl", catalog)


def test_no_mentions_for_unrelated_text(catalog):
    assert find_concept_mentions("What is the weather like today?", catalog) == []


def test_no_duplicate_mentions(catalog):
    mentions = find_concept_mentions("LDL LDL LDL, what about my LDL?", catalog)
    assert mentions.count("ldl_c_mg_dl") == 1


def test_alias_via_formal_catalog_table(catalog):
    mentions = find_concept_mentions("How is my eGFR looking?", catalog)
    assert "egfr_ml_min_1_73m2" in mentions


def test_hyphenated_alias_resolves(catalog):
    """Regression test: hs-CRP's catalog alias is normalized as "hs crp"
    (space-separated); a naive tokenizer that keeps the hyphen inside the
    word ("hs-crp") never matches that and silently drops the mention.
    Found via live testing: an LLM narrator asked about hs-CRP specifically
    was fed the wrong (unrelated) markers because this resolution failed,
    and reasonably reported that no hs-CRP data had been given to it.
    """
    mentions = find_concept_mentions("Tell me about my hs-CRP result.", catalog)
    assert "hs_crp_mg_l" in mentions


def test_parenthesized_alias_resolves(catalog):
    """Regression test: "Lp(a)" normalizes to "lp a" in the catalog; a
    tokenizer that keeps parentheses attached to the word never matches it.
    """
    mentions = find_concept_mentions("What about my Lp(a)?", catalog)
    assert "lp_a_mg_dl" in mentions


def test_hyphenated_multiword_alias_resolves(catalog):
    """Regression test: "Non-HDL cholesterol" normalizes to
    "non hdl cholesterol" in the catalog."""
    mentions = find_concept_mentions("What is my non-HDL cholesterol?", catalog)
    assert "non_hdl_c_mg_dl" in mentions
