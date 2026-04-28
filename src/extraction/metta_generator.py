import os
from pathlib import Path


class MeTTaGenerator:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _safe_name(self, name: str) -> str:
        return (
            name.replace(" ", "_")
            .replace("/", "_")
            .replace("-", "_")
            .replace("(", "")
            .replace(")", "")
            .replace("+", "plus")
            .replace(".", "_")
            .replace(":", "_")
            .replace(",", "")
            .replace("'", "")
            .replace('"', "")
            .replace("!", "")
            .replace("?", "")
            .replace("[", "")
            .replace("]", "")
            .replace("@", "at")
            .replace("#", "num")
            .replace("&", "and")
            .replace("*", "star")
        )

    def generate_tool_atoms(self, operators: list) -> str:
        """
        Emit tool_atoms.metta plus a sidecar tool_meta.json mapping
        safe_name -> {display_name, full_id, owner}.

        The display_name and full_id are kept OUT of MeTTa because
        hyperon 0.2.10's trie index panics on large numbers of quoted
        string atoms. The sidecar JSON is read by PLNReasoner on load
        for tool_id resolution at compile time.
        """
        import json

        lines = [
            "; ============================================",
            "; Tool atoms with initial TruthValues",
            "; Auto-generated from Neo4j Knowledge Graph",
            ";",
            "; Sidecar tool_meta.json (next to this file) holds the",
            "; un-sanitized display names and toolshed full IDs that the",
            "; compiler uses as gxformat2 tool_id values. They live in JSON",
            "; rather than MeTTa to avoid hyperon 0.2.10 trie panics on",
            "; large quoted-string atomspaces.",
            "; ============================================",
            "",
        ]

        seen_names: set[str] = set()
        meta: dict[str, dict[str, str]] = {}

        for op in operators:
            safe = self._safe_name(op.name)
            if not safe or safe in seen_names:
                continue
            seen_names.add(safe)

            has_outputs = (
                len(op.data_outputs) > 0 if hasattr(op, "data_outputs") else True
            )
            strength = 0.7 if has_outputs else 0.5
            confidence = 0.3

            lines.append(
                f"(= (tool-quality {safe}) (STV {strength:.2f} {confidence:.2f}))"
            )

            entry = {}
            display_name = (op.name or "").strip()
            if display_name and display_name != safe:
                entry["display_name"] = display_name
            full_id = (getattr(op, "full_id", "") or "").strip()
            if full_id:
                entry["full_id"] = full_id
            owner = (getattr(op, "owner", "") or "").strip()
            if owner:
                entry["owner"] = owner
            if entry:
                meta[safe] = entry

        lines.append("")
        lines.append(f"; Total tools: {len(seen_names)}")

        content = "\n".join(lines)
        self._write_file("tool_atoms.metta", content)

        meta_path = self.output_dir / "tool_meta.json"
        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True))
        print(f"  Written: {meta_path} ({len(meta)} entries)")
        return content

    def generate_method_sets(self, method_sets: list) -> str:
        """
        Emits per-method MeTTa atoms (planner uses these) plus a sidecar
        method_meta.json (compiler uses this) with per-step disambiguated
        data flow.

        MeTTa form (lean — kept inside hyperon's safe atom-volume window):
          (= (method-for TASK METHOD) (MethodSequence (t1 t2 ...)))
          (MethodUsesTool METHOD tool)
          (MethodDataFlow METHOD tool direction port var)  -- legacy

        Sidecar method_meta.json keyed by method_name:
          {
            "<method>": {
              "task_type": "...",
              "steps": [
                {"step_id": "...", "tool": "...", "position": 0,
                 "inputs":  [{"port": "...", "var": "?data_N"}, ...],
                 "outputs": [{"port": "...", "var": "?data_N"}, ...]},
                ...
              ],
              "method_inputs": [
                {"var": "?data_N", "step_id": "...", "port": "..."}, ...
              ]
            }
          }

        Per-step data lives outside MeTTa because hyperon 0.2.10's trie
        index panics once the atomspace grows past a few thousand atoms
        (same failure mode as the ToolDisplayName/ToolFullID attempt).
        """
        import json

        method_meta: dict[str, dict] = {}

        lines = [
            "; ============================================",
            "; HTN Method Sets: alternative workflows for each task type",
            "; PLN selects the best method based on tool TruthValues",
            "; Auto-generated from Neo4j Knowledge Graph",
            ";",
            "; MeTTa form (lean):",
            ";   (= (method-for TASK METHOD) (MethodSequence (t1 t2 ...)))",
            ";   (MethodUsesTool method tool)",
            ";   (MethodDataFlow method tool direction port var)  -- legacy",
            ";",
            "; Per-step disambiguated data lives in method_meta.json next",
            "; to this file. The compiler reads that JSON for connection",
            "; resolution because hyperon 0.2.10's trie panics on large",
            "; atomspaces.",
            "; ============================================",
            "",
        ]

        for ms in method_sets:
            if not ms.methods:
                continue

            task_safe = self._safe_name(ms.task_type)
            lines.append(
                f"; ---- Task: {ms.task_type} ({ms.num_alternatives} alternatives) ----"
            )
            lines.append("")

            for i, method in enumerate(ms.methods):
                method_name = self._safe_name(method.workflow_name or f"method_{i}")

                tool_names = [
                    self._safe_name(s.tool_name) for s in method.subtasks if s.tool_name
                ]
                if not tool_names:
                    continue

                tool_list = " ".join(tool_names)
                lines.append(f"(= (method-for {task_safe} {method_name})")
                lines.append(f"   (MethodSequence ({tool_list})))")
                lines.append("")

                for tool_safe in tool_names:
                    lines.append(f"(MethodUsesTool {method_name} {tool_safe})")

                # ----- Legacy tool-keyed dataflow (kept for backwards compat) -----
                for subtask in method.subtasks:
                    tool_safe = (
                        self._safe_name(subtask.tool_name)
                        if subtask.tool_name
                        else None
                    )
                    if not tool_safe:
                        continue

                    for input_port, var_name in subtask.inputs.items():
                        port_safe = self._safe_name(input_port)
                        lines.append(
                            f"(MethodDataFlow {method_name} {tool_safe} input {port_safe} {var_name})"
                        )
                    for output_port, var_name in subtask.outputs.items():
                        port_safe = self._safe_name(output_port)
                        lines.append(
                            f"(MethodDataFlow {method_name} {tool_safe} output {port_safe} {var_name})"
                        )

                # ----- Sidecar JSON — per-step disambiguated data -----
                produced_vars: set[str] = set()
                json_steps: list[dict] = []
                for position, subtask in enumerate(method.subtasks):
                    if not subtask.tool_name:
                        continue
                    tool_safe = self._safe_name(subtask.tool_name)
                    step_safe = (
                        self._safe_name(subtask.step_uid) or f"step_{position}"
                    )
                    inputs_list = [
                        {"port": port, "var": var}
                        for port, var in subtask.inputs.items()
                    ]
                    outputs_list = [
                        {"port": port, "var": var}
                        for port, var in subtask.outputs.items()
                    ]
                    for o in outputs_list:
                        produced_vars.add(o["var"])
                    json_steps.append(
                        {
                            "step_id": step_safe,
                            "tool": tool_safe,
                            "position": position,
                            "inputs": inputs_list,
                            "outputs": outputs_list,
                        }
                    )

                method_inputs_list: list[dict] = []
                seen_input_vars: set[str] = set()
                for s in json_steps:
                    for inp in s["inputs"]:
                        if (
                            inp["var"] not in produced_vars
                            and inp["var"] not in seen_input_vars
                        ):
                            method_inputs_list.append(
                                {
                                    "var": inp["var"],
                                    "step_id": s["step_id"],
                                    "port": inp["port"],
                                }
                            )
                            seen_input_vars.add(inp["var"])

                method_meta[method_name] = {
                    "task_type": ms.task_type,
                    "task_safe": task_safe,
                    "steps": json_steps,
                    "method_inputs": method_inputs_list,
                }

                lines.append("")
            lines.append("")

        content = "\n".join(lines)
        self._write_file("method_sets.metta", content)

        meta_path = self.output_dir / "method_meta.json"
        meta_path.write_text(json.dumps(method_meta, indent=2, sort_keys=True))
        print(
            f"  Written: {meta_path} "
            f"({len(method_meta)} methods, "
            f"{sum(len(m['steps']) for m in method_meta.values())} steps)"
        )
        return content

    def generate_type_hierarchy(self) -> str:
        lines = [
            "; ============================================",
            "; EDAM-aligned type hierarchy for PLN verification",
            "; PLN Deduction rule chains these Inheritance links",
            "; ============================================",
            "",
            "; --- Sequence data formats ---",
            "(Inheritance fastqsanger FASTQ)",
            "(Inheritance fastqillumina FASTQ)",
            "(Inheritance fastqsolexa FASTQ)",
            "(Inheritance fastq_gz FASTQ)",
            "(Inheritance FASTQ SequenceData)",
            "(Inheritance FASTA SequenceData)",
            "(Inheritance fasta_gz FASTA)",
            "(Inheritance SequenceData BioinformaticsData)",
            "",
            "; --- Alignment data formats ---",
            "(Inheritance BAM AlignmentData)",
            "(Inheritance SAM AlignmentData)",
            "(Inheritance CRAM AlignmentData)",
            "(Inheritance AlignmentData BioinformaticsData)",
            "",
            "; --- Variant data formats ---",
            "(Inheritance VCF VariantData)",
            "(Inheritance BCF VariantData)",
            "(Inheritance VariantData BioinformaticsData)",
            "",
            "; --- Genomic interval formats ---",
            "(Inheritance BED GenomicInterval)",
            "(Inheritance GFF GenomicInterval)",
            "(Inheritance GFF3 GFF)",
            "(Inheritance GTF GFF)",
            "(Inheritance GenomicInterval BioinformaticsData)",
            "",
            "; --- Annotation formats ---",
            "(Inheritance GFF AnnotationData)",
            "(Inheritance GFF3 AnnotationData)",
            "(Inheritance AnnotationData BioinformaticsData)",
            "",
            "; --- Tabular and report formats ---",
            "(Inheritance tabular TabularData)",
            "(Inheritance csv TabularData)",
            "(Inheritance tsv TabularData)",
            "(Inheritance TabularData Data)",
            "(Inheritance html ReportData)",
            "(Inheritance json ReportData)",
            "(Inheritance pdf ReportData)",
            "(Inheritance txt TextData)",
            "(Inheritance ReportData Data)",
            "(Inheritance TextData Data)",
            "",
            "; --- Top-level ---",
            "(Inheritance BioinformaticsData Data)",
            "",
            "; --- Format aliases (Galaxy uses these) ---",
            "(Inheritance data Data)",
            "(Inheritance auto Data)",
            "(Inheritance input Data)",
        ]

        content = "\n".join(lines)
        self._write_file("galaxy_types.metta", content)
        return content

    def generate_tool_categories(self, operators: list) -> str:

        lines = [
            "; ============================================",
            "; Tool category membership (from HAS_TOOL edges)",
            "; Tools in the same category can potentially substitute",
            "; for each other within a compound subtask",
            "; ============================================",
            "",
        ]

        # Group by category for readability
        category_tools = {}
        seen = set()

        for op in operators:
            safe_tool = self._safe_name(op.name)
            if not safe_tool or safe_tool in seen:
                continue
            seen.add(safe_tool)

            for cat in op.categories:
                if not cat:
                    continue
                safe_cat = self._safe_name(cat)
                if safe_cat not in category_tools:
                    category_tools[safe_cat] = []
                category_tools[safe_cat].append(safe_tool)

        for cat, tools in sorted(category_tools.items()):
            lines.append(f"; --- {cat} ({len(tools)} tools) ---")
            for tool in sorted(set(tools)):
                lines.append(f"(Member {tool} {cat})")
            lines.append("")

        content = "\n".join(lines)
        self._write_file("tool_categories.metta", content)
        return content

    def _write_file(self, filename: str, content: str):
        """Write content to a file in the output directory."""
        filepath = self.output_dir / filename
        with open(filepath, "w") as f:
            f.write(content)
        print(f"  Written: {filepath} ({len(content)} bytes)")
