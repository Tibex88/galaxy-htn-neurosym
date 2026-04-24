# HTN + PLN + LLM: Neuro-Symbolic Workflow Generation for Galaxy

A neuro-symbolic AI system that automatically generates executable [Galaxy](https://galaxyproject.org/) bioinformatics workflows from natural language requests, combining the deterministic rigor of **Hierarchical Task Network (HTN)** planning with the probabilistic reasoning of **Probabilistic Logic Networks (PLN)** and the semantic understanding of **Large Language Models (LLMs)**.

Built on the [OpenCog Hyperon](https://hyperon.opencog.org/) ecosystem using [MeTTa](https://github.com/trueagi-io/hyperon-experimental) and [PeTTa](https://github.com/trueagi-io/PeTTa).

## The Problem

Bioinformaticians spend significant time manually constructing analysis workflows -- selecting the right tools, ordering them correctly, ensuring data format compatibility, and configuring parameters. While LLMs can generate plausible-sounding pipelines, they hallucinate non-existent tools and produce biologically invalid sequences at unacceptable rates. Research shows LLMs alone achieve **0% correct hierarchical decompositions** on planning benchmarks.

## The Approach

This system constrains the LLM to what it's good at (understanding natural language) and delegates what it's bad at (planning and reasoning) to formal AI systems:

- **LLM** translates vague human requests ("find mutations in my exome data") into formal task names using structured outputs -- hallucination probability for task names is exactly zero
- **HTN Planner** decomposes high-level tasks into concrete tool sequences using methods extracted from a knowledge graph of real human-authored workflows
- **PLN** selects the optimal tools when alternatives exist (BWA-MEM2 vs Bowtie2 vs HISAT2), verifies pipeline validity through ontological reasoning, and continuously learns from execution outcomes
- The output is a fully executable Galaxy workflow (Format 2 YAML) ready to run on any Galaxy server

## Architecture

```
  Natural Language Query
          |
          v
  +-----------------+
  | LLM Parser      |  "Find SNPs in paired-end exome data"
  | (Structured     |         |
  |  Outputs)       |         v
  +-----------------+  [quality_control, read_trimming,
          |             read_alignment, variant_calling]
          v
  +-----------------+
  | HTN Planner     |  Decomposes each task using methods
  | (Methods from   |  extracted from 687 real workflows
  |  Neo4j KG)      |  in the knowledge graph
  +-----------------+
          |
          v
  +-----------------+
  | PLN Reasoning   |  Selects best tools via TruthValues,
  | (MeTTa/PeTTa)   |  verifies pipeline type-safety via
  |                 |  EDAM ontology Inheritance chains
  +-----------------+
          |
          v
  +-----------------+
  | Galaxy Compiler |  Generates executable .gxwf.yml
  | (Format 2 YAML) |  with proper DAG structure
  +-----------------+
          |
          v
  Executable Galaxy Workflow
```

## Knowledge Graph

The system is backed by a Neo4j knowledge graph containing:

- **166,544 nodes** -- Tools, Workflows, Steps, Inputs, Outputs, and their type annotations
- **241,682 relationships** -- Data flow edges, tool-step bindings, category memberships, and similarity links
- **687 curated workflows** from the [Intergalactic Workflow Commission](https://github.com/galaxyproject/iwc), [WorkflowHub](https://workflowhub.eu/), Galaxy Training Network, and other sources
- **15,511 Galaxy tools** with their input/output specifications

## Why PLN

PLN provides mathematically grounded reasoning under uncertainty. Instead of rigid heuristic scores, each tool carries a **TruthValue** (strength, confidence) that is updated through Bayesian evidence revision as the system observes real execution outcomes. PLN's inference rules (Deduction, Revision, Abduction, Induction) enable:

- **Tool selection**: Combining historical success rates, type compatibility, and evidence transferred from similar tools
- **Pipeline verification**: Chaining format compatibility checks through the EDAM ontology type hierarchy
- **Continuous learning**: Tools that fail get deprioritized; tools that succeed get reinforced -- automatically, without manual intervention

Performance is achieved through [PeTTa](https://github.com/trueagi-io/PeTTa) (MeTTa-to-Prolog transpiler, ~500x speedup) with [MORK](https://github.com/trueagi-io/MORK) as a future acceleration path.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Probabilistic reasoning | [PLN](https://github.com/trueagi-io/PLN) on [MeTTa](https://github.com/trueagi-io/hyperon-experimental) / [PeTTa](https://github.com/trueagi-io/PeTTa) |
| Knowledge graph | [Neo4j](https://neo4j.com/) |
| HTN planning | Python (GTPyhop-style forward decomposition) |
| LLM integration | Gemini or other provider with Structured Outputs |
| Workflow platform | [Galaxy](https://galaxyproject.org/) via [BioBlend](https://bioblend.readthedocs.io/) |
| DAG analysis | [rustworkx](https://github.com/Qiskit/rustworkx) |
| Workflow compilation | Galaxy Format 2 via [gxformat2](https://github.com/galaxyproject/gxformat2) |

## Project Status

**Active development -- Proof of Concept phase.**

Currently implementing the HTN method extraction pipeline (Neo4j -> MeTTa domain) and PLN reasoning layer.

## Why We Moved From PLN-Only To HTN+PLN

An earlier iteration (PLN_workflow) tried to build workflows by PLN reasoning alone: a greedy "predict next tool" over historical co-occurrence. Live runs surfaced five structural limitations:

1. **Greedy, no lookahead.** `winner = candidates[0]` per step; one bad early pick poisons the chain.
2. **No type verification.** The assembler's regex parses only `(tool, strength, confidence)` — EDAM formats, tool ports, and input/output compatibility are never consulted. A real run for "variant calling" produced `pilon -> BWA-MEM -> ... -> NetCDF_xarray_map_plotting -> Image_Montage` — climate-science and image-processing tools leaking into a genomics chain.
3. **No parallel paths.** Output is `workflow_chain: list[str]` joined with `" -> "` — DAG topology cannot be expressed; fork/join structure is lost.
4. **No backtracking.** On dead-end, the assembler `break`s and returns a partial chain — no alternative search, no re-anchoring.
5. **No hierarchical decomposition.** There is no `method-for` construct, no compound-task machinery. "Variant calling" cannot be structurally broken down into QC → align → dedupe → call → annotate.

HTN planning targets all five: methods are named decompositions, method sets give alternatives to score, data-flow edges preserve parallelism, axioms carry type info for verification, and the method-set abstraction is itself the hierarchy.

## Roadmap

The HTN+PLN runtime is delivered across three sequential branches off `main`. Each branch is self-contained and produces a demo-able deliverable.

### Branch 1 — `feat/phase1-htn-mvp`  *(minimum viable HTN planner)*

**Goal:** task name in, ranked tool chain out.

- `src/pln/reasoner.py` — Hyperon-backed KB with PLN scoring (expectation, revision, modus ponens, compound score)
- `src/htn/planner.py` — task → method-set lookup → PLN-scored best method → ordered tool list
- `main.py` — CLI orchestrator: `python main.py "Variant Calling"`
- `scripts/test_htn_planner.py` — sanity check: variant-calling chain contains a variant caller

**Outcome:** Eliminates failures 1, 4, and 5 from the PLN-only baseline. Real variant-calling method wins: `fastp -> BWA-MEM -> MarkDuplicates -> Realign -> Call_variants -> SnpEff`.

### Branch 2 — `feat/phase2-full-pipeline`  *(end-to-end NL → runnable YAML)*

**Goal:** natural-language query in, `.gxwf.yml` runnable on any Galaxy server out.

- `src/parsing/parser.py` — LLM query parser with structured output; emits task list from `TASK_CATEGORIES` closed vocabulary (hallucination probability = 0)
- `src/htn/planner.py` upgrade — multi-task decomposition, precondition checking (does the current data type match the first step's input port?), EDAM-axiom type verification between sequential steps, method re-selection on type mismatch (no-backtrack behaviour replaced with type-guided selection)
- `src/galaxy/compiler.py` — consumes `LiftedMethod` + data-flow edges, emits gxformat2 YAML with proper `input_connections` preserving DAG structure
- `scripts/compute_real_stvs.py` — replaces mock `(STV 0.70 0.30)` with frequency-based truth values from Neo4j tool usage across the 687-workflow corpus
- `src/extraction/metta_generator.py` update — emits `(ToolFullID safe_name "full/tool/id")` atoms needed for BioBlend submission
- `main.py` upgrade — NL query → parser → planner (per task) → compiler → YAML file

**Outcome:** Eliminates failures 2 and 3. Type compatibility is verified before the chain is emitted; parallel branches and data-flow joins are preserved all the way to gxformat2.

### Branch 3 — `feat/phase3-chathtn-online-learning`  *(the ChatHTN contribution)*

**Goal:** the method library grows during planning when the seed KB has gaps.

- `src/htn/method_proposer.py` — when PLN scores all alternatives below threshold OR no method exists for a task, prompt the LLM with the tool catalog + type hierarchy + 3 example successful methods; LLM emits a proposed decomposition
- `src/htn/method_verifier.py` — accept only if: (a) every proposed tool exists in `tool_atoms.metta`, (b) sequential type-compat holds via EDAM `Inheritance` chains, (c) the data-flow DAG is well-formed (rustworkx DAG check)
- `src/htn/method_persistence.py` — verified proposals are appended to `method_sets.metta` with an initial low-confidence STV; become first-class methods on next run
- `src/htn/feedback.py` — after Galaxy execution, success bumps method STVs via PLN revision; repeated failure deprioritizes
- `src/htn/online_learner.py` — orchestrator that closes the loop: detect gap → propose → verify → persist → score → plan with augmented library
- `main.py` upgrade — invokes online learner when planner returns an empty or low-score plan; library grows transparently

**Outcome:** The system acquires methods it was never seeded with, verified against the same axiom layer that guards the static pipeline. This is the paper's core contribution: LLM-as-method-proposer, symbolic-as-verifier, online learning as the acquisition mechanism.

## References

- [ChatHTN: Online Learning of HTN Methods for LLM-HTN Planning](https://arxiv.org/abs/2505.11814) (NeuS 2025)
- [CurricuLAMA: Learning HTN Methods from Landmarks](https://arxiv.org/abs/2404.06325) (FLAIRS 2024)
- [A Roadmap for LLMs in Hierarchical Planning](https://arxiv.org/abs/2501.08068) (AAAI Workshop 2025)
- [From Prompt to Pipeline: LLMs for Bioinformatics Workflows](https://arxiv.org/abs/2507.20122) (2025)
- [XGrammar 2: Dynamic Structured Generation for Agentic LLMs](https://arxiv.org/abs/2601.04426) (2026)
- [OpenCog Hyperon](https://arxiv.org/abs/2310.18318)

## License

MIT

---

*Built at [iCog Labs](https://icog-labs.com/) for the [SingularityNET](https://singularitynet.io/) / [Hyperon](https://hyperon.opencog.org/) ecosystem.*
