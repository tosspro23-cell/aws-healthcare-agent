"""Free, always-run regression guard: catches "the knowledge base was
edited but scripts/build_vector_index.py was never rerun" without
needing chromadb, boto3, or a real Bedrock call -- just the committed
data/knowledge_base_embeddings.jsonl sidecar and the real KB loader.
"""

from __future__ import annotations

import json

from care_agent.retrieval.bm25_retriever import DEFAULT_KB_PATH, load_knowledge_base
from care_agent.retrieval.chroma_retriever import EMBEDDINGS_PATH


def test_committed_embeddings_cover_exactly_the_current_knowledge_base():
    kb_ids = {chunk.id for chunk in load_knowledge_base(DEFAULT_KB_PATH)}

    embedded_ids = set()
    with EMBEDDINGS_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                embedded_ids.add(json.loads(line)["id"])

    assert embedded_ids == kb_ids, (
        "data/knowledge_base_embeddings.jsonl is out of sync with data/knowledge_base.jsonl -- "
        "rerun `python scripts/build_vector_index.py` after editing the knowledge base."
    )


def test_committed_embeddings_all_have_the_expected_dimension():
    from care_agent.retrieval.chroma_retriever import EMBEDDING_DIMENSIONS

    with EMBEDDINGS_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                record = json.loads(line)
                assert len(record["embedding"]) == EMBEDDING_DIMENSIONS, (
                    f"{record['id']} has a {len(record['embedding'])}-dim embedding, "
                    f"expected {EMBEDDING_DIMENSIONS} -- was EMBEDDING_DIMENSIONS changed without rerunning the build script?"
                )
