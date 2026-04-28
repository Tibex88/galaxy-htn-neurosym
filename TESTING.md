# Testing & Demo Commands

Every phase can be exercised both **offline** (deterministic, no network)
and **online** (Gemini-backed). Flip between them with `--no-llm` on the
parser side and `--learn` on the online-learning side.

## Prerequisites

```bash
cd /Users/m4pro/git/galaxy-htn-neurosym

# Python deps
pip install -r requirements.txt
pip install hyperon==0.2.10 rustworkx matplotlib pyyaml
# only needed for online paths:
pip install google-generativeai

# Environment (online paths only)
export GEMINI_API_KEY="your-key-here"
# (optional, for compute_real_stvs.py and BioBlend)
export NEO4J_URI="bolt://37.27.231.93:7990"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="abc12345"
export GALAXY_URL="https://usegalaxy.org"
export GALAXY_API_KEY="your-galaxy-key"

# Generated MeTTa domain (if not already present)
python scripts/generate_metta.py
```

---

## Phase 1 — HTN MVP

Single-task planning, PLN method selection. No LLM involvement at all,
so no online/offline split.

```bash
# list available task types from the KB
python main.py --list-tasks

# plan a single task (best-scored method)
python main.py --task "Variant Calling"
python main.py --task "RNA Analysis"
python main.py --task "Assembly"

# show top-K alternatives ranked by PLN compound score
python main.py --task "Variant Calling" --alternatives 5

# run the phase 1 sanity test
python scripts/test_htn_planner.py
```

Expected: `Variant Calling` returns `fastp -> BWA-MEM -> MarkDuplicates -> ... -> Call_variants -> SnpEff` with `score=0.560`.

---

## Phase 2 — NL query to gxformat2 YAML

### Offline (keyword parser — deterministic, no network)

```bash
# NL query, offline parse, writes to generated/<timestamp>/
python main.py --no-llm "call variants on paired-end human exome data"
python main.py --no-llm "RNA-seq differential expression in mouse samples"
python main.py --no-llm "assemble bacterial genome from nanopore reads"

# explicit output path
python main.py --no-llm --out /tmp/my_wf.gxwf.yml "variant calling on WGS data"

# custom workflow name in the YAML header
python main.py --no-llm --name "exome_vc_v1" "variant calling on exome data"

# run the phase 2 integration test
python scripts/test_phase2.py
```

### Online (Gemini parser — LLM-backed, structured output)

```bash
# Requires GEMINI_API_KEY. If unset, silently falls back to offline.
python main.py "identify SNPs and indels in tumor-normal paired-end data"
python main.py "profile microbiome composition from 16S amplicon sequencing"
python main.py "quantify gene expression across conditions in human RNA-seq"
```

### One-time: replace mock STVs with real Neo4j usage frequency

```bash
# Reads tool usage counts from Neo4j, rewrites tool_atoms.metta with
# evidence-based priors, appends ToolFullID atoms for BioBlend.
python scripts/compute_real_stvs.py
```

---

## Phase 3 — ChatHTN online method learning

### Offline (splice-based proposer, no network)

```bash
# plan a task with online learning enabled; if no good method exists or
# best score is below threshold, offline proposer synthesizes one from
# existing examples, verifier checks it, persistence writes it back.
python main.py --task "RNA Analysis" --learn

# full NL pipeline with online learning on gaps
python main.py --no-llm --learn "novel task the seed KB does not cover"

# run the phase 3 integration test
python scripts/test_phase3.py
```

### Online (Gemini proposer — real LLM method synthesis)

```bash
# Requires GEMINI_API_KEY. Proposer calls Gemini with the tool catalog
# (from tool_atoms.metta) as a closed vocabulary — hallucinated tool
# names are caught and rejected by the verifier.
python main.py --task "Epigenetics" --learn

# full pipeline: LLM parser + LLM proposer + verifier + persistence
python main.py --learn "detect differentially methylated regions in bisulfite-seq data"
```

### Feedback — PLN Revision from execution outcomes

```bash
# success evidence bumps STVs toward 1.0 via PLN Revision
python main.py --feedback --outcome success \
  --tools "fastp,Map_with_BWA_MEM,MarkDuplicates,Call_variants,SnpEff_eff_"

# failure evidence deprioritizes
python main.py --feedback --outcome failure --tools "Flaky_Tool_X"

# inspect updated STV (it will be different on next plan)
python -c "
from src.pln.reasoner import PLNReasoner
r = PLNReasoner().load()
print('fastp:', r.get_tool_stv('fastp'))
"
```

---

## Visualize generated workflows

```bash
# writes SVG alongside the YAML (same stem)
python scripts/visualize_workflow.py generated/<timestamp>/htn_generated_workflow.gxwf.yml

# explicit output path + interactive window
python scripts/visualize_workflow.py generated/<timestamp>/my.gxwf.yml --out /tmp/vis.png --show

# pipeline one-liner: generate + visualize
python main.py --no-llm "call variants on exome data" \
  && python scripts/visualize_workflow.py generated/$(ls -t generated/ | head -1)/*.gxwf.yml
```

Rendering: input nodes blue, tool steps green, edges labeled with downstream port names, columns are topological-depth layers.

---

## End-to-end demos

### Offline demo (no network required)

```bash
python main.py --no-llm --learn "assemble and annotate a bacterial genome" \
  && LATEST=$(ls -t generated/ | head -1) \
  && python scripts/visualize_workflow.py generated/$LATEST/*.gxwf.yml \
  && open generated/$LATEST/*.svg
```

### Online demo (Gemini + online learning)

```bash
export GEMINI_API_KEY="..."
python main.py --learn "call somatic variants in paired tumor-normal WGS data" \
  && LATEST=$(ls -t generated/ | head -1) \
  && python scripts/visualize_workflow.py generated/$LATEST/*.gxwf.yml \
  && open generated/$LATEST/*.svg
```

---

## Branch layout

```
main
 └─ feat/phase1-htn-mvp            (task -> tool chain)
     └─ feat/phase2-full-pipeline  (NL query -> gxformat2 YAML)
         └─ feat/phase3-chathtn-online-learning  (method gap -> propose/verify/persist)
```

Switch branches to demo earlier phases in isolation:

```bash
git checkout feat/phase1-htn-mvp       # MVP only
git checkout feat/phase2-full-pipeline # static pipeline, no online learning
git checkout feat/phase3-chathtn-online-learning  # full system (default)
```
