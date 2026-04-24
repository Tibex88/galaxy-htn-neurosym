"""
Phase 1 sanity check — HTN planner over the generated KB.

Run:  python scripts/test_htn_planner.py
"""

import sys

sys.path.insert(0, ".")

from src.pln.reasoner import PLNReasoner
from src.htn.planner import HTNPlanner


def main():
    reasoner = PLNReasoner().load()
    planner = HTNPlanner(reasoner)

    print("\n=== Task 1: Variant Calling ===")
    plan = planner.plan("Variant Calling")
    print(plan.render())
    assert plan.ok, "expected at least one method for Variant Calling"
    assert any(
        "variant" in t.lower() or "call" in t.lower() or "snp" in t.lower()
        for t in plan.tools
    ), f"variant-calling plan had no variant-related tool: {plan.tools}"

    print("\n=== Task 2: RNA Analysis — top 3 alternatives ===")
    plans = planner.alternatives("RNA Analysis", top_k=3)
    for p in plans:
        print(p.render(), "\n")
    assert plans, "expected at least one RNA Analysis method"

    print("\n=== Task 3: unknown task returns empty plan ===")
    empty = planner.plan("Fake Task That Does Not Exist")
    assert not empty.ok, "unknown task should yield empty plan"
    print(empty.render())

    print("\n=== PASS ===")


if __name__ == "__main__":
    main()
