"""
HTN Planner — minimal version.

Given a task name, look up its method set in the KB, score each alternative
method via PLN compound reliability, and return the highest-scoring method's
ordered tool sequence.

No decomposition of subtasks, no precondition checking, no backtracking.
Subsequent phases add those capabilities.
"""

from dataclasses import dataclass, field

from src.pln.reasoner import PLNReasoner


def _safe(name: str) -> str:
    """Normalize a task name to match safe-names in method_sets.metta."""
    return (
        name.replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
        .replace(".", "_")
    )


@dataclass
class Plan:
    task: str
    method_name: str
    tools: list[str] = field(default_factory=list)
    score: float = 0.0
    stvs: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.tools)

    def render(self) -> str:
        if not self.tools:
            return f"[NO PLAN] task={self.task}"
        chain = " -> ".join(self.tools)
        return (
            f"task={self.task}  method={self.method_name}  "
            f"score={self.score:.3f}\n  {chain}"
        )


class HTNPlanner:
    def __init__(self, reasoner: PLNReasoner):
        self.reasoner = reasoner

    def plan(self, task: str) -> Plan:
        """Return the best-scoring method for a task, as an ordered tool list."""
        safe_task = _safe(task)
        candidates = self.reasoner.get_methods_for_task_scored(safe_task)

        if not candidates:
            return Plan(task=safe_task, method_name="")

        best = candidates[0]
        sequence = self.reasoner.get_method_sequence(best["name"])
        tools = sequence if sequence else best.get("tools", [])

        return Plan(
            task=safe_task,
            method_name=best["name"],
            tools=tools,
            score=best["score"],
            stvs=best.get("stvs", []),
        )

    def alternatives(self, task: str, top_k: int = 5) -> list[Plan]:
        """Return the top-K scored methods for a task."""
        safe_task = _safe(task)
        candidates = self.reasoner.get_methods_for_task_scored(safe_task)[:top_k]
        plans = []
        for c in candidates:
            sequence = self.reasoner.get_method_sequence(c["name"])
            tools = sequence if sequence else c.get("tools", [])
            plans.append(
                Plan(
                    task=safe_task,
                    method_name=c["name"],
                    tools=tools,
                    score=c["score"],
                    stvs=c.get("stvs", []),
                )
            )
        return plans
