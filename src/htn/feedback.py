"""
Execution feedback -> STV revision.

After a Galaxy run (success or failure), the feedback module revises
each involved tool's STV via PLN Revision, merging the current stored
STV with a fresh evidence stream derived from the observed outcome:

  success observation -> (0.95, 0.35)
  failure observation -> (0.10, 0.35)

Tools that repeatedly succeed drift toward strength=1.0 with growing
confidence; tools that repeatedly fail drift toward strength=0.0.
Because PLN Revision weights by confidence, new observations never
clobber well-established priors in a single step.

STV changes are rewritten in place to tool_atoms.metta so the next
planning run automatically reflects the observed reliability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src import config
from src.pln.reasoner import PLNReasoner


TOOL_ATOMS_PATH = Path(config.METTA_DOMAIN_DIR) / "tool_atoms.metta"

SUCCESS_EVIDENCE = (0.95, 0.35)
FAILURE_EVIDENCE = (0.10, 0.35)


@dataclass
class FeedbackResult:
    tool: str
    old_stv: tuple[float, float]
    new_stv: tuple[float, float]
    outcome: str

    def render(self) -> str:
        os_s, os_c = self.old_stv
        ns_s, ns_c = self.new_stv
        return (
            f"{self.tool:<40}  {self.outcome:<7}  "
            f"({os_s:.3f},{os_c:.3f}) -> ({ns_s:.3f},{ns_c:.3f})"
        )


class FeedbackLearner:
    def __init__(self, reasoner: PLNReasoner):
        self.reasoner = reasoner

    def record(self, tool: str, outcome: str) -> FeedbackResult:
        if outcome not in ("success", "failure"):
            raise ValueError("outcome must be 'success' or 'failure'")

        old = self.reasoner.get_tool_stv(tool)
        evidence = SUCCESS_EVIDENCE if outcome == "success" else FAILURE_EVIDENCE
        new = self.reasoner.pln_revision(old, evidence)

        self.reasoner._stv_cache[tool] = new
        self._rewrite_atom(tool, new)
        return FeedbackResult(tool=tool, old_stv=old, new_stv=new, outcome=outcome)

    def record_workflow(
        self, tools: list[str], outcome: str
    ) -> list[FeedbackResult]:
        return [self.record(t, outcome) for t in tools]

    # ------------------------------------------------------------------ #
    # Persistence                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _rewrite_atom(tool: str, stv: tuple[float, float]):
        if not TOOL_ATOMS_PATH.exists():
            return
        text = TOOL_ATOMS_PATH.read_text()
        s, c = stv
        replacement = f"(= (tool-quality {tool}) (STV {s:.3f} {c:.3f}))"
        pattern = re.compile(
            rf"\(= \(tool-quality {re.escape(tool)}\) \(STV [\d.]+ [\d.]+\)\)"
        )
        new_text, n = pattern.subn(replacement, text)
        if n == 0:
            new_text = text.rstrip() + "\n" + replacement + "\n"
        TOOL_ATOMS_PATH.write_text(new_text)
