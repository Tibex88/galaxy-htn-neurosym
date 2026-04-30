"""
Compare a generated Galaxy workflow (.ga or .gxwf.yml) against KB methods
and report the most similar matches plus a per-component diff.

Similarity is computed in two passes:
  1. Coarse: Jaccard overlap on the multiset of tool safe-names (gives a
     fast top-K shortlist, robust to ordering).
  2. Fine: per-candidate diff covering tool sequence (LCS-based), tool
     additions/removals, connection-graph differences, and a hint of
     compiler-introduced metadata that the KB method doesn't carry
     (workflow inputs, conditional state, format constraints).

This module performs READ-ONLY operations. It does not mutate the .ga,
the YAML, or any KB file.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src import config


KB_PATH = Path(config.METTA_DOMAIN_DIR) / "method_meta.json"
TOOL_META_PATH = Path(config.METTA_DOMAIN_DIR) / "tool_meta.json"


# --------------------------------------------------------------------------- #
# Workflow loading                                                            #
# --------------------------------------------------------------------------- #


@dataclass
class LoadedWorkflow:
    path: Path
    name: str
    tools: list[str]                            # ordered safe names
    tool_steps: list[dict]                      # full step dicts (tool only)
    inputs: list[dict]                          # workflow-level inputs
    connections: list[tuple]                    # (target_step, target_port, source_step, source_port)
    raw: dict                                   # original parsed object


def _safe_from_tool_id(tool_id: str | None, tool_meta: dict) -> str | None:
    """Reverse lookup: full toolshed ID -> safe_name (key in tool_meta)."""
    if not tool_id:
        return None
    for safe, meta in tool_meta.items():
        if meta.get("full_id") == tool_id:
            return safe
    parts = tool_id.split("/")
    if len(parts) >= 2:
        return parts[-2]
    return tool_id


def _safe_from_label(label: str | None, tool_meta: dict) -> str | None:
    """Map a step's display label back to a safe_name where possible."""
    if not label:
        return None
    for safe, meta in tool_meta.items():
        if meta.get("display_name") == label:
            return safe
    return re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_") or None


def load_ga(path: Path) -> LoadedWorkflow:
    raw = json.loads(path.read_text())
    tool_meta = json.loads(TOOL_META_PATH.read_text()) if TOOL_META_PATH.exists() else {}
    steps_dict = raw.get("steps", {})
    steps = sorted(steps_dict.values(), key=lambda s: int(s.get("id", 0)))

    tool_steps: list[dict] = []
    inputs: list[dict] = []
    tools_ordered: list[str] = []

    for s in steps:
        kind = s.get("type")
        if kind == "tool":
            safe = _safe_from_tool_id(s.get("tool_id"), tool_meta) or _safe_from_label(
                s.get("label"), tool_meta
            )
            tools_ordered.append(safe or s.get("label") or s.get("name") or "?")
            tool_steps.append({**s, "_safe": safe})
        elif kind in ("data_input", "data_collection_input"):
            inputs.append(s)

    connections: list[tuple] = []
    for s in steps:
        if s.get("type") != "tool":
            continue
        for port, src_list in (s.get("input_connections") or {}).items():
            if isinstance(src_list, dict):
                src_list = [src_list]
            for src in src_list or []:
                connections.append(
                    (
                        s.get("label") or s.get("name"),
                        port,
                        src.get("id"),
                        src.get("output_name"),
                    )
                )

    return LoadedWorkflow(
        path=path,
        name=raw.get("name") or path.stem,
        tools=tools_ordered,
        tool_steps=tool_steps,
        inputs=inputs,
        connections=connections,
        raw=raw,
    )


def load_yaml(path: Path) -> LoadedWorkflow:
    import yaml as _y

    raw = _y.safe_load(path.read_text())
    tool_meta = json.loads(TOOL_META_PATH.read_text()) if TOOL_META_PATH.exists() else {}

    tools_ordered: list[str] = []
    tool_steps: list[dict] = []
    inputs: list[dict] = []

    for k, v in (raw.get("inputs") or {}).items():
        inputs.append({"label": k, **(v if isinstance(v, dict) else {"type": v})})

    for label, step in (raw.get("steps") or {}).items():
        tool_id = step.get("tool_id") if isinstance(step, dict) else None
        safe = _safe_from_tool_id(tool_id, tool_meta) or _safe_from_label(label, tool_meta)
        tools_ordered.append(safe or label)
        tool_steps.append({"label": label, **step, "_safe": safe})

    connections: list[tuple] = []
    for label, step in (raw.get("steps") or {}).items():
        if not isinstance(step, dict):
            continue
        for port, conn in (step.get("in") or {}).items():
            source = conn.get("source") if isinstance(conn, dict) else conn
            if not source:
                continue
            src_label, _, src_port = (source or "").rpartition("/")
            if not src_label:
                src_label, src_port = source, ""
            connections.append((label, port, src_label, src_port))

    return LoadedWorkflow(
        path=path,
        name=raw.get("label") or path.stem,
        tools=tools_ordered,
        tool_steps=tool_steps,
        inputs=inputs,
        connections=connections,
        raw=raw,
    )


def load_workflow(path: str | Path) -> LoadedWorkflow:
    p = Path(path)
    if p.suffix == ".ga":
        return load_ga(p)
    if p.suffix in (".yml", ".yaml"):
        return load_yaml(p)
    raise ValueError(f"unknown workflow extension: {p.suffix}")


# --------------------------------------------------------------------------- #
# Similarity                                                                  #
# --------------------------------------------------------------------------- #


def _jaccard(a: Counter, b: Counter) -> float:
    if not a and not b:
        return 1.0
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 0.0


def _lcs_ratio(seq_a: list[str], seq_b: list[str]) -> float:
    return SequenceMatcher(None, seq_a, seq_b).ratio()


@dataclass
class CandidateMatch:
    method_name: str
    task_type: str
    method_tools: list[str]
    jaccard: float
    sequence_ratio: float

    @property
    def score(self) -> float:
        # Weighted: tool-set overlap matters most, ordering tiebreaks.
        return 0.7 * self.jaccard + 0.3 * self.sequence_ratio


def find_similar(
    wf: LoadedWorkflow, kb: dict, top_k: int = 5
) -> list[CandidateMatch]:
    target = Counter(wf.tools)
    matches: list[CandidateMatch] = []
    for method_name, m in kb.items():
        method_tools = [s["tool"] for s in m.get("steps", [])]
        if not method_tools:
            continue
        cand_counter = Counter(method_tools)
        matches.append(
            CandidateMatch(
                method_name=method_name,
                task_type=m.get("task_type", ""),
                method_tools=method_tools,
                jaccard=_jaccard(target, cand_counter),
                sequence_ratio=_lcs_ratio(wf.tools, method_tools),
            )
        )
    matches.sort(key=lambda c: c.score, reverse=True)
    return matches[:top_k]


# --------------------------------------------------------------------------- #
# Per-candidate diff                                                          #
# --------------------------------------------------------------------------- #


@dataclass
class WorkflowDiff:
    candidate: CandidateMatch
    common_tools: list[str]
    only_in_generated: list[str]
    only_in_method: list[str]
    sequence_alignment: list[tuple[str, str | None, str | None]]  # (op, gen, method)
    compiler_additions: dict = field(default_factory=dict)


def _align(seq_a: list[str], seq_b: list[str]) -> list[tuple[str, str | None, str | None]]:
    """SequenceMatcher-based alignment returning (op, a_item, b_item) tuples."""
    sm = SequenceMatcher(None, seq_a, seq_b)
    ops: list[tuple[str, str | None, str | None]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                ops.append(("equal", seq_a[i], seq_b[j]))
        elif tag == "replace":
            for i in range(i1, i2):
                ops.append(("replace", seq_a[i], None))
            for j in range(j1, j2):
                ops.append(("replace", None, seq_b[j]))
        elif tag == "delete":
            for i in range(i1, i2):
                ops.append(("only_generated", seq_a[i], None))
        elif tag == "insert":
            for j in range(j1, j2):
                ops.append(("only_method", None, seq_b[j]))
    return ops


def diff_against(wf: LoadedWorkflow, candidate: CandidateMatch) -> WorkflowDiff:
    a = Counter(wf.tools)
    b = Counter(candidate.method_tools)
    common = sorted((a & b).elements())
    only_a = sorted((a - b).elements())
    only_b = sorted((b - a).elements())

    # Compiler additions — gxformat2 / .ga things that the raw KB method
    # doesn't carry. Always present when the workflow was emitted by our
    # pipeline; surfaced here so the user can see what came from the
    # compiler vs from the KB.
    compiler_additions = {
        "workflow_inputs": [i.get("label") for i in wf.inputs],
        "tool_states_with_state": sum(
            1 for s in wf.tool_steps
            if s.get("tool_state") and "_selector" in (s["tool_state"] or "")
        ),
    }

    return WorkflowDiff(
        candidate=candidate,
        common_tools=common,
        only_in_generated=only_a,
        only_in_method=only_b,
        sequence_alignment=_align(wf.tools, candidate.method_tools),
        compiler_additions=compiler_additions,
    )


# --------------------------------------------------------------------------- #
# Pretty-print                                                                #
# --------------------------------------------------------------------------- #


def render_summary(wf: LoadedWorkflow, diffs: list[WorkflowDiff]) -> str:
    lines: list[str] = []
    lines.append(f"GENERATED: {wf.path}")
    lines.append(f"  name        : {wf.name}")
    lines.append(f"  tool steps  : {len(wf.tools)}")
    lines.append(f"  inputs      : {len(wf.inputs)}")
    lines.append(f"  connections : {len(wf.connections)}")
    lines.append("")
    lines.append(f"Top {len(diffs)} similar KB methods:")
    lines.append("=" * 72)

    for i, d in enumerate(diffs, 1):
        c = d.candidate
        lines.append("")
        lines.append(
            f"[{i}] {c.method_name}   "
            f"(score {c.score:.3f}  jaccard {c.jaccard:.3f}  seq {c.sequence_ratio:.3f})"
        )
        lines.append(f"    task_type     : {c.task_type}")
        lines.append(f"    method tools  : {len(c.method_tools)}")
        lines.append(f"    common        : {len(d.common_tools)}")
        if d.only_in_generated:
            lines.append(f"    only in gen   : {d.only_in_generated[:8]}{'...' if len(d.only_in_generated)>8 else ''}")
        if d.only_in_method:
            lines.append(f"    only in KB    : {d.only_in_method[:8]}{'...' if len(d.only_in_method)>8 else ''}")
        # Sequence alignment summary (truncated)
        eq = sum(1 for op, _, _ in d.sequence_alignment if op == "equal")
        diffs_n = sum(1 for op, _, _ in d.sequence_alignment if op != "equal")
        lines.append(f"    sequence ops  : {eq} equal, {diffs_n} differ")
        if c.score < 1.0 and diffs_n:
            preview = [op for op in d.sequence_alignment if op[0] != "equal"][:5]
            for op, gen, method in preview:
                if op == "only_generated":
                    lines.append(f"      + gen has   : {gen}")
                elif op == "only_method":
                    lines.append(f"      - KB has    : {method}")
                elif op == "replace":
                    if gen:
                        lines.append(f"      ~ gen has   : {gen}")
                    if method:
                        lines.append(f"      ~ KB has    : {method}")
        lines.append(
            f"    compiler adds : inputs={d.compiler_additions['workflow_inputs']}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Diff a generated Galaxy workflow against KB methods."
    )
    ap.add_argument("workflow_path")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument(
        "--kb",
        default=str(KB_PATH),
        help="Path to method_meta.json (default: metta/domain/method_meta.json)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable summary",
    )
    args = ap.parse_args()

    wf = load_workflow(args.workflow_path)
    kb = json.loads(Path(args.kb).read_text())
    candidates = find_similar(wf, kb, top_k=args.top)
    diffs = [diff_against(wf, c) for c in candidates]

    if args.json:
        out = {
            "workflow": {
                "path": str(wf.path),
                "name": wf.name,
                "tools": wf.tools,
                "n_inputs": len(wf.inputs),
                "n_connections": len(wf.connections),
            },
            "matches": [
                {
                    "method_name": d.candidate.method_name,
                    "task_type": d.candidate.task_type,
                    "score": d.candidate.score,
                    "jaccard": d.candidate.jaccard,
                    "sequence_ratio": d.candidate.sequence_ratio,
                    "method_tools": d.candidate.method_tools,
                    "common": d.common_tools,
                    "only_in_generated": d.only_in_generated,
                    "only_in_method": d.only_in_method,
                    "sequence_alignment": d.sequence_alignment,
                    "compiler_additions": d.compiler_additions,
                }
                for d in diffs
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        print(render_summary(wf, diffs))


if __name__ == "__main__":
    main()
