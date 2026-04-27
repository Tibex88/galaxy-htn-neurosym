"""
Galaxy Format 2 (gxformat2) workflow compiler.

Takes one or more planned tool sequences (with their data-flow edges
recovered from the KB) and emits a gxformat2 YAML document suitable
for import into a Galaxy server via BioBlend.

Data-flow reconstruction preserves DAG topology — parallel branches
and fan-in joins survive compilation. A linear method sequence
degrades to a linear YAML chain; a method with real fork/join
structure (recoverable from MethodDataFlow atoms) is serialized with
multiple input_connections per step.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from src.htn.planner import Plan
from src.pln.reasoner import PLNReasoner


@dataclass
class CompiledWorkflow:
    name: str
    yaml: str
    tool_ids: list[str] = field(default_factory=list)
    missing_full_ids: list[str] = field(default_factory=list)

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.yaml, encoding="utf-8")
        return p


def _y_str(s: str) -> str:
    """Escape a string as a YAML scalar."""
    if not s:
        return '""'
    if any(ch in s for ch in ":#@`\n\"'"):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


class WorkflowCompiler:
    def __init__(self, reasoner: PLNReasoner):
        self.reasoner = reasoner

    def compile(
        self,
        plans: list[Plan],
        workflow_name: str = "htn_generated_workflow",
        annotation: str = "",
    ) -> CompiledWorkflow:
        """
        Compile one or more plans into a single gxformat2 YAML workflow.
        Sequential plans are chained: the last tool of plan N feeds the
        first tool of plan N+1 when types are compatible (best-effort;
        verification happens in the planner).
        """
        all_tool_ids: list[str] = []
        missing_full_ids: list[str] = []

        lines: list[str] = []
        lines.append("class: GalaxyWorkflow")
        lines.append(f"label: {_y_str(workflow_name)}")
        if annotation:
            lines.append(f"doc: {_y_str(annotation)}")
        lines.append("")

        # Collect unique data inputs from the first step of the first plan.
        lines.append("inputs:")
        lines.append("  input_dataset:")
        lines.append("    type: data")
        lines.append('    doc: "Primary input dataset (e.g. FASTQ / BAM)."')
        lines.append("")

        # gxformat2 v19_09 requires an outputs block (may be empty).
        lines.append("outputs: {}")
        lines.append("")

        lines.append("steps:")

        prev_step_id: str | None = "input_dataset"
        prev_is_input = True

        # Tracks how many times each tool name has been used so we can
        # disambiguate duplicates (gxformat2 step keys must be unique).
        # Galaxy renders the step key as the step label in its UI, so we
        # keep keys as the bare tool name where possible.
        used_keys: dict[str, int] = {}

        for plan in plans:
            if not plan.ok:
                continue
            dataflow = self.reasoner.get_method_dataflow(plan.method_name)
            per_tool_flows = self._index_by_tool(dataflow)

            tool_to_step_id: dict[str, str] = {}

            for i, tool in enumerate(plan.tools):
                base = tool
                count = used_keys.get(base, 0) + 1
                used_keys[base] = count
                step_id = base if count == 1 else f"{base}_{count}"
                tool_to_step_id[tool] = step_id

                # Prefer ToolFullID (toolshed) -> ToolDisplayName (original
                # un-sanitized name) -> safe_name as a last resort. The
                # safe_name is junk to Galaxy's tool registry — record it
                # so callers know which tools still need a real ID source.
                tool_ref = self.reasoner.resolve_tool_id(tool)
                if tool_ref == tool and self.reasoner.get_tool_full_id(tool) is None:
                    missing_full_ids.append(tool)
                all_tool_ids.append(tool_ref)

                # In gxformat2, the step's dict key serves as its label and is
                # what `in: source:` references must point to. Setting an
                # explicit `label:` here would override the dict key and break
                # source resolution in gxwf-to-native.
                lines.append(f"  {step_id}:")
                lines.append(f"    tool_id: {_y_str(tool_ref)}")

                connections = self._resolve_connections(
                    tool=tool,
                    tool_index=i,
                    plan=plan,
                    per_tool_flows=per_tool_flows,
                    tool_to_step_id=tool_to_step_id,
                    prev_step_id=prev_step_id,
                    prev_is_input=prev_is_input,
                )

                if connections:
                    lines.append("    in:")
                    for port, source in connections:
                        lines.append(f"      {port}:")
                        lines.append(f"        source: {source}")
                lines.append("")

                prev_step_id = step_id
                prev_is_input = False

        yaml = "\n".join(lines)
        return CompiledWorkflow(
            name=workflow_name,
            yaml=yaml,
            tool_ids=all_tool_ids,
            missing_full_ids=sorted(set(missing_full_ids)),
        )

    @staticmethod
    def _index_by_tool(dataflow: list[dict]) -> dict[str, dict]:
        """Group dataflow records by tool for O(1) per-tool lookup."""
        idx: dict[str, dict] = defaultdict(lambda: {"inputs": [], "outputs": []})
        for flow in dataflow:
            idx[flow["tool"]][flow["direction"] + "s"].append(flow)
        return idx

    def _resolve_connections(
        self,
        tool: str,
        tool_index: int,
        plan: Plan,
        per_tool_flows: dict[str, dict],
        tool_to_step_id: dict[str, str],
        prev_step_id: str | None,
        prev_is_input: bool,
    ) -> list[tuple[str, str]]:
        """
        Resolve each input port on the current tool to either a previous
        step's output or the primary workflow input.
        """
        flows = per_tool_flows.get(tool, {"inputs": [], "outputs": []})
        inputs = flows.get("inputs", [])

        if not inputs:
            if prev_step_id and tool_index > 0 and not prev_is_input:
                return [("input", f"{prev_step_id}/output")]
            if prev_step_id and prev_is_input:
                return [("input", prev_step_id)]
            return []

        # Build reverse map: variable -> producing (tool, output_port)
        var_producer: dict[str, tuple[str, str]] = {}
        for upstream_tool, upstream_flows in per_tool_flows.items():
            for out in upstream_flows.get("outputs", []):
                var_producer[out["var"]] = (upstream_tool, out["port"])

        connections: list[tuple[str, str]] = []
        for in_flow in inputs:
            port = in_flow["port"]
            var = in_flow["var"]
            prod = var_producer.get(var)
            if prod and prod[0] in tool_to_step_id:
                upstream_step = tool_to_step_id[prod[0]]
                connections.append((port, f"{upstream_step}/{prod[1]}"))
            elif prev_step_id and tool_index > 0 and not prev_is_input:
                connections.append((port, f"{prev_step_id}/output"))
            elif prev_step_id and prev_is_input:
                connections.append((port, prev_step_id))
        return connections
