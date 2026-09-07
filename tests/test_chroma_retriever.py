"""Unit tests for the optional local Chroma retrieval backend.

Skipped entirely (via ``importorskip``) unless the ``chromadb`` package is
installed -- CI's default `pip install -e ".[dev]"` does not install it,
same tier as `boto3`/`test_bedrock_narrator.py`. Most tests build a tiny,
fully synthetic index (never the real 68-chunk committed one) with a fake
``embed_fn``, so retrieval-logic assertions are deterministic and need
neither real Bedrock nor real network access even when `chromadb` is
present. A couple of tests mock `boto3.client` to test the *default*
embedding wiring's env-var resolution and request/response parsing,
mirroring `test_bedrock_narrator.py` exactly.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("chromadb")

from care_agent.retrieval.chroma_retriever import ChromaRetriever, _bedrock_embed_fn  # noqa: E402

_TINY_KB = [
    {
        "id": "c_alpha",
        "title": "Alpha topic",
        "topic": ["alpha"],
        "source_name": "test-source",
        "source_url": "https://example.com/alpha",
        "content": "Alpha content.",
    },
    {
        "id": "c_beta",
        "title": "Beta topic",
        "topic": ["beta"],
        "source_name": "test-source",
        "source_url": "https://example.com/beta",
        "content": "Beta content.",
    },
    {
        "id": "c_gamma",
        "title": "Gamma topic",
        "topic": ["gamma", "alpha"],
        "source_name": "test-source",
        "source_url": "https://example.com/gamma",
        "content": "Gamma content.",
    },
    {
        "id": "c_delta",
        "title": "Delta topic",
        "topic": ["delta"],
        "source_name": "test-source",
        "source_url": "https://example.com/delta",
        "content": "Delta content.",
    },
]

# One clean unit vector per axis so cosine similarity is exact and
# hand-checkable: alpha=[1,0,0], beta=[0,1,0], gamma=[0,0,1], delta
# opposite alpha. "tied" is equidistant from alpha/beta/gamma (cos ~0.577
# to each) and used to test the topic-tag boost without a pre-existing
# winner to fight against.
_EMBEDDINGS = {
    "c_alpha": [1.0, 0.0, 0.0],
    "c_beta": [0.0, 1.0, 0.0],
    "c_gamma": [0.0, 0.0, 1.0],
    "c_delta": [-1.0, 0.0, 0.0],
}
_TIED_QUERY_VECTOR = [0.5773502691896258, 0.5773502691896258, 0.5773502691896258]
_FAKE_QUERIES = {
    "exact alpha": [1.0, 0.0, 0.0],
    "tied query": _TIED_QUERY_VECTOR,
}


def _build_retriever(tmp_path, embed_fn=None):
    import chromadb

    kb_path = tmp_path / "knowledge_base.jsonl"
    with kb_path.open("w", encoding="utf-8") as fh:
        for chunk in _TINY_KB:
            fh.write(json.dumps(chunk) + "\n")

    index_dir = tmp_path / "vector_index"
    client = chromadb.PersistentClient(path=str(index_dir))
    collection = client.create_collection("knowledge_base", metadata={"hnsw:space": "cosine"})
    collection.add(ids=list(_EMBEDDINGS.keys()), embeddings=list(_EMBEDDINGS.values()))

    fake_embed = embed_fn or (lambda query: _FAKE_QUERIES[query])
    return ChromaRetriever(kb_path=kb_path, index_dir=index_dir, embed_fn=fake_embed)


def test_retrieve_ranks_the_exact_match_first(tmp_path):
    retriever = _build_retriever(tmp_path)
    results = retriever.retrieve("exact alpha", top_k=2)
    assert results[0].chunk.id == "c_alpha"
    assert results[0].score == pytest.approx(1.0)


def test_topic_filter_boosts_a_tied_candidate_above_its_untagged_peers(tmp_path):
    retriever = _build_retriever(tmp_path)
    results = retriever.retrieve("tied query", top_k=4, topic_filter={"gamma"})
    assert results[0].chunk.id == "c_gamma"
    assert results[0].matched_terms == ("gamma",)
    # ~0.577 (tied cosine similarity) + 0.15 (TAG_BOOST) for the one match;
    # retrieve() rounds to 4dp, so compare against that same rounding.
    assert results[0].score == pytest.approx(round(0.5773502691896258 + 0.15, 4), abs=1e-4)


def test_top_k_limits_result_count(tmp_path):
    retriever = _build_retriever(tmp_path)
    results = retriever.retrieve("tied query", top_k=1)
    assert len(results) == 1


def test_empty_query_returns_no_results_without_calling_embed_fn(tmp_path):
    embed_fn = MagicMock()
    retriever = _build_retriever(tmp_path, embed_fn=embed_fn)
    assert retriever.retrieve("", top_k=5) == []
    embed_fn.assert_not_called()


def test_get_by_id(tmp_path):
    retriever = _build_retriever(tmp_path)
    chunk = retriever.get_by_id("c_gamma")
    assert chunk is not None
    assert "alpha" in chunk.topic

    assert retriever.get_by_id("does_not_exist") is None


def test_repeated_construction_does_not_recopy_the_index(tmp_path):
    """The writable-copy cache (chroma_retriever.py's _writable_index_copy)
    should reuse the same scratch directory for the same source index
    across repeated construction in one process -- never mutate the
    committed source directory, but also don't recopy it every time
    (e.g. a warm Lambda, or a REPL constructing more than one agent)."""
    from care_agent.retrieval import chroma_retriever

    chroma_retriever._writable_copy_cache.clear()
    _build_retriever(tmp_path)  # builds the on-disk index at tmp_path/vector_index
    index_dir = tmp_path / "vector_index"

    first_copy = chroma_retriever._writable_index_copy(index_dir)
    second_copy = chroma_retriever._writable_index_copy(index_dir)
    assert first_copy == second_copy
    assert chroma_retriever._writable_copy_cache[str(index_dir)] == first_copy


def _fake_titan_response(embedding: list[float]) -> MagicMock:
    client = MagicMock()
    body = MagicMock()
    body.read.return_value = json.dumps({"embedding": embedding, "inputTextTokenCount": 7}).encode("utf-8")
    client.invoke_model.return_value = {"contentType": "application/json", "body": body}
    return client


def test_default_embed_fn_sends_expected_request_and_parses_response():
    fake_client = _fake_titan_response([0.1, 0.2, 0.3])
    with patch("boto3.client", return_value=fake_client):
        embed = _bedrock_embed_fn()
    result = embed("some question text")

    assert result == [0.1, 0.2, 0.3]
    call_kwargs = fake_client.invoke_model.call_args.kwargs
    assert call_kwargs["modelId"] == "amazon.titan-embed-text-v2:0"
    assert call_kwargs["contentType"] == "application/json"
    assert call_kwargs["accept"] == "application/json"
    body = json.loads(call_kwargs["body"])
    assert body["inputText"] == "some question text"
    assert body["dimensions"] == 512
    assert body["normalize"] is True


def test_default_region_from_env(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("BEDROCK_REGION", "us-west-2")
    with patch("boto3.client", return_value=_fake_titan_response([0.0])) as mock_client:
        _bedrock_embed_fn()
    mock_client.assert_called_once_with("bedrock-runtime", region_name="us-west-2")


def test_explicit_model_id_overrides_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v1")
    fake_client = _fake_titan_response([0.0])
    with patch("boto3.client", return_value=fake_client):
        embed = _bedrock_embed_fn(model_id="amazon.titan-embed-text-v2:0")
    embed("hi")
    assert fake_client.invoke_model.call_args.kwargs["modelId"] == "amazon.titan-embed-text-v2:0"
