"""Offline lexical retrieval over the knowledge_base.jsonl corpus.

No embedding model or paid API is required: this is a from-scratch BM25
ranker over the chunk title/content, boosted by exact matches against each
chunk's structured ``topic`` tags. BM25 + tag-boosting is a deliberate choice
for a 68-document corpus -- it is deterministic, inspectable, fast, and
trivially testable, which matters more here than marginal recall gains from
a heavier retriever.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from care_agent.models import KnowledgeChunk, RetrievedChunk

DEFAULT_KB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge_base.jsonl"

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")

_STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "as",
    "by",
    "at",
    "it",
    "this",
    "that",
    "should",
    "can",
    "may",
    "not",
    "if",
    "than",
    "then",
    "when",
    "which",
    "such",
    "into",
    "from",
    "does",
    "do",
    "did",
    "has",
    "have",
    "had",
    "will",
    "would",
}


def tokenize(text: str) -> list[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def load_knowledge_base(path: Path | str = DEFAULT_KB_PATH) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            chunks.append(
                KnowledgeChunk(
                    id=raw["id"],
                    title=raw.get("title", ""),
                    topic=tuple(raw.get("topic", [])),
                    source_name=raw.get("source_name", ""),
                    source_url=raw.get("source_url", ""),
                    content=raw.get("content", ""),
                )
            )
    return chunks


@dataclass
class _Doc:
    chunk: KnowledgeChunk
    tokens: list[str]
    term_freq: Counter
    length: int


class KnowledgeRetriever:
    """A small BM25 index with topic-tag boosting, kept dependency-free."""

    K1 = 1.5
    B = 0.75
    TAG_BOOST = 2.5  # extra score per exact topic-tag match

    def __init__(self, chunks: list[KnowledgeChunk] | None = None, kb_path: Path | str = DEFAULT_KB_PATH):
        self.chunks = chunks if chunks is not None else load_knowledge_base(kb_path)
        self._docs: list[_Doc] = []
        self._df: Counter = Counter()
        for chunk in self.chunks:
            text = f"{chunk.title} {chunk.content} {' '.join(chunk.topic)}"
            tokens = tokenize(text)
            doc = _Doc(chunk=chunk, tokens=tokens, term_freq=Counter(tokens), length=len(tokens))
            self._docs.append(doc)
            for term in set(tokens):
                self._df[term] += 1
        self._avgdl = (sum(d.length for d in self._docs) / len(self._docs)) if self._docs else 0.0
        self._n = len(self._docs)

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        # BM25+ style idf, floored at a small positive value so unseen terms
        # don't zero out a whole query.
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def retrieve(
        self,
        query: str,
        top_k: int = 6,
        topic_filter: set[str] | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        query_terms = tokenize(query)
        if topic_filter:
            query_terms += [t.replace("_", " ") for t in topic_filter]
            query_terms = tokenize(" ".join(query_terms))
        if not query_terms:
            return []

        scored: list[tuple[float, _Doc, list[str]]] = []
        for doc in self._docs:
            score = 0.0
            matched: list[str] = []
            for term in set(query_terms):
                tf = doc.term_freq.get(term, 0)
                if tf == 0:
                    continue
                idf = self._idf(term)
                denom = tf + self.K1 * (1 - self.B + self.B * doc.length / (self._avgdl or 1))
                score += idf * (tf * (self.K1 + 1)) / denom
                matched.append(term)

            if topic_filter:
                normalized_topics = {t.replace("_", "-") for t in doc.chunk.topic}
                normalized_filter = {t.replace("_", "-") for t in topic_filter}
                overlap = normalized_topics & normalized_filter
                score += self.TAG_BOOST * len(overlap)
                matched.extend(sorted(overlap))

            if score > min_score:
                scored.append((score, doc, matched))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievedChunk(chunk=doc.chunk, score=round(score, 4), matched_terms=tuple(dict.fromkeys(matched)))
            for score, doc, matched in scored[:top_k]
        ]

    def get_by_id(self, chunk_id: str) -> KnowledgeChunk | None:
        for doc in self._docs:
            if doc.chunk.id == chunk_id:
                return doc.chunk
        return None
