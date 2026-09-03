#!/usr/bin/env bash
# Manual check: does the LLM narrator's rephrased text keep exact numeric
# values (not just vague labels like "elevated")? Run this after
# `ollama serve` is up and CARE_AGENT_NARRATOR_BACKEND=ollama is exported.
#
# Usage:
#   export CARE_AGENT_NARRATOR_BACKEND=ollama
#   export OLLAMA_MODEL=llama3.1   # or any pulled model
#   bash scripts/test_numeric_visibility.sh
#
# For each question below this prints the answer text and a quick grep
# check for whether the real bloodwork numbers appear in it.

set -euo pipefail

declare -a QUESTIONS=(
  "My LDL and HbA1c are high. What should I focus on first, and does my questionnaire change the advice?"
  "What's going on with my triglycerides?"
  "Is my vitamin D okay?"
  "Tell me about my hs-CRP result."
  "How does my TSH look, given I'm on levothyroxine?"
  "Can you tell me if my glucose got worse?"
  "Should I take supplements for cholesterol?"
  "Are my LDL, HDL, and triglycerides all bad?"
)

# concept_id -> the exact number that MUST appear if that marker is discussed
declare -a EXPECTED=(
  "162|6.1"     # q1: LDL-C 162, HbA1c 6.1
  "188"         # q2: triglycerides 188
  "24"          # q3: vitamin D 24
  "2.8"         # q4: hs-CRP 2.8
  "3.9"         # q5: TSH 3.9
  "108"         # q6: fasting glucose 108
  "162"         # q7: LDL-C 162 (supplement question mentions "cholesterol")
  "162"         # q8: at least LDL-C 162 should show up
)

for i in "${!QUESTIONS[@]}"; do
  q="${QUESTIONS[$i]}"
  expected="${EXPECTED[$i]}"
  echo "================================================================"
  echo "Q: $q"
  echo "----------------------------------------------------------------"
  answer="$(python -m care_agent ask "$q")"
  echo "$answer"
  echo "----------------------------------------------------------------"
  IFS='|' read -ra nums <<< "$expected"
  all_found=true
  for n in "${nums[@]}"; do
    if echo "$answer" | grep -qF "$n"; then
      echo "  [OK]   found expected number: $n"
    else
      echo "  [MISS] expected number not visible in text: $n (may still be grounded in --trace)"
      all_found=false
    fi
  done
  if [ "$all_found" = true ]; then
    echo "  => numeric visibility: PASS"
  else
    echo "  => numeric visibility: PARTIAL (not necessarily a bug -- check --trace grounded_facts)"
  fi
  echo
done
