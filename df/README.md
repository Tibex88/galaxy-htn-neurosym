# df — workflow diff utilities

Compare a generated Galaxy workflow against the KB methods it could have
been derived from. Useful for:

1. **Validation** — confirm the picked HTN method actually matches what
   the user asked for.
2. **Discovery** — surface alternative KB methods that were close
   competitors.
3. **Audit** — see what the compiler added on top of the raw KB method
   (workflow inputs, conditional state, format constraints).

This package is intentionally isolated from `src/` so adding it does not
risk breaking the planner / compiler / reasoner.

## Usage

```bash
# Human-readable summary
python -m df.workflow_differ generated/<ts>/htn_generated_workflow.ga

# Top 10 instead of default 5
python -m df.workflow_differ generated/<ts>/htn_generated_workflow.ga --top 10

# JSON for downstream consumption
python -m df.workflow_differ generated/<ts>/htn_generated_workflow.ga --json

# Works on .gxwf.yml too
python -m df.workflow_differ generated/<ts>/htn_generated_workflow.gxwf.yml
```

## How similarity is computed

Two-pass:

1. **Coarse (Jaccard)** — multiset overlap on tool safe-names. Fast and
   robust to ordering. Used to rank candidates.
2. **Fine (LCS)** — `SequenceMatcher.ratio()` over the ordered tool
   sequence. Tiebreaks within similarly-scoring candidates.

Combined score = `0.7 * jaccard + 0.3 * sequence_ratio`.

## What the diff shows per candidate

- **score** — combined similarity (1.0 = identical tool multiset and order)
- **common** — tools present in both
- **only_in_generated** — tools present in the workflow but not the KB method
- **only_in_method** — tools the KB method has but the workflow dropped
- **sequence ops** — number of equal / differing positions in the aligned
  tool sequence
- **compiler additions** — fields the compiler emitted that the raw KB
  method doesn't carry (workflow inputs, conditional state count)

## Caveat

Today's pipeline emits a chosen KB method **verbatim** — the planner
picks one method from `method_meta.json` and the compiler reproduces its
tool sequence with metadata around it. So the top match for any
generated workflow is normally the source method with score ≈ 1.0, with
runner-up methods scoring lower. If you want the system to *modify*
methods to fit a query (e.g. swap one tool for a substitute), that's a
separate piece of work in the planner and not done by this differ.
