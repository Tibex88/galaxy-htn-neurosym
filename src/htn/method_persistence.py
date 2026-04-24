"""
Persist verified method proposals into method_sets.metta.

Appends a new `(= (method-for TASK METHOD_NAME) (MethodSequence (...)))`
block plus `MethodUsesTool` facts. Proposals are born with a
low-confidence STV so the PLN scorer treats them as tentative until
execution feedback revises the confidence upward or down.

Also appends a trailing comment marker identifying the proposal origin
and a creation timestamp, so the library grown online is distinguishable
from the seed library for audit purposes.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from src import config
from src.htn.method_proposer import ProposedMethod


METHOD_SETS_PATH = Path(config.METTA_DOMAIN_DIR) / "method_sets.metta"
INITIAL_METHOD_STV = (0.55, 0.20)


def _safe(name: str) -> str:
    return (
        name.replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
        .replace(".", "_")
    )


class MethodPersistence:
    def __init__(self, path: Path = METHOD_SETS_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, proposed: ProposedMethod) -> int:
        """
        Append a verified proposal to method_sets.metta.
        Returns the number of lines added.
        """
        task_safe = _safe(proposed.task)
        method_safe = _safe(proposed.method_name)
        tool_list = " ".join(proposed.tools)
        now = _dt.datetime.now().isoformat(timespec="seconds")

        lines: list[str] = [
            "",
            f"; ---- online-learned method ({proposed.source}) {now} ----",
            f"; task: {proposed.task}",
            f"; rationale: {proposed.rationale[:120]}",
            f"(= (method-for {task_safe} {method_safe})",
            f"   (MethodSequence ({tool_list})))",
            "",
        ]
        for tool in proposed.tools:
            lines.append(f"(MethodUsesTool {method_safe} {_safe(tool)})")
        s, c = INITIAL_METHOD_STV
        lines.append(f"(OnlineLearned {method_safe} (stv {s:.2f} {c:.2f}))")
        lines.append("")

        body = "\n".join(lines)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(body)
        return len(lines)
