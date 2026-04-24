"""
Runtime orchestrator — Phase 2.

    NL query
       -> QueryParser (Gemini structured output, offline keyword fallback)
       -> HTNPlanner (per-task method selection with EDAM type checks)
       -> WorkflowCompiler (gxformat2 YAML)
       -> optional file output

Usage:
    python main.py "call variants on paired-end human exome data"
    python main.py "RNA-seq differential expression in mouse" --out workflow.gxwf.yml
    python main.py --task "Variant Calling"                   # single-task shortcut
"""

import argparse

from src.pln.reasoner import PLNReasoner
from src.htn.planner import HTNPlanner
from src.parsing.parser import QueryParser
from src.galaxy.compiler import WorkflowCompiler


def _cmd_task(args, reasoner):
    planner = HTNPlanner(reasoner)
    if args.alternatives:
        plans = planner.alternatives(args.task, top_k=args.alternatives)
        print(f"\nTop {len(plans)} methods for '{args.task}':\n")
        for i, p in enumerate(plans, 1):
            print(f"[{i}] {p.render()}\n")
        return
    plan = planner.plan(args.task)
    print(f"\n{plan.render()}")


def _cmd_query(args, reasoner):
    parser = QueryParser(prefer_llm=not args.no_llm)
    parsed = parser.parse(args.query)
    print(f"\n[1/3] PARSED\n{parsed.render()}")

    planner = HTNPlanner(reasoner)
    report = planner.plan_tasks(parsed.tasks or [])
    print(f"\n[2/3] PLANNED\n{report.render()}")

    if not report.plans or not any(p.ok for p in report.plans):
        print("\n[abort] no plannable tasks")
        return

    compiler = WorkflowCompiler(reasoner)
    compiled = compiler.compile(
        [p for p in report.plans if p.ok],
        workflow_name=args.name or "htn_generated_workflow",
        annotation=f"Generated from query: {parsed.raw_query!r}",
    )
    print("\n[3/3] COMPILED")
    print(f"  {len(compiled.tool_ids)} tool steps")
    if compiled.missing_full_ids:
        print(
            f"  warning: {len(compiled.missing_full_ids)} tools missing "
            f"toolshed IDs: {compiled.missing_full_ids[:3]}..."
        )

    if args.out:
        path = compiled.write(args.out)
        print(f"  wrote gxformat2 YAML -> {path}")
    else:
        print("\n--- gxformat2 YAML ---")
        print(compiled.yaml)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="Natural-language workflow request")
    ap.add_argument("--task", help="Shortcut: plan a single task type directly")
    ap.add_argument("--alternatives", type=int, default=0)
    ap.add_argument("--out", help="Write gxformat2 YAML to this path")
    ap.add_argument("--name", help="Override workflow name")
    ap.add_argument("--no-llm", action="store_true", help="Force offline parser")
    ap.add_argument("--list-tasks", action="store_true")
    args = ap.parse_args()

    print("Loading KB...")
    reasoner = PLNReasoner().load()

    if args.list_tasks:
        for t in reasoner.list_task_types():
            print(f"  {t}")
        return

    if args.task:
        _cmd_task(args, reasoner)
        return

    if args.query:
        _cmd_query(args, reasoner)
        return

    ap.error("Provide a query, --task, or --list-tasks")


if __name__ == "__main__":
    main()
