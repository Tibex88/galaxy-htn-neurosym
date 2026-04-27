"""
Recompute tool STVs from real Neo4j usage frequency.

Replaces the mock (STV 0.70 0.30) seed values in tool_atoms.metta with
evidence-based priors computed from how many of the 687 curated workflows
each tool appears in.

Strength   = usage_rank_percentile   — higher rank = better historical signal
Confidence = min(1.0, log(1 + usage_count) / log(1 + max_usage))  — saturates

Run after `scripts/generate_metta.py` to overlay real values onto the
auto-generated tool_atoms.metta. ToolDisplayName / ToolFullID atoms are
emitted by generate_metta.py at extraction time and are not touched here.
"""

import math
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

from src import config
from src.knowledge.neo4j_client import Neo4jClient


TOOL_ATOMS_PATH = Path(config.METTA_DOMAIN_DIR) / "tool_atoms.metta"


def _safe_name(name: str) -> str:
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


def fetch_tool_frequencies() -> dict[str, int]:
    """Count workflow occurrences per tool via Neo4j."""
    with Neo4jClient() as db:
        records = db.query(
            """
            MATCH (t:Tool)<-[:STEP_USES_TOOL]-(:Step)<-[:HAS_STEP]-(w:Workflow)
            RETURN t.name AS tool, count(DISTINCT w) AS wf_count
            ORDER BY wf_count DESC
            """
        )
    return {r["tool"]: r["wf_count"] for r in records if r["tool"]}


def fetch_tool_full_ids() -> dict[str, str]:
    """Map tool name -> full toolshed ID (for BioBlend submission later)."""
    with Neo4jClient() as db:
        records = db.query(
            "MATCH (t:Tool) RETURN t.name AS name, t.id AS full_id"
        )
    out = {}
    for r in records:
        if r["name"] and r["full_id"] and r["name"] not in out:
            out[r["name"]] = r["full_id"]
    return out


def compute_stvs(frequencies: dict[str, int]) -> dict[str, tuple[float, float]]:
    """
    Return {safe_name: (strength, confidence)}.

    - Strength ranks tools within [0.3, 0.9] by descending workflow usage.
    - Confidence saturates with log-scaled usage count.
    """
    if not frequencies:
        return {}

    sorted_tools = sorted(frequencies.items(), key=lambda kv: kv[1], reverse=True)
    max_rank = max(len(sorted_tools) - 1, 1)
    max_count = max(frequencies.values())
    log_max = math.log(1 + max_count)

    stvs: dict[str, tuple[float, float]] = {}
    for idx, (tool_name, count) in enumerate(sorted_tools):
        safe = _safe_name(tool_name)
        strength = 0.9 - 0.6 * (idx / max_rank)
        confidence = min(1.0, math.log(1 + count) / log_max)
        stvs[safe] = (round(strength, 3), round(confidence, 3))
    return stvs


def rewrite_tool_atoms(stvs: dict[str, tuple[float, float]], full_ids: dict[str, str]):
    """
    Replace mock STVs in tool_atoms.metta. ToolFullID atoms are only
    appended if generate_metta.py did not already emit them (legacy
    tool_atoms files without the new emitter).
    """
    text = TOOL_ATOMS_PATH.read_text()
    updated = 0

    def _replace(match: re.Match) -> str:
        nonlocal updated
        safe = match.group(1)
        if safe in stvs:
            s, c = stvs[safe]
            updated += 1
            return f"(= (tool-quality {safe}) (STV {s:.3f} {c:.3f}))"
        return match.group(0)

    text = re.sub(
        r"\(= \(tool-quality ([A-Za-z0-9_]+)\) \(STV [\d.]+ [\d.]+\)\)",
        _replace,
        text,
    )

    added = 0
    if "(ToolFullID " not in text:
        full_id_lines = ["", "; --- ToolFullID atoms (name -> toolshed ID) ---"]
        for name, full_id in full_ids.items():
            safe = _safe_name(name)
            if safe in stvs and full_id:
                full_id_lines.append(f'(ToolFullID {safe} "{full_id}")')
                added += 1
        if added:
            text += "\n" + "\n".join(full_id_lines) + "\n"

    TOOL_ATOMS_PATH.write_text(text)
    return updated, added


def main():
    print("Fetching tool usage frequencies from Neo4j...")
    freqs = fetch_tool_frequencies()
    print(f"  {len(freqs)} tools with at least one workflow appearance.")

    print("Fetching tool full IDs...")
    full_ids = fetch_tool_full_ids()
    print(f"  {len(full_ids)} tools with toolshed IDs.")

    print("Computing evidence-based STVs...")
    stvs = compute_stvs(freqs)

    print(f"Updating {TOOL_ATOMS_PATH}...")
    updated, added = rewrite_tool_atoms(stvs, full_ids)
    print(f"  {updated} tool-quality STVs replaced.")
    print(f"  {added} ToolFullID atoms appended.")

    top = sorted(stvs.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
    print("\nTop 5 by strength:")
    for name, (s, c) in top:
        print(f"  {name:<40}  s={s}  c={c}")


if __name__ == "__main__":
    main()
