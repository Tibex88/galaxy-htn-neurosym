"""
ChatHTN-style online learning loop.

Orchestrates the library-growth mechanism:

    1. Planner attempts to solve the task from the current method set.
    2. If no method exists, or the best-scoring method is below a
       confidence threshold, invoke the MethodProposer.
    3. Verify the proposal via MethodVerifier.
    4. If verified, persist via MethodPersistence and re-run the
       planner — now with the augmented library.
    5. Return the final plan plus a LearningReport capturing what
       happened (gap detected? proposal accepted? how many rounds?).

The loop is deliberately single-round by default: we accept at most
one LLM proposal per task to keep behaviour predictable. Callers can
raise max_rounds if they want iterative refinement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.htn.planner import HTNPlanner, Plan
from src.htn.method_proposer import MethodProposer, ProposedMethod
from src.htn.method_verifier import MethodVerifier, VerificationResult
from src.htn.method_persistence import MethodPersistence
from src.pln.reasoner import PLNReasoner


GAP_SCORE_THRESHOLD = 0.30


@dataclass
class LearningReport:
    task: str
    gap_detected: bool = False
    proposal: ProposedMethod | None = None
    verification: VerificationResult | None = None
    accepted: bool = False
    rounds: int = 0
    final_plan: Plan | None = None

    def render(self) -> str:
        lines = [f"task: {self.task}"]
        lines.append(f"  gap_detected: {self.gap_detected}")
        lines.append(f"  rounds: {self.rounds}")
        if self.proposal:
            lines.append("  proposal:")
            lines.append(f"    name: {self.proposal.method_name}")
            lines.append(f"    source: {self.proposal.source}")
            lines.append(f"    tools: {' -> '.join(self.proposal.tools)}")
        if self.verification:
            lines.append(f"  verification: {self.verification.render()}")
        lines.append(f"  accepted: {self.accepted}")
        if self.final_plan:
            lines.append("  final_plan:")
            lines.append("    " + self.final_plan.render().replace("\n", "\n    "))
        return "\n".join(lines)


class OnlineLearner:
    def __init__(
        self,
        reasoner: PLNReasoner,
        planner: HTNPlanner,
        max_rounds: int = 1,
        score_threshold: float = GAP_SCORE_THRESHOLD,
    ):
        self.reasoner = reasoner
        self.planner = planner
        self.max_rounds = max_rounds
        self.score_threshold = score_threshold
        self.proposer = MethodProposer(reasoner)
        self.verifier = MethodVerifier(reasoner)
        self.persistence = MethodPersistence()

    def plan_with_learning(self, task: str) -> LearningReport:
        report = LearningReport(task=task)

        initial = self.planner.plan(task)
        if self._is_adequate(initial):
            report.final_plan = initial
            return report

        report.gap_detected = True

        for round_idx in range(self.max_rounds):
            report.rounds = round_idx + 1
            proposal = self.proposer.propose(task)
            report.proposal = proposal

            verification = self.verifier.verify(proposal)
            report.verification = verification

            if not verification.ok:
                continue

            self.persistence.append(proposal)

            # Invalidate the reasoner's caches so the new atom is visible
            self.reasoner._stv_cache.clear()
            self.reasoner._loaded = False
            self.reasoner.load()

            report.accepted = True
            report.final_plan = self.planner.plan(task)
            return report

        report.final_plan = initial
        return report

    def _is_adequate(self, plan: Plan) -> bool:
        return plan.ok and plan.score >= self.score_threshold
