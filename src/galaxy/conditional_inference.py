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
    "fastq_input|fastq_input1": {
        "fastq_input": {"fastq_input_selector": "paired"}
    },
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

# upstream tool (safe_name) -> (software_value, output_type_value | None)
# software_value: discriminator for software_cond.software
# output_type_value: discriminator for software_cond.output[0].type.type when
#   the software branch has a nested type conditional. None means the branch
#   has only a flat input port (no deeper conditional).
MULTIQC_BY_UPSTREAM: dict[str, tuple[str, str | None]] = {
    # fastp branch — single 'input' port, no nested type
    "fastp": ("fastp", None),
    "FastQC": ("fastqc", None),
    # samtools branch — output[0].type.type selects stats|flagstat|idxstats
    "Samtools_stats": ("samtools", "stats"),
    "Samtools_flagstat": ("samtools", "flagstat"),
    "Samtools_idxstats": ("samtools", "idxstats"),
    # picard branch — output[0].type.type selects MarkDuplicates|InsertSize|...
    "MarkDuplicates": ("picard", "markdups"),
    "Picard_MarkDuplicates": ("picard", "markdups"),
    "Picard_CollectInsertSizeMetrics": ("picard", "insertsize"),
    # other tools — flat
    "Bowtie2": ("bowtie2", None),
    "HISAT2": ("hisat2", None),
    "STAR": ("star", None),
    "salmon": ("salmon", None),
    "Salmon_quant": ("salmon", None),
    "kallisto_quant": ("kallisto", None),
    "RSeQC_bam_stat": ("rseqc", None),
    "trimmomatic": ("trimmomatic", None),
    "Trim_Galore": ("cutadapt", None),
    "Cutadapt": ("cutadapt", None),
    "SnpEff_eff_": ("snpeff", None),
    "SnpEff_eff": ("snpeff", None),
    "featureCounts": ("featurecounts", None),
    "Tophat2": ("tophat", None),
}

DEFAULT_MULTIQC_SOFTWARE = ("custom_content", None)

_RESULTS_INDEX_RE = re.compile(r"^results_(\d+)\b")


def _multiqc_for(upstream_tool: str | None) -> tuple[str, str | None]:
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


def infer_state(
    *,
    tool: str,
    step_inputs: list[dict],
    var_producer: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    """
    Return the state dict to emit under a step's `state:` block based on
    its inputs. Empty dict if no rule matches.

    `step_inputs` are records like {port, var}. `var_producer[var]` is
    `(upstream_tool, output_port)` when the var is produced inside the
    same plan, or absent when the var becomes a workflow-level input.
    """
    state: dict[str, Any] = {}

    for flow in step_inputs:
        port = flow["port"]

        if port in PORT_RULES:
            _deep_merge(state, PORT_RULES[port])
            continue

        idx_match = _RESULTS_INDEX_RE.match(port)
        if idx_match and "software_cond" in port:
            idx = int(idx_match.group(1))
            prod = var_producer.get(flow["var"])
            upstream = prod[0] if prod else None
            software, output_type = _multiqc_for(upstream)

            results = state.setdefault("results", [])
            while len(results) <= idx:
                results.append({})
            sw_cond = {"software": software}
            # Software branches that wrap their data port inside a nested
            # `output[0].type.type` conditional need that discriminator too,
            # otherwise Galaxy renders the wrapping ports red.
            if output_type:
                sw_cond["output"] = [{"type": {"type": output_type}}]
            _deep_merge(results[idx], {"software_cond": sw_cond})

    return state


# ---------------------------------------------------------------------------
# Workflow-input type inference — paired collection vs single dataset
# ---------------------------------------------------------------------------

# port name (lowercase) substrings that imply a paired collection
_PAIRED_COLLECTION_HINTS = (
    "paired_input",
    "paired_coll",
    "paired_fastq",
    "fastq_pair",
)

# port name substrings that imply a generic collection (list)
_COLLECTION_HINTS = (
    "_collection",
    "_set",
    "input_list",
)


def infer_input_type(consumer_port: str) -> dict[str, str]:
    """
    Return a partial gxformat2 input spec for a workflow-level input,
    based on the name of the first port that consumes it.
    """
    p = consumer_port.lower()
    if any(h in p for h in _PAIRED_COLLECTION_HINTS):
        return {"type": "collection", "collection_type": "paired"}
    if any(h in p for h in _COLLECTION_HINTS):
        return {"type": "collection", "collection_type": "list"}
    return {"type": "data"}
