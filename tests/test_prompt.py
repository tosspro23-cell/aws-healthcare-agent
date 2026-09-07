"""Unit tests for care_agent.narrator._prompt.build_user_message -- the
shared user-turn content every LLM narrator sends (previously duplicated,
identically, in five separate files).

No AWS/API dependency: this is a pure string-formatting function, so
these tests run unconditionally in CI, unlike the per-backend narrator
tests (which are gated behind pytest.importorskip for their optional SDK).
"""

from __future__ import annotations

from care_agent.models import KnowledgeChunk, RetrievedChunk
from care_agent.narrator._prompt import MAX_REFERENCE_CHUNKS, build_user_message


def _chunk(chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=KnowledgeChunk(
            id=chunk_id,
            title=f"Title for {chunk_id}",
            topic=("nutrition",),
            source_name=f"Source {chunk_id}",
            source_url=f"https://example.com/{chunk_id}",
            content=f"Content body for {chunk_id}.",
        ),
        score=1.0,
    )


def test_no_retrieved_chunks_produces_the_original_message_shape():
    """A Brief with no retrieved chunks (every existing narrator test)
    must produce byte-identical output to the string every narrator
    built inline before this function existed."""
    message = build_user_message("What should I focus on first?", "Your LDL-C is 162 mg/dL (high).", retrieved_chunks=None)
    assert message == (
        "User's question: What should I focus on first?\n\n"
        "Grounded facts and constraints to rephrase (do not add to this list):\n"
        "Your LDL-C is 162 mg/dL (high)."
    )


def test_empty_list_is_treated_the_same_as_none():
    with_none = build_user_message("q", "grounded", retrieved_chunks=None)
    with_empty = build_user_message("q", "grounded", retrieved_chunks=[])
    assert with_none == with_empty
    assert "Reference material" not in with_none


def test_retrieved_chunks_add_a_labeled_reference_section():
    message = build_user_message("What foods help my LDL?", "Your LDL-C is 162 mg/dL (high).", retrieved_chunks=[_chunk("kb_1")])

    assert "Reference material" in message
    assert "general educational background" in message
    assert "not a new grounded fact" in message
    assert "[Source kb_1] Title for kb_1: Content body for kb_1." in message
    # The original grounded-facts section must still be present, unchanged.
    assert "Grounded facts and constraints to rephrase (do not add to this list):\nYour LDL-C is 162 mg/dL (high)." in message


def test_reference_section_is_capped_at_max_reference_chunks():
    chunks = [_chunk(f"kb_{i}") for i in range(MAX_REFERENCE_CHUNKS + 2)]
    message = build_user_message("q", "grounded", retrieved_chunks=chunks)

    for i in range(MAX_REFERENCE_CHUNKS):
        assert f"kb_{i}" in message
    for i in range(MAX_REFERENCE_CHUNKS, MAX_REFERENCE_CHUNKS + 2):
        assert f"kb_{i}" not in message
