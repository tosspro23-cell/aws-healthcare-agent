import pytest

from care_agent.catalog import BiomarkerCatalog


@pytest.fixture()
def catalog(data_dir):
    return BiomarkerCatalog(data_dir / "mock_biomarker_catalog.sqlite")


def test_lookup_known_concept(catalog):
    entry = catalog.lookup("hba1c_percent")
    assert entry is not None
    assert entry.display_name == "HbA1c"
    assert entry.importance == "high"
    assert "MEDICAL" in entry.action_fields


def test_lookup_unknown_concept_returns_none(catalog):
    assert catalog.lookup("not_a_real_marker") is None


def test_alias_search_resolves_acronym(catalog):
    entry = catalog.search_by_alias("a1c")
    assert entry is not None
    assert entry.biomarker_name == "hba1c_percent"


def test_alias_search_case_insensitive(catalog):
    entry = catalog.search_by_alias("HbA1c")
    assert entry is not None
    assert entry.biomarker_name == "hba1c_percent"


def test_alias_search_unknown_returns_none(catalog):
    assert catalog.search_by_alias("totally not a marker") is None


def test_catalog_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        BiomarkerCatalog(tmp_path / "nope.sqlite")
