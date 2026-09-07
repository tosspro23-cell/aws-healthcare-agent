"""Retriever interface.

Mirrors ``care_agent.narrator.base.Narrator`` deliberately: retrieval just
gained its second real backend (BM25, the default, and an optional local
Chroma vector index -- see ``chroma_retriever.py``), for the same reason
narrator has several, so it gets the same shape: a ``Protocol`` (not an
ABC, matching how ``Narrator`` is defined) plus a ``_select_retriever()``
factory in ``agent.py`` that reads an env var and lazily imports only the
backend actually selected.

``min_score`` is deliberately not part of this Protocol -- it's a
BM25-specific tuning knob (``KnowledgeRetriever.retrieve``'s own kwarg),
and the one real call site (``HealthAgent.ask``) never passes it.
"""

from __future__ import annotations

from typing import Protocol

from care_agent.models import KnowledgeChunk, RetrievedChunk


class Retriever(Protocol):
    backend_name: str

    def retrieve(self, query: str, top_k: int = 6, topic_filter: set[str] | None = None) -> list[RetrievedChunk]: ...

    def get_by_id(self, chunk_id: str) -> KnowledgeChunk | None: ...
