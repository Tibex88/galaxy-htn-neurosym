"""
PLN reasoning over the Galaxy MeTTa domain.

Loads the generated domain files (tool_atoms, method_sets, galaxy_types,
tool_categories) into a Hyperon AtomSpace and provides PLN-scored
lookups: tool truth values, method candidates per task, and compound
method reliability via geometric mean of per-tool expectations.

PLN truth-value formulas (Expectation, Revision, Modus Ponens) run in
Python. Hyperon is used purely as the KB query backend.
"""

import json
import math
import re
import sys
from pathlib import Path

try:
    from hyperon import MeTTa
except ImportError:
    print("Error: hyperon not installed. Run: pip install hyperon==0.2.10")
    sys.exit(1)

from src import config

DOMAIN_DIR = Path(config.METTA_DOMAIN_DIR)

DOMAIN_FILES = [
    "tool_atoms.metta",
    "galaxy_types.metta",
    "tool_categories.metta",
    "method_sets.metta",
]

UNINFORMED_PRIOR = (0.5, 0.1)


def _parse_stv(s: str) -> tuple[float, float] | None:
    m = re.search(r"[Ss][Tt][Vv]\s+([\d.]+)\s+([\d.]+)", s)
    return (float(m.group(1)), float(m.group(2))) if m else None


def _expectation(s: float, c: float) -> float:
    """PLN expectation: E = c * (s - 0.5) + 0.5"""
    return c * (s - 0.5) + 0.5


def _compound_score(expectations: list[float]) -> float:
    """Geometric mean of per-tool expectations."""
    if not expectations:
        return 0.0
    return math.exp(
        sum(math.log(max(e, 0.001)) for e in expectations) / len(expectations)
    )


class PLNReasoner:
    def __init__(self):
        self.metta = MeTTa()
        self._loaded = False
        self._stv_cache: dict[str, tuple[float, float]] = {}
        # Sidecar metadata keyed by safe_name. Holds display_name / full_id /
        # owner so the compiler can resolve gxformat2 tool_id values without
        # putting quoted strings into the MeTTa atomspace (hyperon 0.2.10's
        # trie index panics on large quoted-string spaces).
        self._tool_meta: dict[str, dict[str, str]] = {}
        # Sidecar per-method step data (steps + method_inputs), keyed by
        # method_name. Lives in JSON for the same trie-panic reason.
        self._method_meta: dict[str, dict] = {}

    def load(self) -> "PLNReasoner":
        if self._loaded:
            return self

        missing = []
        for fname in DOMAIN_FILES:
            path = DOMAIN_DIR / fname
            if path.exists():
                try:
                    self.metta.run(path.read_text())
                    print(f"  Loaded {fname}")
                except Exception as e:
                    print(f"  Warning: {fname}: {e}")
            else:
                missing.append(fname)

        if missing:
            print(f"\n  Missing domain files: {missing}")
            print("  Run:  python scripts/generate_metta.py\n")

        meta_path = DOMAIN_DIR / "tool_meta.json"
        if meta_path.exists():
            try:
                self._tool_meta = json.loads(meta_path.read_text())
                print(f"  Loaded tool_meta.json ({len(self._tool_meta)} entries)")
            except Exception as e:
                print(f"  Warning: tool_meta.json parse failed: {e}")

        method_meta_path = DOMAIN_DIR / "method_meta.json"
        if method_meta_path.exists():
            try:
                self._method_meta = json.loads(method_meta_path.read_text())
                step_count = sum(
                    len(m.get("steps", []))
                    for m in self._method_meta.values()
                )
                print(
                    f"  Loaded method_meta.json "
                    f"({len(self._method_meta)} methods, {step_count} steps)"
                )
            except Exception as e:
                print(f"  Warning: method_meta.json parse failed: {e}")

        self._loaded = True
        return self

    def _q(self, expr: str) -> list:
        try:
            results = self.metta.run(expr)
            return results[0] if results else []
        except Exception:
            return []

    def get_tool_stv(self, tool_name: str) -> tuple[float, float]:
        if tool_name in self._stv_cache:
            return self._stv_cache[tool_name]
        atoms = self._q(f"!(match &self (= (tool-quality {tool_name}) $stv) $stv)")
        for atom in atoms:
            stv = _parse_stv(str(atom))
            if stv:
                self._stv_cache[tool_name] = stv
                return stv
        return UNINFORMED_PRIOR

    def get_tool_full_id(self, safe_name: str) -> str | None:
        meta = self._tool_meta.get(safe_name) or {}
        full_id = meta.get("full_id")
        return full_id or None

    def get_tool_display_name(self, safe_name: str) -> str | None:
        """Original tool display name (with spaces / dashes preserved)."""
        meta = self._tool_meta.get(safe_name) or {}
        return meta.get("display_name") or None

    def resolve_tool_id(self, safe_name: str) -> str:
        """
        Galaxy-facing tool ID resolution.
        Preference order: full_id (toolshed) -> display_name -> safe_name.
        """
        full_id = self.get_tool_full_id(safe_name)
        if full_id:
            return full_id
        display = self.get_tool_display_name(safe_name)
        if display:
            return display
        return safe_name

    def get_methods_for_task(self, task_type: str) -> list[str]:
        atoms = self._q(
            f"!(match &self (= (method-for {task_type} $m) $body) $m)"
        )
        return [str(a).strip() for a in atoms if str(a).strip()]

    def get_tools_for_method(self, method_name: str) -> list[str]:
        atoms = self._q(f"!(match &self (MethodUsesTool {method_name} $t) $t)")
        return [str(a).strip() for a in atoms if str(a).strip()]

    def get_method_sequence(self, method_name: str) -> list[str]:
        """Ordered tool list from (MethodSequence (t1 t2 ...))"""
        atoms = self._q(
            f"!(match &self (= (method-for $tt {method_name}) "
            f"(MethodSequence $seq)) $seq)"
        )
        if not atoms:
            return []
        raw = str(atoms[0]).strip().strip("()")
        return [t.strip() for t in raw.split() if t.strip()]

    def get_method_dataflow(self, method_name: str) -> list[dict]:
        atoms = self._q(
            f"!(match &self (MethodDataFlow {method_name} $tool $dir $port $var) "
            f"($tool $dir $port $var))"
        )
        flows = []
        for atom in atoms:
            raw = str(atom).strip().strip("()")
            parts = raw.split()
            if len(parts) == 4:
                flows.append(
                    {
                        "tool": parts[0],
                        "direction": parts[1],
                        "port": parts[2],
                        "var": parts[3],
                    }
                )
        return flows

    # ------------------------------------------------------------------ #
    # Step-keyed lookups (preferred for compilation — disambiguate duplicate
    # tool instances within the same method).
    # ------------------------------------------------------------------ #

    def get_method_steps(self, method_name: str) -> list[dict]:
        """
        Returns ordered list of {step_id, tool, position} for a method.
        Sourced from method_meta.json (loaded at PLNReasoner.load).
        """
        meta = self._method_meta.get(method_name)
        if not meta:
            return []
        return [
            {
                "step_id": s["step_id"],
                "tool": s["tool"],
                "position": s["position"],
            }
            for s in sorted(meta.get("steps", []), key=lambda s: s["position"])
        ]

    def get_step_dataflow(self, method_name: str, step_id: str) -> list[dict]:
        """Per-step input/output flow records: {direction, port, var}."""
        meta = self._method_meta.get(method_name)
        if not meta:
            return []
        for s in meta.get("steps", []):
            if s["step_id"] != step_id:
                continue
            flows = []
            for inp in s.get("inputs", []):
                flows.append(
                    {"direction": "input", "port": inp["port"], "var": inp["var"]}
                )
            for out in s.get("outputs", []):
                flows.append(
                    {"direction": "output", "port": out["port"], "var": out["var"]}
                )
            return flows
        return []

    def get_method_inputs(self, method_name: str) -> list[dict]:
        """Vars that must come from outside the method (workflow inputs)."""
        meta = self._method_meta.get(method_name)
        if not meta:
            return []
        return list(meta.get("method_inputs", []))

    def get_type_parent(self, type_name: str) -> list[str]:
        atoms = self._q(
            f"!(match &self (Inheritance {type_name} $parent) $parent)"
        )
        return [str(a).strip() for a in atoms if str(a).strip()]

    def types_compatible(self, child: str, parent: str) -> bool:
        """Transitive inheritance check over the EDAM-aligned type hierarchy."""
        if child == parent:
            return True
        visited = set()
        frontier = [child]
        while frontier:
            t = frontier.pop()
            if t in visited:
                continue
            visited.add(t)
            if t == parent:
                return True
            frontier.extend(self.get_type_parent(t))
        return False

    def score_method(self, method_name: str) -> dict:
        tools = self.get_tools_for_method(method_name)
        if not tools:
            return {"score": 0.0, "tools": [], "stvs": []}

        stvs = []
        expectations = []
        for tool in tools:
            s, c = self.get_tool_stv(tool)
            e = _expectation(s, c)
            stvs.append(
                {"tool": tool, "strength": s, "confidence": c, "expectation": e}
            )
            expectations.append(e)

        return {
            "score": _compound_score(expectations),
            "tools": tools,
            "stvs": stvs,
        }

    def get_methods_for_task_scored(self, task_type: str) -> list[dict]:
        methods = self.get_methods_for_task(task_type)
        scored = []
        for m in methods:
            data = self.score_method(m)
            scored.append({"name": m, "task_type": task_type, **data})
        return sorted(scored, key=lambda x: x["score"], reverse=True)

    def pln_revision(self, stv1: tuple, stv2: tuple) -> tuple[float, float]:
        """PLN Revision — weight-based merge of independent evidence streams."""
        s1, c1 = stv1
        s2, c2 = stv2
        if c1 >= 1.0 or c2 >= 1.0:
            return (1.0, 0.99)
        w1 = c1 / (1.0 - c1)
        w2 = c2 / (1.0 - c2)
        w = w1 + w2
        return (((s1 * w1) + (s2 * w2)) / w, w / (w + 1.0))

    def pln_modus_ponens(self, stv_p: tuple, stv_pq: tuple) -> tuple[float, float]:
        """PLN Modus Ponens — P, P→Q ⊢ Q."""
        ps, pc = stv_p
        pqs, pqc = stv_pq
        return (ps * pqs, ps * pqs * pc * pqc)

    def list_task_types(self) -> list[str]:
        atoms = self._q(
            "!(match &self (= (method-for $task $m) $body) $task)"
        )
        return sorted(set(str(a).strip() for a in atoms if str(a).strip()))
