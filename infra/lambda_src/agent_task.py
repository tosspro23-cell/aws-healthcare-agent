"""Step Functions Task Lambda for the async orchestration path (Phase 3).

Input shape (from the state machine): {"run_id": ..., "user_id": ...,
"question": ...}. Returns {"answer": ..., "safe": ..., "trace": {...}} on
success.

Deliberately lets exceptions propagate rather than catching them into a
JSON error response the way `adapter.py` does for its synchronous HTTP
path. Here, the *state machine* is the error-handling boundary: an
unhandled exception becomes a Lambda-reported task failure, which is what
the state machine's Retry/Catch blocks are designed to see and act on (see
`../stacks/orchestration_stack.py`). If this handler swallowed errors into
a 200-shaped response the way an HTTP handler would, Step Functions would
never know a failure happened and the whole point of Phase 3 -- native
retry/timeout/catch semantics -- would be silently defeated.
"""

from __future__ import annotations

from agent_runtime import agent as _agent


def handler(event: dict, context: object) -> dict:
    run_id = event["run_id"]
    user_id = event["user_id"]
    question = event["question"]

    response = _agent.ask(user_id=user_id, question_text=question, question_id=run_id)

    return {
        "answer": response.answer,
        "safe": response.safe,
        "trace": response.trace.as_dict(),
    }
