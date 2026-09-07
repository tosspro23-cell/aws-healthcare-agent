"""Builds the committed local vector index for `ChromaRetriever`
(`src/care_agent/retrieval/chroma_retriever.py`) from the real
`data/knowledge_base.jsonl` corpus.

Makes 68 real Bedrock Titan Embeddings calls (one per knowledge-base
chunk) -- deliberately manual, not run in CI or pytest, same tier as
`infra/scripts/stress_test.py`. Run this whenever `data/knowledge_base.jsonl`
changes; `tests/test_vector_index_freshness.py` has a free, always-run
check that the committed embeddings sidecar's id set still matches the KB's.

Run with:
    python scripts/build_vector_index.py

Writes two committed artifacts:
  - data/knowledge_base_embeddings.jsonl  -- the single source of truth
    for these vectors (one JSON object per line: {"id", "embedding"})
  - data/vector_index/                    -- a Chroma PersistentClient
    directory built from that sidecar, mirroring the already-committed,
    generated data/mock_biomarker_catalog.sqlite as a precedent for a
    generated binary artifact checked into git and bundled via flat copy.
"""

from __future__ import annotations

import json
import shutil

from care_agent.retrieval.bm25_retriever import DEFAULT_KB_PATH, load_knowledge_base
from care_agent.retrieval.chroma_retriever import _COLLECTION_NAME, EMBEDDINGS_PATH, _bedrock_embed_fn
from care_agent.retrieval.chroma_retriever import DEFAULT_VECTOR_INDEX_DIR as INDEX_DIR


def _embed_text(chunk) -> str:
    return f"{chunk.title}\n{chunk.content}\nTopics: {', '.join(chunk.topic)}"


def main() -> None:
    import chromadb

    chunks = load_knowledge_base(DEFAULT_KB_PATH)
    embed = _bedrock_embed_fn()

    print(f"Embedding {len(chunks)} chunks via Bedrock Titan Embeddings (real, billed calls)...")
    records = []
    for i, chunk in enumerate(chunks, start=1):
        embedding = embed(_embed_text(chunk))
        records.append({"id": chunk.id, "embedding": embedding})
        print(f"  [{i}/{len(chunks)}] {chunk.id} ({len(embedding)}-dim)")

    with EMBEDDINGS_PATH.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    print(f"Wrote {EMBEDDINGS_PATH}")

    if INDEX_DIR.exists():
        shutil.rmtree(INDEX_DIR)
    INDEX_DIR.mkdir(parents=True)

    client = chromadb.PersistentClient(path=str(INDEX_DIR))
    # Explicit, not Chroma's raw default (squared L2) -- cosine distance
    # (0 = identical, 2 = opposite) is what `chroma_retriever.py`'s
    # `similarity = 1.0 - distance` assumes to produce a real cosine
    # similarity in [-1, 1], not just a same-ranking-order stand-in.
    collection = client.create_collection(_COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
    collection.add(
        ids=[r["id"] for r in records],
        embeddings=[r["embedding"] for r in records],
        documents=[_embed_text(c) for c in chunks],
        metadatas=[{"title": c.title, "topic": ",".join(c.topic), "source_name": c.source_name} for c in chunks],
    )
    print(f"Built Chroma index at {INDEX_DIR} ({collection.count()} vectors)")


if __name__ == "__main__":
    main()
