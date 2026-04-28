"""
Runtime orchestrator — Phase 3.

    NL query
       -> QueryParser (structured output; offline keyword fallback)
       -> HTNPlanner (per-task method selection with EDAM type checks)
       -> OnlineLearner (ChatHTN: propose->verify->persist when gap detected)
       -> WorkflowCompiler (gxformat2 YAML)

Usage:
    python main.py "call variants on paired-end human exome data"
    python main.py --task "Variant Calling"
    python main.py --task "A_Totally_New_Task" --learn
    python main.py --feedback --tools fastp,BWA-MEM,Call_variants --outcome success
"""

import argparse
import re
import time
from pathlib import Path

from src.pln.reasoner import PLNReasoner
from src.htn.planner import HTNPlanner
from src.parsing.parser import QueryParser
from src.galaxy.compiler import WorkflowCompiler
from src.htn.online_learner import OnlineLearner
from src.htn.feedback import FeedbackLearner


GENERATED_ROOT = Path(__file__).parent / "generated"


def _default_output_path(workflow_name: str) -> Path:
    """generated/<unix_timestamp>/<workflow_name>.gxwf.yml"""
    ts_dir = GENERATED_ROOT / str(int(time.time()))
    ts_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", workflow_name) or "workflow"
    return ts_dir / f"{safe_name}.gxwf.yml"


def _cmd_task(args, reasoner):
    planner = HTNPlanner(reasoner)
    if args.alternatives:
        plans = planner.alternatives(args.task, top_k=args.alternatives)
        print(f"\nTop {len(plans)} methods for '{args.task}':\n")
        for i, p in enumerate(plans, 1):
            print(f"[{i}] {p.render()}\n")
        return

    if args.learn:
        learner = OnlineLearner(reasoner, planner)
        report = learner.plan_with_learning(args.task)
        print("\n=== LEARNING REPORT ===")
        print(report.render())
        return

    plan = planner.plan(args.task)
    print(f"\n{plan.render()}")


def _cmd_query(args, reasoner):
    parser = QueryParser(prefer_llm=not args.no_llm)
    parsed = parser.parse(args.query)
    print(f"\n[1/3] PARSED\n{parsed.render()}")

    planner = HTNPlanner(reasoner)

    if args.learn:
        learner = OnlineLearner(reasoner, planner)
        plans = []
        for task in parsed.tasks or []:
            report = learner.plan_with_learning(task)
            if report.gap_detected:
                print(f"\n  [gap] filled for {task} (accepted={report.accepted})")
            if report.final_plan and report.final_plan.ok:
                plans.append(report.final_plan)
    else:
        report = planner.plan_tasks(parsed.tasks or [])
        plans = [p for p in report.plans if p.ok]
        print(f"\n[2/3] PLANNED\n{report.render()}")

    if not plans:
        print("\n[abort] no plannable tasks")
        return

    compiler = WorkflowCompiler(reasoner)
    compiled = compiler.compile(
        plans,
        workflow_name=args.name or "htn_generated_workflow",
        annotation=f"Generated from query: {parsed.raw_query!r}",
    )
    print("\n[3/3] COMPILED")
    print(f"  {len(compiled.tool_ids)} tool steps")
    if compiled.workflow_inputs:
        print(
            f"  {len(compiled.workflow_inputs)} workflow inputs: "
            f"{compiled.workflow_inputs}"
        )
    if compiled.missing_full_ids:
        print(
            f"  warning: {len(compiled.missing_full_ids)} tools missing "
            f"toolshed IDs: {compiled.missing_full_ids[:3]}..."
        )
    if compiled.unconnected_step_inputs:
        print(
            f"  warning: {len(compiled.unconnected_step_inputs)} step inputs "
            f"unconnected: {compiled.unconnected_step_inputs[:3]}..."
        )

    out_path = Path(args.out) if args.out else _default_output_path(compiled.name)
    path = compiled.write(out_path)
    print(f"  wrote gxformat2 YAML -> {path}")


def _cmd_feedback(args, reasoner):
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]
    if not tools:
        print("[feedback] no tools provided")
        return
    learner = FeedbackLearner(reasoner)
    results = learner.record_workflow(tools, args.outcome)
    print(f"\n=== FEEDBACK: {args.outcome} over {len(tools)} tools ===")
    for r in results:
        print(r.render())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="Natural-language workflow request")
    ap.add_argument("--task", help="Plan a single task type directly")
    ap.add_argument("--alternatives", type=int, default=0)
    ap.add_argument("--out", help="Write gxformat2 YAML to this path")
    ap.add_argument("--name", help="Workflow name override")
    ap.add_argument(
        "--no-llm",
        action="store_true",
        help="Force offline parser (no Gemini call)",
    )
    ap.add_argument(
        "--learn",
        action="store_true",
        help="Enable ChatHTN online method learning on gaps",
    )
    ap.add_argument(
        "--feedback",
        action="store_true",
        help="Record execution outcome (uses --tools and --outcome)",
    )
    ap.add_argument("--tools", help="Comma-separated tool names for --feedback")
    ap.add_argument(
        "--outcome", choices=["success", "failure"], help="Outcome for --feedback"
    )
    ap.add_argument("--list-tasks", action="store_true")
    args = ap.parse_args()

    print("Loading KB...")
    reasoner = PLNReasoner().load()

    if args.list_tasks:
        for t in reasoner.list_task_types():
            print(f"  {t}")
        return

    if args.feedback:
        if not args.tools or not args.outcome:
            ap.error("--feedback requires --tools and --outcome")
        _cmd_feedback(args, reasoner)
        return

    if args.task:
        _cmd_task(args, reasoner)
        return

    if args.query:
        _cmd_query(args, reasoner)
        return

    ap.error("Provide a query, --task, --feedback, or --list-tasks")


if __name__ == "__main__":
    main()
