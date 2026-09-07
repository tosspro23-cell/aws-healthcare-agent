"""Optional local vector-retrieval backend, backed by Chroma.

Off by default. Enable with ``CARE_AGENT_RETRIEVER_BACKEND=chroma``;
otherwise the agent always uses the dependency-free BM25 retriever
(``bm25_retriever.py``). Nothing in this file is required for this
project's "no paid API" default path or for the default test suite.

Chosen over FAISS specifically because it's pure Python + SQLite
persistence -- it fits this project's existing Lambda packaging model
(``infra/build_lambda_asset.py`` does a plain ``shutil.copytree``, no
``pip install``/Docker bundling step) far better than FAISS's compiled
``hnswlib``-backed wheel would. **This backend is still local/CLI-only
for now, not wired into any deployed Lambda** -- ``chromadb`` itself
still pulls in compiled dependencies (``hnswlib`` via a platform-specific
wheel) that the current flat-copy packaging model can't handle without
new work (see ``docs/DECISIONS.md``); extending ``build_lambda_asset.py``
to do a real ``pip install --platform ...`` step is left as documented
future work, not attempted here.

The vector index itself (``data/vector_index/``, a committed Chroma
persistent-client directory) and its embeddings (``data/knowledge_base_
embeddings.jsonl``) are precomputed offline by ``scripts/build_vector_
index.py`` -- this class never calls Bedrock to embed the corpus, only
to embed the live query text (a single small call per request), the
same "one paid model call the deployed app is already allowed to make"
policy every other cloud-backed component in this project follows.

IAM (if this is ever wired into a deployed Lambda): the caller would
need `bedrock:InvokeModel` scoped to the specific embedding model ARN,
mirroring `infra/stacks/bedrock_grant.py`'s existing narrator grant --
never a broad `bedrock:*` permission.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from care_agent.models import KnowledgeChunk, RetrievedChunk
from care_agent.retrieval.bm25_retriever import DEFAULT_KB_PATH, load_knowledge_base

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_VECTOR_INDEX_DIR = _REPO_ROOT / "data" / "vector_index"
EMBEDDINGS_PATH = _REPO_ROOT / "data" / "knowledge_base_embeddings.jsonl"
_COLLECTION_NAME = "knowledge_base"

# Confirmed live against this account (2026-09-06, a real `bedrock-runtime
# invoke-model` call): request shape `{"inputText": ..., "dimensions": ...,
# "normalize": true}`, response has an `embedding` field (list[float]) --
# see docs/DECISIONS.md for the full confirmation. `EMBEDDING_DIMENSIONS`
# must match whatever `scripts/build_vector_index.py` used to build the
# committed index; changing one without the other silently breaks
# similarity search (mismatched vector lengths), so both live here as the
# one shared source of truth for both scripts.
DEFAULT_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 512
DEFAULT_REGION = "us-east-1"

# Chroma's `PersistentClient` may attempt to write lock/WAL files on open
# even for read-mostly access, and the committed `data/vector_index/` is
# both version-controlled (never mutate it as a side effect of opening it
# for reads) and, once bundled into a Lambda, read-only at runtime
# (`/var/task`). Copy it to a writable scratch directory before opening,
# unconditionally -- correct locally too, not just under Lambda. Cached
# per source directory so repeated construction in the same process
# (tests, a REPL, a warm Lambda) doesn't recopy every time.
_writable_copy_cache: dict[str, Path] = {}


def _writable_index_copy(index_dir: Path | str) -> Path:
    index_dir = Path(index_dir)
    cache_key = str(index_dir)
    cached = _writable_copy_cache.get(cache_key)
    if cached is not None and cached.exists():
        return cached
    writable_dir = Path(tempfile.mkdtemp(prefix="care_agent_vector_index_"))
    shutil.copytree(index_dir, writable_dir, dirs_exist_ok=True)
    _writable_copy_cache[cache_key] = writable_dir
    return writable_dir


def _bedrock_embed_fn(model_id: str | None = None, region: str | None = None) -> Callable[[str], list[float]]:
    try:
        import boto3  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only when extra installed
        raise RuntimeError(
            "The 'boto3' package is not installed. The chroma retriever's default embedding "
            "function needs it to call Bedrock Titan Embeddings. Install with `pip install .[bedrock]` "
            "or pass an explicit embed_fn= to ChromaRetriever, or unset CARE_AGENT_RETRIEVER_BACKEND "
            "to use the default bm25 retriever."
        ) from exc

    resolved_region = region or os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION", DEFAULT_REGION)
    client = boto3.client("bedrock-runtime", region_name=resolved_region)
    resolved_model_id = model_id or os.environ.get("BEDROCK_EMBEDDING_MODEL_ID", DEFAULT_EMBEDDING_MODEL_ID)

    def embed(text: str) -> list[float]:
        body = json.dumps({"inputText": text, "dimensions": EMBEDDING_DIMENSIONS, "normalize": True})
        response = client.invoke_model(modelId=resolved_model_id, contentType="application/json", accept="application/json", body=body)
        payload = json.loads(response["body"].read())
        return payload["embedding"]

    return embed


class ChromaRetriever:
    """Semantic retrieval over the same 68-chunk corpus, via a local Chroma index."""

    backend_name = "chroma"

    # Query more candidates than top_k so the topic-tag boost below has
    # room to reorder results, mirroring KnowledgeRetriever's own
    # tag-boost behavior (retrieval/bm25_retriever.py's TAG_BOOST) even
    # though the underlying ranking signal here is cosine similarity, not
    # BM25 -- topic_filter's *effect* (boost matching chunks, don't hard-
    # exclude the rest) should feel the same regardless of backend.
    _CANDIDATE_MULTIPLIER = 3
    TAG_BOOST = 0.15

    def __init__(
        self,
        kb_path: Path | str = DEFAULT_KB_PATH,
        index_dir: Path | str = DEFAULT_VECTOR_INDEX_DIR,
        embed_fn: Callable[[str], list[float]] | None = None,
    ):
        try:
            import chromadb  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only when extra installed
            raise RuntimeError(
                "The 'chromadb' package is not installed. Install with `pip install .[chroma]` "
                "or unset CARE_AGENT_RETRIEVER_BACKEND to use the default bm25 retriever."
            ) from exc

        self.chunks = load_knowledge_base(kb_path)
        self._chunks_by_id: dict[str, KnowledgeChunk] = {c.id: c for c in self.chunks}
        self._embed = embed_fn or _bedrock_embed_fn()

        writable_dir = _writable_index_copy(index_dir)
        client = chromadb.PersistentClient(path=str(writable_dir))
        self._collection = client.get_collection(_COLLECTION_NAME)

    def retrieve(self, query: str, top_k: int = 6, topic_filter: set[str] | None = None) -> list[RetrievedChunk]:
        if not query.strip() or not self.chunks:
            return []

        query_embedding = self._embed(query)
        n_results = min(len(self.chunks), max(top_k * self._CANDIDATE_MULTIPLIER, top_k))
        results = self._collection.query(query_embeddings=[query_embedding], n_results=n_results)

        normalized_filter = {t.replace("_", "-") for t in topic_filter} if topic_filter else set()

        scored: list[tuple[float, KnowledgeChunk, list[str]]] = []
        for chunk_id, distance in zip(results["ids"][0], results["distances"][0], strict=True):
            chunk = self._chunks_by_id.get(chunk_id)
            if chunk is None:  # pragma: no cover - defensive, index and KB are built together
                continue

            # The committed index is built with hnsw:space=cosine (see
            # scripts/build_vector_index.py), so `distance` is cosine
            # distance (0=identical, 2=opposite) and this is a real cosine
            # similarity in [-1, 1] -- not compared numerically against
            # BM25's own score, though (nothing downstream does either).
            similarity = 1.0 - distance
            matched_terms: list[str] = []
            if normalized_filter:
                normalized_topics = {t.replace("_", "-") for t in chunk.topic}
                overlap = normalized_topics & normalized_filter
                similarity += self.TAG_BOOST * len(overlap)
                matched_terms = sorted(overlap)

            scored.append((similarity, chunk, matched_terms))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievedChunk(chunk=chunk, score=round(similarity, 4), matched_terms=tuple(matched_terms))
            for similarity, chunk, matched_terms in scored[:top_k]
        ]

    def get_by_id(self, chunk_id: str) -> KnowledgeChunk | None:
        return self._chunks_by_id.get(chunk_id)
