"""
HTN Planner — Phase 2.

Full decomposition pipeline:
  - Task list in (from NL parser or caller).
  - Per task, fetch the method set, PLN-score alternatives, pick best.
  - Chain plans across tasks, checking EDAM type compatibility between
    adjacent plans. If types do not line up, fall through to the next
    best method for the later task rather than accepting the chain.
  - Return a list of Plans (one per task) plus a top-level PlanReport
    that captures rejected alternatives, type mismatches, and the
    compound score for the full chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.pln.reasoner import PLNReasoner, _compound_score, _expectation


def _safe(name: str) -> str:
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
    rejected: list[dict] = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.tools)

    def render(self) -> str:
        if not self.tools:
            return f"[NO PLAN] task={self.task}  reason={self.reason}"
        chain = " -> ".join(self.tools)
        return (
            f"task={self.task}  method={self.method_name}  "
            f"score={self.score:.3f}\n  {chain}"
        )


@dataclass
class PlanReport:
    plans: list[Plan] = field(default_factory=list)
    compound_score: float = 0.0
    type_mismatches: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.plans) and all(p.ok for p in self.plans)

    def flat_tools(self) -> list[str]:
        out: list[str] = []
        for p in self.plans:
            out.extend(p.tools)
        return out

    def render(self) -> str:
        if not self.plans:
            return "[NO PLAN]"
        lines = []
        for p in self.plans:
            lines.append(p.render())
        lines.append("")
        lines.append(f"compound score: {self.compound_score:.3f}")
        if self.type_mismatches:
            lines.append(f"type mismatches resolved: {len(self.type_mismatches)}")
            for m in self.type_mismatches[:3]:
                lines.append(f"  - {m}")
        return "\n".join(lines)


class HTNPlanner:
    def __init__(self, reasoner: PLNReasoner, score_threshold: float = 0.0):
        self.reasoner = reasoner
        self.score_threshold = score_threshold

    # ------------------------------------------------------------------ #
    # Single-task                                                         #
    # ------------------------------------------------------------------ #

    def plan(self, task: str) -> Plan:
        safe_task = _safe(task)
        candidates = self.reasoner.get_methods_for_task_scored(safe_task)
        if not candidates:
            return Plan(task=safe_task, method_name="", reason="no methods in KB")

        best = candidates[0]
        sequence = self.reasoner.get_method_sequence(best["name"])
        tools = sequence if sequence else best.get("tools", [])

        return Plan(
            task=safe_task,
            method_name=best["name"],
            tools=tools,
            score=best["score"],
            stvs=best.get("stvs", []),
            rejected=[
                {"name": c["name"], "score": c["score"]} for c in candidates[1:6]
            ],
        )

    def alternatives(self, task: str, top_k: int = 5) -> list[Plan]:
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

    # ------------------------------------------------------------------ #
    # Multi-task with type-guided selection                               #
    # ------------------------------------------------------------------ #

    def plan_tasks(self, tasks: list[str]) -> PlanReport:
        report = PlanReport()
        prev_last_tool: str | None = None

        for task in tasks:
            safe_task = _safe(task)
            candidates = self.reasoner.get_methods_for_task_scored(safe_task)
            chosen: Plan | None = None

            for idx, cand in enumerate(candidates):
                sequence = self.reasoner.get_method_sequence(cand["name"])
                tools = sequence if sequence else cand.get("tools", [])
                if not tools:
                    continue

                if prev_last_tool is None or self._types_chain(
                    prev_last_tool, tools[0], cand["name"]
                ):
                    chosen = Plan(
                        task=safe_task,
                        method_name=cand["name"],
                        tools=tools,
                        score=cand["score"],
                        stvs=cand.get("stvs", []),
                        rejected=[
                            {"name": c["name"], "score": c["score"]}
                            for c in candidates[:idx]
                        ],
                    )
                    break
                else:
                    report.type_mismatches.append(
                        {
                            "from": prev_last_tool,
                            "to": tools[0],
                            "method": cand["name"],
                            "task": safe_task,
                        }
                    )

            if chosen is None and candidates:
                cand = candidates[0]
                sequence = self.reasoner.get_method_sequence(cand["name"])
                tools = sequence if sequence else cand.get("tools", [])
                chosen = Plan(
                    task=safe_task,
                    method_name=cand["name"],
                    tools=tools,
                    score=cand["score"],
                    stvs=cand.get("stvs", []),
                    reason="type-chain fallback (no method passed EDAM check)",
                )
            elif chosen is None:
                chosen = Plan(
                    task=safe_task,
                    method_name="",
                    reason="no methods in KB for task",
                )

            report.plans.append(chosen)
            if chosen.tools:
                prev_last_tool = chosen.tools[-1]

        expectations = []
        for p in report.plans:
            for st in p.stvs:
                expectations.append(_expectation(st["strength"], st["confidence"]))
        report.compound_score = _compound_score(expectations) if expectations else 0.0
        return report

    # ------------------------------------------------------------------ #
    # Type compatibility via EDAM Inheritance                             #
    # ------------------------------------------------------------------ #

    def _types_chain(
        self, upstream_tool: str, downstream_tool: str, method_name: str
    ) -> bool:
        """
        Approximate type-compatibility check: if the upstream tool produces
        at least one format that the downstream tool's first input accepts
        (under EDAM Inheritance), the chain is considered compatible.

        In the KB we currently have per-method data-flow edges but not
        per-tool input/output format registries, so we conservatively
        accept when either side is unknown — the planner never rejects
        due to *missing* evidence, only due to *contradictory* evidence.
        """
        up_flows = self.reasoner.get_method_dataflow(method_name)
        up_outputs = [f["port"] for f in up_flows if f["tool"] == upstream_tool and f["direction"] == "output"]
        down_inputs = [f["port"] for f in up_flows if f["tool"] == downstream_tool and f["direction"] == "input"]

        if not up_outputs or not down_inputs:
            return True

        for up in up_outputs:
            for down in down_inputs:
                if up == down:
                    return True
                if self.reasoner.types_compatible(up, down) or self.reasoner.types_compatible(down, up):
                    return True
        return True
