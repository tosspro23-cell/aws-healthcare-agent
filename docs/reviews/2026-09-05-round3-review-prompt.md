# Independent review prompt (round 3)

Paste everything below the line to the reviewing model.

---

You are doing a third independent, skeptical review of this repository (a
healthcare-agent AWS deployment, comparison project against an Azure
counterpart). Two earlier rounds already happened:

- **Round 1** (commit `36a48a8`) found 15 structural findings -- an
  authorization vulnerability, a non-atomic run-record data model,
  exploitable numeric-grounding/diagnosis/dosing safety-check gaps,
  overly-broad IAM, an incorrect JWT-token-type ADR, missing `run_id`
  validation, stale tests, a hardcoded account ID, and more.
- **Round 2** (commit `3985ce4`) was scoped as *verification*, not a
  re-scan: it checked whether round 1's fixes actually closed what they
  claimed, and found that several had introduced real regressions (a
  safety-check fix that broke ordinary trend/eGFR answers, a sync-cancel
  race, a missing IAM permission, a compensating-write race, an
  S3-write-ordering bug) -- all confirmed and fixed, plus two items
  deliberately left open as documented backlog (see below).

Full disposition of both rounds, with fix descriptions and
live-verification notes for every finding:
[`docs/INDEPENDENT_REVIEW_FINDINGS.md`](../INDEPENDENT_REVIEW_FINDINGS.md).
Full ADR-style reasoning for every decision since: [`docs/DECISIONS.md`](../DECISIONS.md)
(newest entries at the top -- everything from round 2 onward is new
since the last review and hasn't been independently checked).

Repo: https://github.com/tosspro23-cell/aws-healthcare-agent
Current HEAD: `3f9b091`

## What's new since round 2 (all unreviewed)

A lot has been built and live-patched since the last review, based on
the repo owner's own hands-on testing of a newly-built frontend. None of
it has had an independent look yet:

1. **An entire browser frontend** (`frontend/`) -- a React/Vite
   "Workbench" that didn't exist at round 2. Covers real Authorization
   Code + PKCE login against Cognito, all three backend paths
   (`/ask`, `/runs` polling+cancel, `/jobs` polling), client-side run
   history (localStorage), and a debug view of rejected/fallback LLM
   drafts. Key files: `frontend/src/auth.ts` (PKCE implementation),
   `frontend/src/api.ts`, `frontend/src/components/AskForm.tsx` (the
   polling/cancel logic), `frontend/src/history.ts`.
2. **New public hosting infrastructure** -- `infra/stacks/frontend_stack.py`
   (S3 + CloudFront + Origin Access Control), deployed live at
   **https://d355ijp67vmjbx.cloudfront.net**. This is a real deployed
   environment, not a sandbox -- please don't attempt to actually
   complete a login (self-sign-up is now disabled and you won't have
   credentials), but do feel free to probe the *edges*: confirm
   self-sign-up is actually blocked (not just hidden from the Hosted UI),
   check whether the S3 bucket is reachable directly (it shouldn't be),
   check the CORS behavior from an arbitrary origin, and generally treat
   it as a real target worth testing, not just reading code for.
   `infra/app.py`'s module docstring explains the two-pass deploy
   mechanism (`CARE_AGENT_WORKBENCH_URL`) that registers this domain with
   Cognito and API Gateway CORS.
3. **Several rapid live-hotfixes**, each found by the repo owner
   hands-on-testing the new frontend against the real deployed backend,
   not from static review -- each documented in `docs/DECISIONS.md`
   (search for "2026-09-05"), but worth verifying independently rather
   than trusting the write-up:
   - `intent.py`: a keyword-classifier fix (a bare "vitamin" mention no
     longer force-overrides trend/priority intent) and new red-flag
     patterns for emergency headache phrasings.
   - `safety.py`: `verify_numeric_grounding` now accepts the original
     `question_text` and exempts a bare (no-unit) number if the caller's
     own question already used it -- added because a model correctly
     *declining* to fabricate a number was being penalized for echoing a
     number the user introduced. Verify this doesn't reopen a bypass: can
     a crafted question smuggle a number into the answer that *should*
     have been rejected?
   - `reasoning.py` / `narrator/mock_narrator.py`: wording naturalization
     fixes, including a personalization summary that's now built
     conditionally from which questionnaire modifiers actually fired
     (previously hardcoded regardless of trigger -- verify this is
     actually correct now, not just less obviously wrong).
   - `cancel_run.py`: two 409 responses were using `"message"` instead of
     the `"error"` key every other endpoint uses.
   - `get_run.py`: now merges a full grounding trace from S3 for the
     async paths (`agent_task.py`/`process_job.py` also write evidence
     there now, matching `adapter.py`). A live bug was found and fixed
     here: the handler is deliberately granted only `s3:GetObject`, not
     `s3:ListBucket`, and S3 returns `AccessDenied` (not `NoSuchKey`/404)
     for a missing key without `ListBucket` -- this crashed the whole
     endpoint with a 500 until fixed to tolerate any S3 read failure for
     this specific best-effort enrichment. Verify the fix's blast radius
     is actually as narrow as claimed (i.e. it doesn't swallow errors
     that *should* surface).

## What to do

1. **First-time review of the frontend** (item 1 above) -- nobody has
   looked at this code yet. In particular: is the PKCE implementation in
   `auth.ts` correct (code_verifier generation/storage, state handling or
   lack thereof, token storage)? Does `api.ts`/`AskForm.tsx`'s polling and
   error handling have gaps? Is anything sensitive ever logged to the
   console or stored insecurely?
2. **First-time review of the public hosting security posture** (item 2)
   -- test it live, not just read the CDK code. Is self-sign-up really
   blocked at the Cognito API level? Is the S3 bucket really
   unreachable directly? Does CORS actually reject an arbitrary
   third-party origin, or only omit the allow-origin header (different
   security properties)?
3. **Verify, don't trust, the round-2-to-now hotfixes** (item 3) -- each
   was reproduced and fixed under real time pressure while the owner was
   actively testing; that's exactly the condition under which round 2
   found regressions in round 1's fixes. Look especially hard at the
   `safety.py` question-echo exemption and the `get_run.py` broad
   exception handling, both described above with the specific bypass/
   blast-radius questions worth checking.
4. **Do not re-flag** the two items already tracked as deliberately open
   in `docs/INDEPENDENT_REVIEW_FINDINGS.md`: the cross-marker value/unit
   binding gap (two markers sharing a unit, e.g. LDL/HDL/triglycerides
   all in mg/dL, can still be swapped without detection) and the SQS
   processing-lease/reconciliation gap (overlapping deliveries, no DLQ
   reconciliation). Both need a real design change, not a quick patch,
   and are already accepted as backlog -- but you're welcome to push back
   if you think either risk is being underestimated.
5. **Look at whatever else seems worth looking at.** The above is what
   the repo owner knows is new/risky; a third independent pass is most
   valuable precisely for what isn't on this list.

## Report format

For each finding, classify it as `REGRESSION` (a hotfix broke something),
`INCOMPLETE FIX` (a hotfix doesn't fully close what it claims to), `NEW`
(something in the frontend/hosting/older code nobody has flagged before),
or `DOCS` (a documentation-accuracy issue). Give severity (High/Medium/
Low), the specific file/line, a concrete reproduction or reasoning path,
and what a correct fix would look like. If something you checked turns
out to be solid, say so explicitly -- a review that only ever finds
problems is as suspicious as one that finds none.
