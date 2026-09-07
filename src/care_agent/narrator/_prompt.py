"""Shared system prompt (and shared user-message construction) for every
LLM-backed narrator (cloud or local).

Kept in one place so the cloud (Anthropic/OpenAI/Google/Bedrock) and local
(Ollama) backends can never drift into inconsistent instructions -- every
one of them only ever rephrases the mock narrator's already-grounded
bullet list, and this prompt is the (courtesy, not guarantee -- see each
narrator module's docstring) instruction layer for that. The real
guarantee is still `safety.run_safety_checks`, run by `agent.py` on
whatever text comes back regardless of what fed the prompt.
"""

from __future__ import annotations

from care_agent.models import RetrievedChunk

SYSTEM_PROMPT = (
    "You are a health-data explainer. You will be given a list of already-verified, "
    "grounded facts and safety constraints. Rephrase them into a clear, warm, concise answer. "
    "Rules: do not add any number, marker, or claim that is not in the provided facts. "
    "When you mention a marker that has a specific value and unit in the source facts "
    "(e.g. 'LDL-C 162 mg/dL'), state that exact value and unit rather than only a vague "
    "word like 'elevated' -- the reader should be able to see the number, not just infer it. "
    "When you mention a date from the source facts, keep it in the exact same YYYY-MM-DD "
    "format (e.g. '2026-05-06') rather than writing it out in words (e.g. not 'May 6, 2026'). "
    "Do not diagnose. Do not give supplement or medication doses. Keep it under 200 words. "
    "You may also be given reference material from a vetted knowledge base, separate from the "
    "grounded facts. Treat it as general educational background only: you may draw on its general "
    "concepts (e.g. food categories, activity types, what a marker generally means) to make your "
    "answer more specific and useful, but never state a specific number, dose, or value from that "
    "reference material as true for this user unless it already appears in the grounded facts above."
)

# Retrieval currently drives citations, not generation content, for the
# mock narrator by design (see docs/DECISIONS.md's Stage A entry) -- its
# template only ever reads a chunk's source_name/source_url, never its
# content. The gap that entry identified was specific to the LLM
# narrators: they only ever saw citation *names*, never the actual
# educational text those citations represent, so their specific
# suggestions were drawn from the model's own training knowledge rather
# than this project's curated KB. This constant is how many chunks'
# `content` actually get attached to the prompt to close that gap --
# capped, not all of them, to keep the prompt short and avoid the SYSTEM
# instructions above 200-word answer limit getting less signal than facts.
MAX_REFERENCE_CHUNKS = 3


def build_user_message(question_text: str, grounded_text: str, retrieved_chunks: list[RetrievedChunk] | None = None) -> str:
    """The user-turn content every LLM narrator sends -- identical shape
    across all five (Bedrock/Anthropic/OpenAI/Google/Ollama), previously
    duplicated in each file. Adds a labeled reference-material section
    only when there's something to add, so a `Brief` with no retrieved
    chunks (every existing narrator test) produces byte-identical output
    to before this function existed."""
    parts = [
        f"User's question: {question_text}",
        "",
        f"Grounded facts and constraints to rephrase (do not add to this list):\n{grounded_text}",
    ]
    if retrieved_chunks:
        reference_lines = [
            f"- [{rc.chunk.source_name}] {rc.chunk.title}: {rc.chunk.content}" for rc in retrieved_chunks[:MAX_REFERENCE_CHUNKS]
        ]
        parts.append(
            "\nReference material (general educational background from a vetted knowledge base -- "
            "for context only, not a new grounded fact; do not restate any specific number from here "
            "unless it already appears in the grounded facts above):\n" + "\n".join(reference_lines)
        )
    return "\n".join(parts)
