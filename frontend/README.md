# Care Agent Workbench

Phase 6 of [`docs/AWS_ROADMAP.md`](../docs/AWS_ROADMAP.md): a minimal
browser UI over the deployed AWS backend, so the operations this project
has so far only exercised from a terminal (login, ask a question, inspect
the grounding trace and safety checks) are visible and usable, not just
provable via curl/pytest.

Sign in through a real browser redirect to the Cognito Hosted UI
(Authorization Code + PKCE -- the actual flow, not
`infra/scripts/get_dev_token.py`'s local script standing in for one),
then ask a question against any of the three backend paths from one form
(a mode switcher at the top): synchronous `POST /ask` (full grounding
trace -- safety checks, grounded facts, sources), the Step-Functions-
orchestrated `POST /runs` (polled via `GET /runs/{run_id}` until
terminal, cancellable while pending), or the SQS-buffered `POST /jobs`
(same polling). A client-side run history (localStorage) lets any past
run be revisited by `run_id` after a reload.

Only the synchronous path shows a full grounding trace -- the async
paths' DynamoDB records only ever carry
`answer`/`safe`/`narrator_backend`, not safety checks or grounded facts,
so that's genuinely all there is to show for them today. Markdown
rendering for LLM prose and real hosting (beyond a local dev server)
aren't built -- see `docs/AWS_ROADMAP.md`'s Phase 6 section for the full
list of what's still open.

## Setup

```bash
cd frontend
npm install
cp .env.example .env.local
```

Fill in `.env.local` with the real deployed values (the example file has
the commands to fetch them):

- `VITE_COGNITO_DOMAIN` / `VITE_COGNITO_APP_CLIENT_ID` -- from
  `CareAgentAuthStack`'s CloudFormation outputs.
- `VITE_API_BASE_URL` -- from `CareAgentApiStack`'s `ApiUrl` output.

## Run

```bash
npm run dev
```

Opens on `http://localhost:8765` -- **this exact port is required**, not
a convenience default: it's the one redirect URI
(`http://localhost:8765/callback`) already registered on the Cognito App
Client in `infra/stacks/auth_stack.py`. Running on a different port needs
a matching CDK change (adding another `callback_urls` entry) and redeploy
first.

The deployed API Gateway also needs CORS configured for this origin
specifically (`infra/stacks/api_stack.py`'s `cors_preflight`) -- already
wired in, but if you change the dev port, that needs updating too.

## Why this shape

- **No client secret, no server-side session** -- the Cognito App Client
  is a public client (see `auth_stack.py`), so the access token lives in
  `sessionStorage` only, the same trust model as any SPA calling an API
  directly with a bearer token. This matches the CLI tooling's own model
  (`get_dev_token.py` hands you a token to export, not a server session).
- **One demo user, shown not hidden** -- the shipped sample dataset only
  has data for `user_demo_001`; the form pre-fills it and lets you see/
  change it, rather than hardcoding it invisibly.
- **No framework beyond React + Vite** -- no router library (a single
  `pathname === "/callback"` check is enough for the one extra route this
  needs), no state-management library, no component library. Matches the
  kernel/infra's own "no dependency beyond what's actually needed"
  approach.
