#!/usr/bin/env python3
"""Local, unauthenticated demo/test server for the Workbench's V2
(compound-reasoning) UI -- **never deployed**, no Cognito, no relation to
the real `CareAgentApiStack`/`infra/lambda_src/*` production path.

Why this exists: the production frontend (`frontend/src/api.ts`) only
ever talks to the real, deployed, Cognito-authenticated AWS API. Testing
V2 (`agent.ask_compound`) end to end in a real browser -- specifically,
watching tool-calling *progress* rendered live instead of a single long
silent wait -- needs *some* HTTP surface for the frontend to call. Real
AWS deployment (a new Lambda handler, an API Gateway route, IAM changes,
a real `cdk deploy`) is a separate, deliberate, costed decision this
project's own established pattern reserves for a later, explicit step
(see e.g. Stage B's "real spend starts here" markers in docs/DECISIONS.md)
-- not something to fold silently into a demo/test pass. This script is
the cheap, local-only alternative that answers the actual question asked
("can I see this work in a browser") without that cost or risk.

Install: `pip install -e ".[devserver]"` (fastapi/uvicorn, not part of
the default install or `dev` extra -- this project's core tests never
need them).

Run: `AWS_PROFILE=dev python scripts/dev_server.py` (real Bedrock calls
for both the tool-calling planner and narration -- real, small AWS cost,
same tier as this project's other `scripts/*.py` real-call scripts).

CORS is restricted to the Vite dev server's own fixed origin
(`http://localhost:8765`, see `frontend/vite.config.ts`'s own comment on
why that port is fixed) -- this script has no reason to accept requests
from anywhere else.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from care_agent.agent import HealthAgent
from care_agent.narrator.bedrock_narrator import BedrockNarrator
from care_agent.tool_planner import BedrockToolPlanner

app = FastAPI(title="care-agent dev server (local only, not for production)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8765"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# One shared agent, one real Bedrock-backed narrator -- matches how this
# project already demos real Bedrock narration elsewhere (see README's
# "Live demo" section). `MockNarrator` is deliberately not used here: the
# whole point of this server is showing V2's real behavior, including
# real narration latency, in the browser.
_agent = HealthAgent(narrator=BedrockNarrator())


def _response_to_dict(response) -> dict[str, Any]:
    return {"run_id": str(uuid.uuid4()), "answer": response.answer, "safe": response.safe, "trace": response.trace.as_dict()}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask")
def ask(body: dict[str, Any]) -> dict[str, Any]:
    """V1, synchronous -- for side-by-side comparison against V2 in the
    same demo session."""
    response = _agent.ask(user_id=body["user_id"], question_text=body["question"], persona=body.get("persona", "patient"))
    return _response_to_dict(response)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/ask_compound/stream")
def ask_compound_stream(user_id: str, question: str, persona: str = "patient") -> StreamingResponse:
    """V2, streamed as Server-Sent Events -- a `stage` event each time
    `ask_compound`'s `on_stage` callback fires (planning, tool calls,
    narration), then one `done` event with the final result, or one
    `error` event on failure. No token-level streaming (this project's
    process is a small, fixed number of coarse stages, not a long
    autonomous loop -- see docs/DECISIONS.md, 2026-09-20 entry) -- but
    real progress, not a single long silent wait followed by a sudden
    result.

    Bridges `ask_compound`'s synchronous, callback-based progress
    reporting to an async streaming response with a thread + queue: the
    blocking call runs on a background thread, `on_stage` pushes each
    message onto a thread-safe queue, and this generator drains it live.
    """
    events: queue.Queue = queue.Queue()
    SENTINEL_DONE = object()

    def run() -> None:
        try:
            planner = BedrockToolPlanner()
            response = _agent.ask_compound(
                user_id=user_id,
                question_text=question,
                planner=planner,
                persona=persona,
                on_stage=lambda message: events.put(("stage", {"message": message})),
            )
            events.put(("done", _response_to_dict(response)))
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any failure must still reach the client as an SSE event, not a hung connection
            events.put(("error", {"error": str(exc)}))
        finally:
            events.put((SENTINEL_DONE, None))

    threading.Thread(target=run, daemon=True).start()

    def event_stream():
        while True:
            event, data = events.get()
            if event is SENTINEL_DONE:
                return
            yield _sse(event, data)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    print("care-agent dev server -- local only, unauthenticated, not for production.")
    print("Real Bedrock calls (planner + narrator) -- real, small AWS cost. Requires AWS_PROFILE=dev credentials.")
    uvicorn.run(app, host="127.0.0.1", port=8000)
