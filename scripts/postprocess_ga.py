"""
Post-process a Galaxy native .ga JSON file to fix fields that
gxformat2's gxwf-to-native leaves blank/missing — fields that Galaxy's
workflow importer requires (or strongly prefers) and rejects with HTTP 500
when missing.

Fixes applied:

1. `tool_version`        derived from the trailing segment of the toolshed
                          tool_id (e.g. .../fastp/0.19.5+galaxy1 -> 0.19.5+galaxy1).
2. `name` (short)         set to the tool's display_name from
                          metta/domain/tool_meta.json (e.g. "fastp",
                          "Map with BWA-MEM"). Galaxy renders this in the
                          editor; the bare toolshed URI looks ugly but
                          also confuses some importer code paths.
3. `content_id`           same as `tool_id` for tool steps (Galaxy expects
                          this for installed-tool resolution).
4. `workflow_outputs: []` empty array on every step (importer requires it).
5. `errors: null`         standard field, harmless when missing but some
                          importer versions rely on it being present.
6. `inputs: []` / `outputs: []` empty arrays on tool steps (IWC convention).
7. `post_job_actions: {}` empty dict on tool steps if absent.
8. `uuid`                 mint a UUID per step if missing (Galaxy's
                          importer uses these as stable identifiers).
9. `position`             default {top, left} if missing.

Usage:
    python scripts/postprocess_ga.py path/to/wf.ga [--in-place]
    python scripts/postprocess_ga.py path/to/wf.ga --out path/to/wf.fixed.ga
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, ".")

from src import config


_TOOL_META_PATH = Path(config.METTA_DOMAIN_DIR) / "tool_meta.json"


def _load_tool_meta() -> dict[str, dict]:
    if not _TOOL_META_PATH.exists():
        return {}
    return json.loads(_TOOL_META_PATH.read_text())


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
    )


def _short_name_for(tool_id: str, label: str | None, tool_meta: dict) -> str:
    """
    Recover a tool's short name from its toolshed tool_id.
    1. Try matching tool_meta.json by full_id.
    2. Fall back to the second-to-last segment of the toolshed path
       (.../<repo>/<tool_short>/<version> -> <tool_short>).
    """
    if not tool_id:
        return label or "tool"
    for safe, meta in tool_meta.items():
        if meta.get("full_id") == tool_id:
            return meta.get("display_name") or safe
    parts = tool_id.split("/")
    if len(parts) >= 2:
        return parts[-2]
    return label or tool_id


def _version_from_tool_id(tool_id: str) -> str:
    if not tool_id:
        return ""
    return tool_id.rsplit("/", 1)[-1]


def fix_workflow(ga: dict, tool_meta: dict) -> dict:
    if "a_galaxy_workflow" not in ga:
        ga["a_galaxy_workflow"] = "true"
    if "format-version" not in ga:
        ga["format-version"] = "0.1"
    if "uuid" not in ga:
        ga["uuid"] = str(uuid.uuid4())
    ga.setdefault("annotation", "")
    ga.setdefault("tags", [])

    steps = ga.get("steps") or {}
    for sid, step in steps.items():
        is_tool = step.get("type") == "tool"
        is_input = step.get("type") in ("data_input", "data_collection_input")

        if "annotation" not in step:
            step["annotation"] = ""
        if "errors" not in step:
            step["errors"] = None
        step.setdefault("position", {"left": 10 + 220 * int(sid), "top": 100})
        step.setdefault("workflow_outputs", [])
        step.setdefault("uuid", str(uuid.uuid4()))

        if is_tool:
            tool_id = step.get("tool_id") or ""
            short = _short_name_for(tool_id, step.get("label"), tool_meta)
            # Galaxy uses `name` as the human-readable tool name in the editor
            # even though it's structurally redundant with `tool_id`.
            step["name"] = short
            if not step.get("tool_version"):
                step["tool_version"] = _version_from_tool_id(tool_id)
            step.setdefault("content_id", tool_id)
            step.setdefault("inputs", [])
            step.setdefault("outputs", [])
            step.setdefault("post_job_actions", {})
        elif is_input:
            # data_input / data_collection_input: Galaxy convention is
            #   name  = "Input dataset" / "Input dataset collection"
            #   label = the user's chosen input name
            # The label should already be set by the converter; only
            # populate name when missing.
            if not step.get("name"):
                step["name"] = (
                    "Input dataset collection"
                    if step["type"] == "data_collection_input"
                    else "Input dataset"
                )
            step.setdefault("inputs", [])
            step.setdefault("outputs", [])
            step["tool_id"] = None
            step["tool_version"] = None
            step["content_id"] = None

    return ga


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ga_path")
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    src = Path(args.ga_path)
    if not src.exists():
        ap.error(f"not found: {src}")

    ga = json.loads(src.read_text())
    tool_meta = _load_tool_meta()
    fixed = fix_workflow(ga, tool_meta)

    if args.in_place:
        dst = src
    elif args.out:
        dst = Path(args.out)
    else:
        dst = src.with_suffix(".fixed.ga")
    dst.write_text(json.dumps(fixed, indent=4))
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
