# Phase 4 evidence: real, non-mocked Amazon Bedrock call

This file captures the genuine, non-mocked Bedrock evidence the roadmap's
Phase 4 asked for: real output and a real trace from an actual
`bedrock-runtime.Converse` call, not a description of one. See
[`AWS_ROADMAP.md`](AWS_ROADMAP.md) for phase status and
[`DECISIONS.md`](DECISIONS.md) for the reasoning behind the two issues this
run surfaced (the cross-region inference-profile requirement, and the
natural-language-date safety-check finding).

## How this was produced

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

## Real model output

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

## Real trace (`safety_checks` + `narrator_backend` tail)

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

## Confirming the fix generalized, not just for this one question

After the `SYSTEM_PROMPT` fix, all three of the project's standard sample
questions (`q_main`, `q_missing_context`, `q_supplements` — run via
`care-agent eval-samples`) were re-run live against real Bedrock. All
three produced well-formed, grounded, non-diagnostic, non-dosing answers
with `narrator_backend: "bedrock"` and no fallback — including
`q_missing_context`, the question that originally surfaced the
date-formatting issue (it references two dates from the sample data,
`2026-05-06` and `2025-12-08`, both correctly preserved in ISO format
in the real model output).

## Regression check (same session, immediately before this evidence was captured)

```
ruff check src tests && ruff format --check src tests && mypy src \
  && python -m pytest -q --cov=care_agent --cov-report=term-missing --cov-fail-under=85
```

All clean; 125 passed, 2 skipped (the `google`/`openai` narrator test
files correctly skip via `pytest.importorskip` since those extras are
never installed in this environment) — the mocked test suite that runs in
CI is untouched by any of the fixes made while chasing this real-call
evidence.
