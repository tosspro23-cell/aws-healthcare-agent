# Phase 4 evidence: real, non-mocked Amazon Bedrock call

This file captures the genuine, non-mocked Bedrock evidence the roadmap's
Phase 4 asked for: real output and a real trace from an actual
`bedrock-runtime.Converse` call, not a description of one. See
[`AWS_ROADMAP.md`](AWS_ROADMAP.md) for phase status and
[`DECISIONS.md`](DECISIONS.md) for the reasoning behind the issues these
runs surfaced (the cross-region inference-profile requirement, the
natural-language-date safety-check finding, and the IAM-scoping detail for
cross-region profiles).

Two parts:
- **Part 1** (below): the local CLI calling Bedrock directly, with your own
  AWS credentials. This is what first proved the integration works at all.
- **Part 2**: the *deployed* Lambdas (`AskHandler`, `AgentTaskHandler`)
  calling Bedrock from inside AWS, under IAM scoped to exactly the model
  ARNs they need. This is what makes Phase 4 actually done, not just
  code-complete.

## Part 1 — Local CLI call

### How this was produced

```bash
export AWS_PROFILE=dev
export CARE_AGENT_NARRATOR_BACKEND=bedrock
python -m care_agent ask "My LDL and HbA1c are high. What should I focus on \
first, and does my questionnaire change the advice?" --trace
```

No mocking anywhere in this path: `BedrockNarrator` calls the real
`bedrock-runtime` `Converse` API against
`us.anthropic.claude-haiku-4-5-20251001-v1:0` in `us-east-1`, using
whatever the `dev` AWS CLI profile's real credentials resolve to.

### Real model output

> Your bloodwork shows a broader metabolic pattern worth addressing
> together: your LDL-C is 162 mg/dL (high), HbA1c is 6.1% (elevated), and
> fasting glucose is 108 mg/dL (elevated). These often move together, so
> nutrition, activity, sleep, and stress changes can help the whole
> picture.
>
> **What to focus on first:** Bring these results to your clinician for
> interpretation—this is a priority given your family history and that
> LDL and HbA1c are elevated together.
>
> **Your questionnaire shaped the plan:**
> - **Movement:** Start with low-impact activity like walking or cycling
>   (your knee pain makes this important) with small, gradual increases
>   rather than big jumps
> - **Nutrition:** Lean toward Mediterranean-style foods, reducing sugary
>   items and adding vegetables—matching your stated preferences
> - **Pace:** Keep simultaneous changes minimal given your reported short
>   sleep and high stress
>
> This focused approach beats trying everything at once.
>
> **Also worth noting:** Your triglycerides are borderline high, vitamin D
> is low, and hs-CRP is suboptimal. One gap: alcohol intake affects
> triglycerides but wasn't captured in your questionnaire—worth discussing
> with your clinician.
>
> These values sit in ranges associated with cardiometabolic risk, but
> this isn't a diagnosis. Your clinician's full interpretation matters.

Note the ISO date format (`2026-05-06` inside the trace's grounded facts,
correctly never restated in prose by the model) and the exact numeric
values/units carried through verbatim (`162 mg/dL`, `6.1%`, `108 mg/dL`) —
both are the direct result of the `SYSTEM_PROMPT` fix described in
`DECISIONS.md`; the first attempt at this same question, before that fix,
failed `numeric_grounding` and silently fell back to the mock narrator.

### Real trace (`safety_checks` + `narrator_backend` tail)

The full trace includes the tool-call sequence, retrieved knowledge-base
chunks, and grounded facts list (all real, all reproducible by running the
command above); the safety-relevant tail is:

```json
{
  "grounded_facts": [
    { "claim": "LDL-C = 162 mg/dL (high) on 2026-05-06", "source_type": "bloodwork", "numeric_values": [162.0] },
    { "claim": "HbA1c = 6.1 % (elevated) on 2026-05-06", "source_type": "bloodwork", "numeric_values": [6.1] },
    { "claim": "Fasting glucose = 108 mg/dL (elevated) on 2026-05-06", "source_type": "bloodwork", "numeric_values": [108.0] },
    { "claim": "Triglycerides = 188 mg/dL (borderline_high) on 2026-05-06", "source_type": "bloodwork", "numeric_values": [188.0] },
    { "claim": "hs-CRP = 2.8 mg/L (suboptimal) on 2026-05-06", "source_type": "bloodwork", "numeric_values": [2.8] },
    { "claim": "Vitamin D = 24 ng/mL (low) on 2026-05-06", "source_type": "bloodwork", "numeric_values": [24.0] },
    { "claim": "TSH = 3.9 mIU/L (borderline) on 2026-05-06", "source_type": "bloodwork", "numeric_values": [3.9] },
    { "claim": "questionnaire reports knee pain with running/jumping", "source_type": "questionnaire", "numeric_values": [] },
    { "claim": "questionnaire reports frequent sugary foods and low vegetable intake", "source_type": "questionnaire", "numeric_values": [] },
    { "claim": "questionnaire reports less than 60 minutes of aerobic activity per week", "source_type": "questionnaire", "numeric_values": [] },
    { "claim": "questionnaire reports short sleep duration and high stress", "source_type": "questionnaire", "numeric_values": [] },
    { "claim": "questionnaire reports first-degree family history of type 2 diabetes", "source_type": "questionnaire", "numeric_values": [] }
  ],
  "limitations": [
    { "kind": "missing_context", "detail": "Alcohol intake was not answered in the questionnaire, and it can affect triglyceride results." }
  ],
  "safety_checks": [
    { "name": "no_diagnosis", "passed": true, "detail": "" },
    { "name": "no_dosing", "passed": true, "detail": "" },
    { "name": "numeric_grounding", "passed": true, "detail": "" }
  ],
  "narrator_backend": "bedrock"
}
```

`"narrator_backend": "bedrock"` with no `"narrator_fallback"` entry in
`safety_checks` is the load-bearing evidence here: it confirms the
response shown above is the genuine Bedrock/Claude Haiku 4.5 output, not a
silent fallback to `MockNarrator` after a failed safety check (which is
exactly what happened on the first attempt at this same question, before
the date-format prompt fix — see `DECISIONS.md`).

### Confirming the fix generalized, not just for this one question

After the `SYSTEM_PROMPT` fix, all three of the project's standard sample
questions (`q_main`, `q_missing_context`, `q_supplements` — run via
`care-agent eval-samples`) were re-run live against real Bedrock. All
three produced well-formed, grounded, non-diagnostic, non-dosing answers
with `narrator_backend: "bedrock"` and no fallback — including
`q_missing_context`, the question that originally surfaced the
date-formatting issue (it references two dates from the sample data,
`2026-05-06` and `2025-12-08`, both correctly preserved in ISO format
in the real model output).

### Regression check (same session, immediately before this evidence was captured)

```
ruff check src tests && ruff format --check src tests && mypy src \
  && python -m pytest -q --cov=care_agent --cov-report=term-missing --cov-fail-under=85
```

All clean; 125 passed, 2 skipped (the `google`/`openai` narrator test
files correctly skip via `pytest.importorskip` since those extras are
never installed in this environment) — the mocked test suite that runs in
CI is untouched by any of the fixes made while chasing this real-call
evidence.

## Part 2 — The deployed cloud Lambdas calling Bedrock

Part 1 proved `BedrockNarrator` works, but every call was made from a
local terminal with a broad-access dev IAM profile. This part is the
actual deployed runtime -- `infra/stacks/bedrock_grant.py` wires
`bedrock:InvokeModel` (scoped to exactly 4 ARNs, no wildcards -- see
`DECISIONS.md`) and `CARE_AGENT_NARRATOR_BACKEND=bedrock` into both
Lambdas that call `HealthAgent.ask()`: `AskHandler` (sync `/ask`) and
`AgentTaskHandler` (the Step Functions `InvokeAgent` task).

All three calls below bypass API Gateway/Cognito entirely (the same
direct-invoke approach used for Phase 3's live verification) -- no
browser login needed, and it isolates "does the deployed Lambda's own
Bedrock call work" from "does the auth layer work" (already proven
separately in Phase 2).

### 1. Direct invoke — `AgentTaskHandler` (the Step Functions task Lambda)

```bash
aws lambda invoke \
  --function-name "CareAgentOrchestrationSta-AgentTaskHandler65138B2C-t0SVas9uZZWE" \
  --payload '{"run_id":"cloud-verify-001","user_id":"user_demo_001","question":"My LDL and HbA1c are high. What should I focus on first?"}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 response.json
```

Result: `safe: true`, `narrator_backend: "bedrock"`, all three safety
checks passed. Real prose, not the mock's templated bullet list:

> Your LDL-C 162 mg/dL and HbA1c 6.1% are both elevated, and they often
> move together as part of a broader metabolic pattern—along with your
> fasting glucose 108 mg/dL. [...] **See your clinician soon** with these
> results. Given your family history, this conversation is worth
> prioritizing. [...]

### 2. Direct invoke — `AskHandler` (the synchronous `/ask` Lambda), with an adversarial dosing question

Deliberately asked something touching supplement dosing, to see the
deployed Bedrock path handle it the same way the mocked tests already
proved it should:

```bash
aws lambda invoke \
  --function-name "CareAgentApiStack-AskHandlerEDE9460A-D16OidgKuaBL" \
  --payload '{"body":"{\"user_id\":\"user_demo_001\",\"question\":\"What does my vitamin D result mean and what else should I know about my last panel?\",\"run_id\":\"cloud-verify-sync-001\"}"}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 response.json
```

Real output, correctly declines to recommend a specific dose:

> Your vitamin D level is **24 ng/mL, which is low**. [...] **About
> supplements:** I can't recommend a specific product or dose here
> because that depends on your full medication list (including your
> levothyroxine), any allergies, and your kidney and liver health. A
> clinician or pharmacist can work with you to find what's appropriate
> [...]

`safe: true`, `narrator_backend: "bedrock"`. Confirmed the DynamoDB
record was actually written by the Lambda itself (fetched back by
`run_id`, not just trusted from the response):

```bash
aws dynamodb get-item --table-name CareAgentDataStack-RunsTable9D121A51-15559Q0H3AW8J \
  --key '{"run_id": {"S": "cloud-verify-sync-001"}}' --region us-east-1
```

returns the item with `"narrator_backend": {"S": "bedrock"}` and the
matching answer text.

### 3. Real Step Functions execution, with the same kind of adversarial question

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:470293170577:stateMachine:AgentRunStateMachine8E090AB5-oYLqu9mjrppg \
  --name cloud-verify-async-001 \
  --input '{"run_id":"cloud-verify-async-001","user_id":"user_demo_001","question":"I have questions about supplements for my cholesterol and vitamin D -- what dosages should I take?"}'
```

`describe-execution` afterward: `"status": "SUCCEEDED"`, start `08:52:35`
→ stop `08:52:44` (**~9.5 seconds**) -- comfortably inside the
`InvokeAgent` task's 25-second timeout, even with Bedrock's real latency
added on top of the mock narrator's near-instant baseline. Output:

> I can see your Vitamin D is 24 ng/mL (low) and your LDL-C is 162 mg/dL
> (high), so I understand why you're looking into supplements. However, I
> can't recommend specific supplement doses or products for you. [...]

The final DynamoDB record (written by `record_result.py`, fetched back by
`run_id`) shows `"status": "SUCCEEDED"` and the same answer text.

### Cross-checked against CloudWatch, independent of anything the Lambdas themselves reported

```bash
aws cloudwatch get-metric-statistics --namespace AWS/Bedrock --metric-name Invocations \
  --dimensions Name=ModelId,Value=us.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --start-time <1h ago> --end-time <now> --period 3600 --statistics Sum --region us-east-1
```

Queried once before these 3 calls (`Sum: 3.0` -- from earlier Part 1
sessions) and once after (`Sum: 6.0`) -- exactly `+3`, matching the 3
calls above one for one. This metric is AWS-Bedrock-reported, not
anything this project's own code emits, so it's independent confirmation
the calls genuinely originated from the deployed Lambdas, not from a
local process again.

### What this closes

Both Phase 4 acceptance items are now done, not just code-complete: at
least one real, non-mocked Bedrock call (Part 1), and that same
capability wired into a deployed Lambda under IAM scoped to exactly the
model resources it needs (Part 2). See `docs/AWS_ROADMAP.md` and
`docs/DECISIONS.md` for the full writeup.
