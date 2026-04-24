"""
MVP runtime orchestrator.

Takes a task name (matching a task type in method_sets.metta), loads the
PLN reasoner, runs the HTN planner, and prints the resulting tool chain.

Phase 1 scope: task name in, tool chain out. No NL parsing, no YAML
compilation, no online method learning — those arrive in later phases.

Usage:
    python main.py "Variant Calling"
    python main.py --alternatives 5 "RNA Analysis"
"""

import argparse

from src.pln.reasoner import PLNReasoner
from src.htn.planner import HTNPlanner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "task", nargs="?", help="Task type (e.g. 'Variant Calling')"
    )
    parser.add_argument(
        "--alternatives",
        type=int,
        default=0,
        help="Print top-K alternative methods instead of a single plan.",
    )
    parser.add_argument(
        "--list-tasks",
        action="store_true",
        help="List all task types available in the KB.",
    )
    args = parser.parse_args()

    print("Loading KB...")
    reasoner = PLNReasoner().load()
    planner = HTNPlanner(reasoner)

    if args.list_tasks:
        tasks = reasoner.list_task_types()
        print(f"\n{len(tasks)} task types in KB:")
        for t in tasks:
            print(f"  {t}")
        return

    if not args.task:
        parser.error("task is required unless --list-tasks is set")

    if args.alternatives:
        plans = planner.alternatives(args.task, top_k=args.alternatives)
        print(f"\nTop {len(plans)} methods for '{args.task}':\n")
        for i, p in enumerate(plans, 1):
            print(f"[{i}] {p.render()}\n")
        return

    plan = planner.plan(args.task)
    print(f"\n{plan.render()}")


if __name__ == "__main__":
    main()
