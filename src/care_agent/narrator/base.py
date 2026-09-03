"""Narrator interface.

A narrator turns a fully-grounded ``Brief`` into user-facing text. It must
NOT introduce any fact -- especially any number -- that isn't already present
in ``brief.grounded_facts``. This contract is enforced after the fact by
``safety.verify_numeric_grounding`` in ``agent.py``, regardless of which
narrator implementation produced the text.
"""

from __future__ import annotations

from typing import Protocol

from care_agent.models import UserProfile
from care_agent.reasoning import Brief


class Narrator(Protocol):
    backend_name: str

    def compose(self, brief: Brief, question_text: str, profile: UserProfile) -> str: ...
