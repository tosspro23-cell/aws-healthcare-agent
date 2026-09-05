"""Command-line entrypoint: ``python -m care_agent ask "..."``."""

from __future__ import annotations

import argparse
import json
import sys

from care_agent.agent import HealthAgent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="care-agent", description="Ask the healthcare Q&A agent a question.")
    sub = parser.add_subparsers(dest="command", required=True)

    ask_p = sub.add_parser("ask", help="Ask a single question")
    ask_p.add_argument("question", help="The user's question, in quotes.")
    ask_p.add_argument("--user-id", default="user_demo_001")
    ask_p.add_argument("--trace", action="store_true", help="Also print the full JSON execution trace.")

    eval_p = sub.add_parser("eval-samples", help="Run all sample_questions.json questions and print answers + traces.")
    eval_p.add_argument("--trace", action="store_true")

    eval_cap_p = sub.add_parser(
        "eval-capabilities",
        help="Run the capability-checked regression eval (care_agent.eval) and exit non-zero on any failure.",
    )
    eval_cap_p.add_argument("--report", metavar="PATH", help="Write the full JSON report to this path.")

    args = parser.parse_args(argv)
    agent = HealthAgent()

    if args.command == "ask":
        response = agent.ask(user_id=args.user_id, question_text=args.question)
        print(response.answer)
        if args.trace:
            print("\n--- TRACE ---")
            print(json.dumps(response.trace.as_dict(), indent=2, default=str))
        return 0

    if args.command == "eval-samples":
        from care_agent.data_store import DataStore

        for q in DataStore().get_sample_questions():
            print(f"=== {q['id']} ===")
            print(f"Q: {q['text']}")
            response = agent.ask(user_id=q["user_id"], question_text=q["text"], question_id=q["id"])
            print(f"A:\n{response.answer}")
            if args.trace:
                print("--- TRACE ---")
                print(json.dumps(response.trace.as_dict(), indent=2, default=str))
            print()
        return 0

    if args.command == "eval-capabilities":
        from care_agent.eval import run_eval, summarize, to_report_dict

        results = run_eval()
        summary = summarize(results)

        for r in results:
            status = "PASS" if r.passed else "FAIL"
            print(f"[{status}] {r.question_id} (narrator: {r.narrator_backend})")
            for c in r.results:
                if not c.passed:
                    print(f"    FAIL {c.capability}: {c.detail}")
            for skipped in r.skipped:
                print(f"    SKIP {skipped} (not automatically checkable)")

        print(f"\n{summary.passed_checks}/{summary.total_checks} checks passed ({summary.pass_rate:.0%}); {summary.total_skipped} skipped.")

        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(to_report_dict(summary), f, indent=2)
            print(f"Wrote report to {args.report}")

        return 0 if summary.all_passed else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
