"""
Phase 2 integration test — NL query -> plan -> gxformat2 YAML.

Uses the offline keyword parser (no LLM network dependency) so the test
runs deterministically. The Gemini path is exercised manually via:

    GEMINI_API_KEY=... python main.py "call variants on exome data"
"""

import sys

sys.path.insert(0, ".")

from src.pln.reasoner import PLNReasoner
from src.htn.planner import HTNPlanner
from src.parsing.parser import QueryParser
from src.galaxy.compiler import WorkflowCompiler


def main():
    reasoner = PLNReasoner().load()
    parser = QueryParser(prefer_llm=False)
    planner = HTNPlanner(reasoner)
    compiler = WorkflowCompiler(reasoner)

    query = "call variants on paired-end human exome data"
    print(f"\n=== QUERY: {query} ===")
    parsed = parser.parse(query)
    print(parsed.render())
    assert parsed.tasks, "parser should emit at least one task"
    assert (
        "Variant Calling" in parsed.tasks
    ), f"expected Variant Calling in tasks, got {parsed.tasks}"

    report = planner.plan_tasks(parsed.tasks)
    print("\n", report.render())
    assert report.plans, "planner should produce plans"
    assert any(
        p.ok for p in report.plans
    ), "at least one plan should succeed"

    compiled = compiler.compile([p for p in report.plans if p.ok])
    print(f"\n=== COMPILED ({len(compiled.tool_ids)} tool steps) ===")
    assert compiled.yaml.startswith("class: GalaxyWorkflow")
    assert "steps:" in compiled.yaml
    assert compiled.tool_ids, "YAML must reference at least one tool"

    print(compiled.yaml[:1500])
    print("...")
    print("\n=== PASS ===")


if __name__ == "__main__":
    main()
