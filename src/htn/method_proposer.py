"""
LLM-driven method proposal.

When the planner finds no method for a task, or all existing methods
score below a threshold, the proposer asks the LLM to suggest a
decomposition. The LLM receives:

  - the task name
  - the catalog of tools actually present in tool_atoms.metta
    (this alone prevents hallucination of non-existent tools —
     if a proposed tool is outside the catalog, the verifier rejects)
  - the EDAM-aligned type hierarchy summary
  - a few example successful methods for the same task type (if any)

The proposer does NOT accept or persist — it only returns a candidate.
Verification and persistence happen in separate modules so the policy
(what to accept, how to assign initial STVs) is isolated from the LLM
call itself.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from src import config
from src.pln.reasoner import PLNReasoner


@dataclass
class ProposedMethod:
    task: str
    method_name: str
    tools: list[str] = field(default_factory=list)
    rationale: str = ""
    source: str = "offline"

    def render(self) -> str:
        return (
            f"proposed method_name={self.method_name}  task={self.task}  "
            f"source={self.source}\n  {' -> '.join(self.tools)}\n"
            f"  rationale: {self.rationale[:120]}"
        )


def _load_tool_catalog() -> list[str]:
    """Read tool names from tool_atoms.metta — the only source of truth."""
    path = Path(config.METTA_DOMAIN_DIR) / "tool_atoms.metta"
    if not path.exists():
        return []
    tools = re.findall(
        r"\(= \(tool-quality ([A-Za-z0-9_]+)\) \(STV", path.read_text()
    )
    return sorted(set(tools))


def _load_type_hierarchy() -> list[str]:
    path = Path(config.METTA_DOMAIN_DIR) / "galaxy_types.metta"
    if not path.exists():
        return []
    return re.findall(r"\(Inheritance ([A-Za-z0-9_]+) ([A-Za-z0-9_]+)\)", path.read_text())


def _example_methods(reasoner: PLNReasoner, task: str, k: int = 3) -> list[dict]:
    scored = reasoner.get_methods_for_task_scored(task)
    return [
        {
            "method_name": m["name"],
            "tools": reasoner.get_method_sequence(m["name"]) or m.get("tools", []),
            "score": m["score"],
        }
        for m in scored[:k]
    ]


_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "method_name": {
            "type": "string",
            "description": (
                "Descriptive identifier for the method. "
                "Use snake_case ASCII letters, digits and underscores only."
            ),
            "pattern": "^[A-Za-z][A-Za-z0-9_]*$",
        },
        "tools": {
            "type": "array",
            "description": (
                "Ordered tool sequence for the decomposition. "
                "Every tool MUST be drawn from the provided catalog."
            ),
            "items": {"type": "string"},
            "minItems": 2,
        },
        "rationale": {"type": "string"},
    },
    "required": ["method_name", "tools"],
}


def _gemini_propose(
    task: str,
    catalog: list[str],
    type_hierarchy: list[tuple[str, str]],
    examples: list[dict],
    api_key: str,
) -> ProposedMethod:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-2.5-flash",
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": _PROPOSAL_SCHEMA,
        },
    )

    catalog_sample = catalog[:600]
    hierarchy_sample = type_hierarchy[:40]
    examples_str = json.dumps(examples, indent=2) if examples else "(none yet)"

    prompt = (
        "You are an expert bioinformatics workflow designer. The planner's "
        "method library has no satisfactory method for the task below. "
        "Propose a tool sequence that would decompose it. Every tool in the "
        "sequence MUST be copied verbatim from the provided catalog; do not "
        "invent tool names.\n\n"
        f"Task: {task}\n\n"
        f"Tool catalog (pick from these only; {len(catalog)} total, first "
        f"{len(catalog_sample)} shown):\n{json.dumps(catalog_sample)}\n\n"
        "Type hierarchy (sample):\n"
        + "\n".join(f"  {c} <- {p}" for c, p in hierarchy_sample)
        + f"\n\nExisting example methods for this task:\n{examples_str}\n\n"
        "Return JSON matching the schema. Order tools so each step's output "
        "types plausibly feed the next step's inputs."
    )

    resp = model.generate_content(prompt)
    payload = json.loads(resp.text)
    return ProposedMethod(
        task=task,
        method_name=payload.get("method_name", f"proposed_{task}"),
        tools=payload.get("tools", []),
        rationale=payload.get("rationale", ""),
        source="gemini",
    )


def _offline_propose(task: str, examples: list[dict]) -> ProposedMethod:
    """
    Deterministic fallback: synthesize a method by concatenating the
    prefix of the best existing example with its suffix, producing a
    novel but plausible chain. Used when no LLM is available so the
    pipeline stays exercisable.
    """
    if not examples:
        return ProposedMethod(
            task=task,
            method_name=f"proposed_{task}_empty",
            tools=[],
            rationale="no examples available; offline proposer cannot synthesize",
            source="offline",
        )
    tools = list(examples[0]["tools"])
    if len(examples) > 1 and examples[1]["tools"]:
        # splice a tool from the second-best method to make it distinct
        extra = examples[1]["tools"][-1]
        if extra not in tools:
            tools.append(extra)
    return ProposedMethod(
        task=task,
        method_name=f"proposed_{task}_offline",
        tools=tools,
        rationale="offline splice of top two existing methods",
        source="offline",
    )


class MethodProposer:
    def __init__(self, reasoner: PLNReasoner, prefer_llm: bool = True):
        self.reasoner = reasoner
        self.prefer_llm = prefer_llm
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self._catalog = _load_tool_catalog()
        self._types = _load_type_hierarchy()

    def propose(self, task: str) -> ProposedMethod:
        examples = _example_methods(self.reasoner, task)
        if self.prefer_llm and self.api_key and self._catalog:
            try:
                return _gemini_propose(
                    task, self._catalog, self._types, examples, self.api_key
                )
            except Exception as e:
                print(f"  [proposer] LLM call failed ({e}); falling back to offline.")
        return _offline_propose(task, examples)
