"""
Heuristic inference of Galaxy conditional-parameter discriminators.

Galaxy tools have conditional parameters where a "selector" field gates
which sub-fields are valid (e.g. fastp's `single_paired` selects between
`single` and `paired` modes; the `paired_input` sub-field is only valid
when the selector is `paired`). The original IWC/SARS-CoV-2 `.ga` files
carry a `tool_state` JSON per step that sets these selectors. Our Neo4j
ingestion only kept tool identity + dataflow edges, so the selectors
were lost. Galaxy's editor renders ports as red when the conditional
branch is unselected.

This module reconstructs sane discriminators by pattern-matching the
port path the compiler is wiring. Every rule covers a well-known
Galaxy conditional. Tools with conditionals not covered here will
still render with red ports — extend the rule tables as needed.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Port-pattern rules — direct mapping from input port name to state delta
# ---------------------------------------------------------------------------

PORT_RULES: dict[str, dict[str, Any]] = {
    # fastp — paired_input is the SINGLE paired-collection input, not two
    # separate R1/R2 datasets. Galaxy's selector for that mode is
    # 'paired_collection', NOT 'paired' (which is for two distinct files).
    "single_paired|paired_input": {
        "single_paired": {"single_paired_selector": "paired_collection"}
    },
    "single_paired|in1": {
        "single_paired": {"single_paired_selector": "single"}
    },
    "single_paired|in2": {
        "single_paired": {"single_paired_selector": "paired"}
    },
    "single_paired|fastq_in1": {
        "single_paired": {"single_paired_selector": "single"}
    },

    # BWA / minimap2 / bowtie style mappers
    "reference_source|ref_file": {
        "reference_source": {"reference_source_selector": "history"}
    },
    "reference_source|ref": {
        "reference_source": {"reference_source_selector": "history"}
    },
    # Note: BWA's `fastq_input|fastq_input1` is handled by
    # `_infer_bwa_fastq_input` below because the selector value depends on
    # whether the upstream produces a paired *collection* (e.g. fastp's
    # output_paired_coll) or two separate datasets.
    "fastq_input|fastq_input2": {
        "fastq_input": {"fastq_input_selector": "paired"}
    },
    "fastq_input|fastq_inputs": {
        "fastq_input": {"fastq_input_selector": "paired_collection"}
    },

    # SnpEff eff — the discriminator field is `genomeSrc` (verified against
    # tools-iuc/tool_collections/snpeff/snpEff.xml). Value `custom` selects
    # "Custom snpEff database in your history", which is what we have when
    # the snpDb is produced by an in-workflow SnpEff_build step.
    "snpDb|snpeff_db": {"snpDb": {"genomeSrc": "custom"}},
    # SnpEff build — selector for the reference genome source.
    "input_type|input_gbk": {"input_type": {"input_type_selector": "gbk"}},

    # Picard / SAMtools selectors
    "input_format|input": {"input_format": {"input_format_selector": "bam"}},

    # Realign / lofreq
    "regions_source|regions": {
        "regions_source": {"regions_source_selector": "history"}
    },
}


# ---------------------------------------------------------------------------
# MultiQC software_cond — discriminator depends on upstream producer
# ---------------------------------------------------------------------------

# Upstream tool (safe_name) -> (software, output_type, output_kind)
#   software:    discriminator value for software_cond.software
#   output_type: discriminator value for the per-output `type` selector
#                  inside the software_cond branch (None for branches that
#                  expose `input` directly without an `output` repeat).
#   output_kind: how the per-output `type` is shaped inside the branch:
#                  "none"      — branch has no `output` repeat (input is flat)
#                  "select"    — `output[N].type = "<value>"` (flat select)
#                                e.g. picard branch
#                  "conditional" — `output[N].type = {"type": "<value>"}`
#                                  (nested conditional whose selector is also
#                                  named `type`) e.g. samtools, rseqc
#
# Verified against tools-iuc/tools/multiqc/macros.xml.
MULTIQC_BY_UPSTREAM: dict[str, tuple[str, str | None, str]] = {
    # fastp branch — single `input` port, no `output` repeat
    "fastp": ("fastp", None, "none"),
    "FastQC": ("fastqc", None, "none"),
    # picard branch — output[N].type is a FLAT select param
    "MarkDuplicates": ("picard", "markdups", "select"),
    "Picard_MarkDuplicates": ("picard", "markdups", "select"),
    "Picard_CollectInsertSizeMetrics": ("picard", "insertsize", "select"),
    "Picard_CollectGcBias": ("picard", "gcbias", "select"),
    "Picard_CollectRnaSeqMetrics": ("picard", "rnaseqmetrics", "select"),
    "Picard_CollectAlignmentSummaryMetrics": ("picard", "alignment_metrics", "select"),
    "Picard_CollectBaseDistributionByCycle": ("picard", "basedistributionbycycle", "select"),
    # samtools branch — output[N].type is a CONDITIONAL whose selector is type
    "Samtools_stats": ("samtools", "stats", "conditional"),
    "Samtools_flagstat": ("samtools", "flagstat", "conditional"),
    "Samtools_idxstats": ("samtools", "idxstats", "conditional"),
    # rseqc branch — same nested-conditional shape as samtools
    "RSeQC_bam_stat": ("rseqc", "bam_stat", "conditional"),
    "RSeQC_read_GC": ("rseqc", "read_gc", "conditional"),
    # branches with a flat input directly under software_cond
    "Bowtie2": ("bowtie2", None, "none"),
    "HISAT2": ("hisat2", None, "none"),
    "STAR": ("star", None, "none"),
    "salmon": ("salmon", None, "none"),
    "Salmon_quant": ("salmon", None, "none"),
    "kallisto_quant": ("kallisto", None, "none"),
    "trimmomatic": ("trimmomatic", None, "none"),
    "Trim_Galore": ("cutadapt", None, "none"),
    "Cutadapt": ("cutadapt", None, "none"),
    "SnpEff_eff_": ("snpeff", None, "none"),
    "SnpEff_eff": ("snpeff", None, "none"),
    "featureCounts": ("featurecounts", None, "none"),
    "Tophat2": ("tophat", None, "none"),
}

DEFAULT_MULTIQC_SOFTWARE = ("custom_content", None, "none")

_RESULTS_INDEX_RE = re.compile(r"^results_(\d+)\b")

# Output-port substrings that imply the producer is emitting a paired
# collection (one dataset that contains both R1 and R2 elements) — used
# when the inference needs to pick between single-dataset and collection
# modes on a downstream tool.
_PAIRED_COLLECTION_OUTPUTS = ("paired_coll", "paired_collection", "_pair")


def _is_paired_collection_producer(prod: tuple[str, str] | None) -> bool:
    if not prod:
        return False
    upstream_port = (prod[1] or "").lower()
    return any(s in upstream_port for s in _PAIRED_COLLECTION_OUTPUTS)


def _infer_bwa_fastq_input(prod: tuple[str, str] | None) -> dict[str, Any]:
    """
    BWA-MEM (and bowtie2/minimap2 with the same conditional shape) uses
    the SAME port name `fastq_input1` in both `paired` and
    `paired_collection` modes — only the selector value differs:

      paired             - fastq_input1 / fastq_input2 (two datasets)
      paired_collection  - fastq_input1 (one paired collection)

    Pick `paired_collection` when the upstream is producing a paired
    collection (e.g. fastp.output_paired_coll); else default to `paired`.
    """
    selector = (
        "paired_collection" if _is_paired_collection_producer(prod) else "paired"
    )
    return {"fastq_input": {"fastq_input_selector": selector}}


def _multiqc_for(upstream_tool: str | None) -> tuple[str, str | None, str]:
    if not upstream_tool:
        return DEFAULT_MULTIQC_SOFTWARE
    return MULTIQC_BY_UPSTREAM.get(upstream_tool, DEFAULT_MULTIQC_SOFTWARE)


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _snpeff_genome_version(context: dict) -> str:
    """
    SnpEff create_db requires a free-text database NAME (validator rejects
    empty). Galaxy sanitises the field so spaces become underscores. We
    pick the parsed organism when available, else a safe placeholder the
    user can rename in the editor.
    """
    organism = (context.get("organism") or "").strip().lower()
    if organism:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", organism).strip("_") or "custom_db"
    return "custom_db"


# Tool-keyed defaults that don't depend on input ports — e.g. free-text
# fields that have validators rejecting empty values. Each entry is a
# callable that receives the inference context and returns a state delta.
TOOL_DEFAULTS: dict[str, Any] = {
    "SnpEff_build_": lambda ctx: {"genome_version": _snpeff_genome_version(ctx)},
}


def infer_state(
    *,
    tool: str,
    step_inputs: list[dict],
    var_producer: dict[str, tuple[str, str]],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return the state dict to emit under a step's `state:` block based on
    its inputs. Empty dict if no rule matches.

    `step_inputs` are records like {port, var}. `var_producer[var]` is
    `(upstream_tool, output_port)` when the var is produced inside the
    same plan, or absent when the var becomes a workflow-level input.
    `context` carries pipeline-wide info (parsed organism, query hints)
    used by tool-specific defaults.
    """
    state: dict[str, Any] = {}
    ctx = context or {}

    if tool in TOOL_DEFAULTS:
        _deep_merge(state, TOOL_DEFAULTS[tool](ctx))

    for flow in step_inputs:
        port = flow["port"]

        if port == "fastq_input|fastq_input1":
            _deep_merge(state, _infer_bwa_fastq_input(var_producer.get(flow["var"])))
            continue

        if port in PORT_RULES:
            _deep_merge(state, PORT_RULES[port])
            continue

        idx_match = _RESULTS_INDEX_RE.match(port)
        if idx_match and "software_cond" in port:
            idx = int(idx_match.group(1))
            prod = var_producer.get(flow["var"])
            upstream = prod[0] if prod else None
            software, output_type, output_kind = _multiqc_for(upstream)

            results = state.setdefault("results", [])
            while len(results) <= idx:
                results.append({})
            sw_cond: dict[str, Any] = {"software": software}
            if output_kind == "select" and output_type is not None:
                # picard branch — `type` is a FLAT select param. State shape:
                #   output: [{type: "markdups"}]
                sw_cond["output"] = [{"type": output_type}]
            elif output_kind == "conditional" and output_type is not None:
                # samtools / rseqc branch — `type` is a NESTED conditional
                # whose selector is also named `type`. State shape:
                #   output: [{type: {type: "stats"}}]
                sw_cond["output"] = [{"type": {"type": output_type}}]
            _deep_merge(results[idx], {"software_cond": sw_cond})

    return state


# ---------------------------------------------------------------------------
# Workflow-input type / format / label inference
# ---------------------------------------------------------------------------
#
# Galaxy's history-picker filters available datasets by `format:` declared
# on the workflow input. Without it, the user can pick literally anything
# (e.g. a MultiQC stats report into a GenBank slot), which won't fail at
# import time but will fail at run time. Surfacing format + label + doc
# also makes the runtime form self-documenting.
#
# Each entry maps a downstream consumer port to the formats / collection
# shape / label / doc that Galaxy should display for that workflow input.

# port name (lowercase) substrings that imply a paired collection
_PAIRED_COLLECTION_HINTS = (
    "paired_input",
    "paired_coll",
    "paired_fastq",
    "fastq_pair",
)
_COLLECTION_HINTS = ("_collection", "_set", "input_list")


# Per-consumer-port spec: formats are taken verbatim from the upstream
# tool wrapper's <param format="..."> in tools-iuc / tools-devteam.
INPUT_PORT_SPEC: dict[str, dict[str, Any]] = {
    "single_paired|paired_input": {
        "format": ["fastqsanger.gz", "fastqsanger"],
        "label": "Paired-end FASTQ reads (collection)",
        "doc": (
            "Build a paired list collection from your forward + reverse "
            "FASTQ files. Element identifiers should be `forward` and "
            "`reverse` (Galaxy default for paired list builds)."
        ),
    },
    "single_paired|in1": {
        "format": ["fastqsanger.gz", "fastqsanger"],
        "label": "Single-end FASTQ reads",
    },
    "fastq_input|fastq_input1": {
        "format": ["fastqsanger.gz", "fastqsanger", "fasta"],
        "label": "Sequencing reads (FASTQ or FASTA)",
    },
    "reference_source|ref_file": {
        "format": ["fasta"],
        "label": "Reference genome (FASTA)",
    },
    "input_type|input_gbk": {
        "format": ["genbank", "genbank.gz"],
        "label": "Reference annotation (GenBank)",
        "doc": (
            "GenBank file used by SnpEff to build the variant-annotation "
            "database for this organism."
        ),
    },
    "snpDb|snpeff_db": {
        "format": ["snpeffdb"],
        "label": "SnpEff genome database",
    },
    "input1": {
        "format": ["sam", "bam"],
        "label": "SAM or BAM alignment",
    },
    "inputFile": {
        "format": ["bam"],
        "label": "Sorted BAM alignment",
    },
}


def infer_input_type(consumer_port: str) -> dict[str, Any]:
    """
    Return a partial gxformat2 input spec for a workflow-level input,
    based on the name of the first port that consumes it. Includes
    type/collection_type plus format/label/doc when known.
    """
    p_lower = consumer_port.lower()

    # Start with collection-shape inference
    if any(h in p_lower for h in _PAIRED_COLLECTION_HINTS):
        spec: dict[str, Any] = {"type": "collection", "collection_type": "paired"}
    elif any(h in p_lower for h in _COLLECTION_HINTS):
        spec = {"type": "collection", "collection_type": "list"}
    else:
        spec = {"type": "data"}

    # Layer per-port format / label / doc on top
    extras = INPUT_PORT_SPEC.get(consumer_port)
    if extras:
        spec.update(extras)

    return spec
