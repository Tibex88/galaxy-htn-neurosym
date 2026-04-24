"""
Natural-language query parser.

Maps a free-form researcher query to an ordered list of task types drawn
from the closed vocabulary in config.TASK_CATEGORIES. Uses structured
outputs so the LLM can only emit task names from that vocabulary —
hallucination probability for task names is exactly zero.

Supports Gemini via google-generativeai (preferred) and falls back to a
keyword-based offline parser when no API key is configured, so the
pipeline stays runnable without network access for tests and demos.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from src import config


TASK_VOCAB = config.TASK_CATEGORIES


@dataclass
class ParsedQuery:
    raw_query: str
    tasks: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    organism: str = ""
    source: str = "offline"

    def render(self) -> str:
        chain = " -> ".join(self.tasks) if self.tasks else "(no tasks)"
        return (
            f"query={self.raw_query!r}\n"
            f"  source={self.source}  organism={self.organism or '-'}\n"
            f"  inputs={self.inputs or []}\n"
            f"  tasks: {chain}"
        )


_KEYWORD_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(qc|quality|trimm|fastqc|adapter)\b", re.I), "Quality Control"),
    (re.compile(r"\b(align|map|bwa|bowtie|hisat|minimap)\b", re.I), "Mapping"),
    (re.compile(r"\bassembl", re.I), "Assembly"),
    (re.compile(r"\b(variant|snp|indel|mutation|exome|wes|wgs)\b", re.I), "Variant Calling"),
    (re.compile(r"\b(rna-?seq|differential expression|transcriptom|dge)\b", re.I), "RNA Analysis"),
    (re.compile(r"\b(annotat|snpeff|vep)\b", re.I), "Annotation"),
    (re.compile(r"\b(metagenom|16s|amplicon|microbiome)\b", re.I), "Metagenomic Analysis"),
    (re.compile(r"\b(chip-?seq|atac-?seq|peak)\b", re.I), "Peak Calling"),
    (re.compile(r"\bphylogen", re.I), "Phylogenetics"),
    (re.compile(r"\b(proteom|mass spec|ms/ms)\b", re.I), "Proteomics"),
    (re.compile(r"\bmetabolom", re.I), "Metabolomics"),
    (re.compile(r"\b(single-?cell|scrna)\b", re.I), "Single-cell"),
    (re.compile(r"\b(methylat|bisulfite|epigenet)\b", re.I), "Epigenetics"),
    (re.compile(r"\b(imag|microscop)\b", re.I), "Imaging"),
]

_ORGANISM_HINTS = [
    ("human", "homo sapiens"),
    ("mouse", "mus musculus"),
    ("rat", "rattus norvegicus"),
    ("zebrafish", "danio rerio"),
    ("yeast", "saccharomyces"),
    ("ecoli", "escherichia coli"),
    ("arabidopsis", "arabidopsis"),
]

_INPUT_HINTS = [
    ("fastq", "FASTQ"),
    ("bam", "BAM"),
    ("vcf", "VCF"),
    ("fasta", "FASTA"),
    ("exome", "FASTQ"),
    ("rna-seq", "FASTQ"),
    ("paired-end", "FASTQ"),
    ("single-end", "FASTQ"),
]


def _offline_parse(query: str) -> ParsedQuery:
    ql = query.lower()
    tasks = []
    seen = set()
    for pattern, task in _KEYWORD_HINTS:
        if pattern.search(query) and task not in seen and task in TASK_VOCAB:
            tasks.append(task)
            seen.add(task)

    # Quality Control is almost always the first step, prepend if inputs suggest reads
    if any(x in ql for x in ("fastq", "rna-seq", "exome", "wgs", "reads")):
        if "Quality Control" in TASK_VOCAB and "Quality Control" not in seen:
            tasks.insert(0, "Quality Control")
            seen.add("Quality Control")

    organism = ""
    for needle, canon in _ORGANISM_HINTS:
        if needle in ql:
            organism = canon
            break

    inputs = []
    for needle, canon in _INPUT_HINTS:
        if needle in ql and canon not in inputs:
            inputs.append(canon)

    return ParsedQuery(
        raw_query=query,
        tasks=tasks,
        inputs=inputs,
        organism=organism,
        source="offline",
    )


_GEMINI_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "description": (
                "Ordered list of task types drawn ONLY from the task vocabulary. "
                "List them in execution order. Begin with Quality Control if the "
                "input is raw sequencing reads."
            ),
            "items": {"type": "string", "enum": TASK_VOCAB},
        },
        "inputs": {
            "type": "array",
            "description": "Expected input data formats (e.g. FASTQ, BAM, VCF).",
            "items": {"type": "string"},
        },
        "organism": {
            "type": "string",
            "description": "Species or organism if mentioned, else empty string.",
        },
    },
    "required": ["tasks"],
}


def _gemini_parse(query: str, api_key: str) -> ParsedQuery:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-2.5-flash",
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": _GEMINI_SCHEMA,
        },
    )

    prompt = (
        "You are a bioinformatics workflow planner. Decompose the user's "
        "request into an ordered list of task types using ONLY the provided "
        "vocabulary.\n\n"
        f"Vocabulary: {TASK_VOCAB}\n\n"
        f"Request: {query}\n\n"
        "Return valid JSON matching the schema. Tasks must be in execution "
        "order. Do not invent task names outside the vocabulary."
    )

    resp = model.generate_content(prompt)
    payload = json.loads(resp.text)
    tasks = [t for t in payload.get("tasks", []) if t in TASK_VOCAB]

    return ParsedQuery(
        raw_query=query,
        tasks=tasks,
        inputs=payload.get("inputs", []) or [],
        organism=payload.get("organism", "") or "",
        source="gemini",
    )


class QueryParser:
    def __init__(self, prefer_llm: bool = True):
        self.prefer_llm = prefer_llm
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    def parse(self, query: str) -> ParsedQuery:
        if self.prefer_llm and self.api_key:
            try:
                return _gemini_parse(query, self.api_key)
            except Exception as e:
                print(f"  [parser] LLM call failed ({e}); falling back to offline.")
        return _offline_parse(query)
