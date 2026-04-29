"""
Galaxy Format 2 (gxformat2) workflow compiler.

Takes one or more planned tool sequences (with their data-flow edges
recovered from the KB) and emits a gxformat2 YAML document suitable
for import into a Galaxy server via BioBlend.

Data-flow reconstruction preserves DAG topology — parallel branches
and fan-in joins survive compilation. The compiler walks each method's
step-keyed atoms (MethodStep / StepDataFlow / MethodInput) so that
multiple instances of the same tool (e.g. 3x MultiQC) get distinct
input wiring instead of being collapsed.

Workflow-level inputs are emitted for each variable that no step in
the plan produces — these become the user-supplied datasets when the
workflow is run on a Galaxy server.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from src.htn.planner import Plan
from src.pln.reasoner import PLNReasoner
from src.galaxy.conditional_inference import infer_state, infer_input_type


@dataclass
class CompiledWorkflow:
    name: str
    yaml: str
    tool_ids: list[str] = field(default_factory=list)
    missing_full_ids: list[str] = field(default_factory=list)
    workflow_inputs: list[str] = field(default_factory=list)
    unconnected_step_inputs: list[tuple[str, str]] = field(default_factory=list)

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.yaml, encoding="utf-8")
        return p


def _y_str(s: str) -> str:
    if not s:
        return '""'
    if any(ch in s for ch in ":#@`\n\"'"):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _y_key(s: str) -> str:
    if not s:
        return '""'
    if s[0].isdigit() or any(ch in s for ch in ' :#@`,[]{}|>?*&!%"\''):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _y_source(s: str) -> str:
    if not s:
        return '""'
    if any(ch in s for ch in ' :#@`,[]{}|>?*&!%"\''):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _input_label(port: str) -> str:
    """
    Synthesize a friendly label for a workflow input derived from a tool
    port name. Strip Galaxy's `cond|sub|leaf` pipe nesting and use the
    leaf segment.
    """
    leaf = port.rsplit("|", 1)[-1] if "|" in port else port
    return leaf or "input"


def _y_scalar(v) -> str:
    """Render a Python scalar as YAML."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
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
        context: dict | None = None,
    ) -> CompiledWorkflow:
        all_tool_ids: list[str] = []
        missing_full_ids: list[str] = []
        unconnected_inputs: list[tuple[str, str]] = []

        # Used to disambiguate workflow input names if multiple tools
        # need a port called e.g. `input`.
        input_name_used: dict[str, int] = defaultdict(int)
        # Map var -> workflow_input_name. A given var may be required by
        # multiple steps; they all reference the same workflow input.
        var_to_input_name: dict[str, str] = {}
        # Ordered list of (input_name, type_spec_dict). The type spec
        # comes from infer_input_type() and may be "data" or a collection.
        workflow_inputs: list[tuple[str, dict[str, str]]] = []

        # First pass — collect workflow-level inputs across all plans.
        # We build them up front so we can emit the inputs: block before
        # the steps: block (gxformat2 expects this order).
        plan_to_method_inputs: dict[int, list[dict]] = {}
        for plan_idx, plan in enumerate(plans):
            if not plan.ok:
                continue
            method_inputs = self.reasoner.get_method_inputs(plan.method_name)
            plan_to_method_inputs[plan_idx] = method_inputs
            for mi in method_inputs:
                var = mi["var"]
                if var in var_to_input_name:
                    continue
                base = _input_label(mi["port"])
                input_name_used[base] += 1
                count = input_name_used[base]
                input_name = base if count == 1 else f"{base}_{count}"
                var_to_input_name[var] = input_name
                workflow_inputs.append((input_name, infer_input_type(mi["port"])))

        # Header
        lines: list[str] = []
        lines.append("class: GalaxyWorkflow")
        lines.append(f"label: {_y_str(workflow_name)}")
        if annotation:
            lines.append(f"doc: {_y_str(annotation)}")
        lines.append("")

        # Workflow-level inputs. Always emit at least one fallback input so
        # the workflow remains importable even when the KB has no
        # MethodInput atoms for the chosen plans.
        lines.append("inputs:")
        if workflow_inputs:
            for input_name, type_spec in workflow_inputs:
                lines.append(f"  {_y_key(input_name)}:")
                for k in ("type", "collection_type"):
                    if k in type_spec:
                        lines.append(f"    {k}: {type_spec[k]}")
        else:
            lines.append("  input_dataset:")
            lines.append("    type: data")
            lines.append('    doc: "Primary input dataset (e.g. FASTQ / BAM)."')
        lines.append("")

        lines.append("outputs: {}")
        lines.append("")

        lines.append("steps:")

        used_keys: dict[str, int] = {}
        # step_id (KB) -> YAML step label, populated as we emit each step.
        step_id_to_label: dict[str, str] = {}

        for plan_idx, plan in enumerate(plans):
            if not plan.ok:
                continue

            steps = self.reasoner.get_method_steps(plan.method_name)
            if not steps:
                # KB has no MethodStep atoms (legacy method or empty plan);
                # skip — without per-step info we cannot wire inputs safely.
                continue

            # Build var -> (kb_step_id, output_port) for this method.
            var_producer: dict[str, tuple[str, str]] = {}
            step_inputs: dict[str, list[dict]] = {}
            for step in steps:
                flows = self.reasoner.get_step_dataflow(plan.method_name, step["step_id"])
                for f in flows:
                    if f["direction"] == "output":
                        var_producer[f["var"]] = (step["step_id"], f["port"])
                    elif f["direction"] == "input":
                        step_inputs.setdefault(step["step_id"], []).append(f)

            for step in steps:
                tool = step["tool"]
                kb_step_id = step["step_id"]

                tool_ref = self.reasoner.resolve_tool_id(tool)
                if tool_ref == tool and self.reasoner.get_tool_full_id(tool) is None:
                    missing_full_ids.append(tool)
                all_tool_ids.append(tool_ref)

                display_name = self.reasoner.get_tool_display_name(tool) or tool
                count = used_keys.get(display_name, 0) + 1
                used_keys[display_name] = count
                yaml_label = display_name if count == 1 else f"{display_name} ({count})"
                step_id_to_label[kb_step_id] = yaml_label

                lines.append(f"  {_y_key(yaml_label)}:")
                lines.append(f"    tool_id: {_y_str(tool_ref)}")

                # Build var_producer keyed by upstream TOOL (not step_id)
                # so the conditional inference module — which only knows
                # about tool names — can match upstream tools when picking
                # MultiQC's software discriminator etc.
                var_producer_by_tool: dict[str, tuple[str, str]] = {}
                for upstream_step in steps:
                    for f in self.reasoner.get_step_dataflow(
                        plan.method_name, upstream_step["step_id"]
                    ):
                        if f["direction"] == "output":
                            var_producer_by_tool[f["var"]] = (
                                upstream_step["tool"],
                                f["port"],
                            )

                this_step_inputs = step_inputs.get(kb_step_id, [])
                state = infer_state(
                    tool=tool,
                    step_inputs=this_step_inputs,
                    var_producer=var_producer_by_tool,
                    context=context,
                )
                if state:
                    lines.append("    state:")
                    self._emit_state(lines, state, indent=6)

                connections: list[tuple[str, str]] = []
                for in_flow in this_step_inputs:
                    port = in_flow["port"]
                    var = in_flow["var"]
                    prod = var_producer.get(var)
                    if prod and prod[0] in step_id_to_label:
                        upstream_label = step_id_to_label[prod[0]]
                        connections.append((port, f"{upstream_label}/{prod[1]}"))
                    elif var in var_to_input_name:
                        connections.append((port, var_to_input_name[var]))
                    else:
                        # Producer hasn't been emitted yet (shouldn't happen
                        # in topo order) and var isn't a method input — leave
                        # the port unconnected and surface it for the caller.
                        unconnected_inputs.append((yaml_label, port))

                if connections:
                    lines.append("    in:")
                    for port, source in connections:
                        lines.append(f"      {_y_key(port)}:")
                        lines.append(f"        source: {_y_source(source)}")
                lines.append("")

        yaml = "\n".join(lines)
        return CompiledWorkflow(
            name=workflow_name,
            yaml=yaml,
            tool_ids=all_tool_ids,
            missing_full_ids=sorted(set(missing_full_ids)),
            workflow_inputs=[name for name, _ in workflow_inputs],
            unconnected_step_inputs=unconnected_inputs,
        )

    def _emit_state(self, lines: list[str], state, indent: int) -> None:
        """
        Recursively emit a nested state dict (with possible list values
        for the MultiQC `results:` array) as gxformat2-compatible YAML.
        """
        pad = " " * indent
        if isinstance(state, dict):
            for k, v in state.items():
                if isinstance(v, dict):
                    lines.append(f"{pad}{_y_key(k)}:")
                    self._emit_state(lines, v, indent + 2)
                elif isinstance(v, list):
                    lines.append(f"{pad}{_y_key(k)}:")
                    for item in v:
                        if isinstance(item, dict):
                            lines.append(f"{pad}  -")
                            self._emit_state(lines, item, indent + 4)
                        else:
                            lines.append(f"{pad}  - {_y_scalar(item)}")
                else:
                    lines.append(f"{pad}{_y_key(k)}: {_y_scalar(v)}")
