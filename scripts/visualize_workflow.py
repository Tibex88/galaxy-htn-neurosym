"""
Visualize a gxformat2 workflow YAML as a DAG.

Reads a gxformat2 file, reconstructs the workflow DAG via rustworkx
using the step `in:` block (each entry names a `source: step_id/port`),
lays out the nodes by topological depth, and writes an SVG/PNG via
matplotlib.

Usage:
    python scripts/visualize_workflow.py workflow.gxwf.yml
    python scripts/visualize_workflow.py workflow.gxwf.yml --out wf.png
    python scripts/visualize_workflow.py workflow.gxwf.yml --show
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import rustworkx as rx
import yaml


def _parse_workflow(yaml_path: Path) -> dict:
    return yaml.safe_load(yaml_path.read_text())


def _build_graph(wf: dict) -> tuple[rx.PyDiGraph, dict[str, int], list[tuple[int, int, str]]]:
    """
    Returns (graph, node_ids_by_name, edge_tuples).
    node kind is stored as a tuple (name, kind) where kind is
    'input' or 'step'. Edges carry the downstream port label.
    """
    g: rx.PyDiGraph = rx.PyDiGraph(check_cycle=False, multigraph=True)
    ids: dict[str, int] = {}
    edges: list[tuple[int, int, str]] = []

    for input_name, _meta in (wf.get("inputs") or {}).items():
        ids[input_name] = g.add_node((input_name, "input"))

    for step_id, step in (wf.get("steps") or {}).items():
        label = step.get("label") or step_id
        ids[step_id] = g.add_node((label, "step"))

    for step_id, step in (wf.get("steps") or {}).items():
        tgt = ids[step_id]
        for port, conn in (step.get("in") or {}).items():
            source = conn.get("source") if isinstance(conn, dict) else conn
            if not source:
                continue
            src_name = source.split("/")[0]
            if src_name not in ids:
                ids[src_name] = g.add_node((src_name, "input"))
            src = ids[src_name]
            g.add_edge(src, tgt, port)
            edges.append((src, tgt, port))

    return g, ids, edges


def _layered_positions(
    g: rx.PyDiGraph, x_spacing: float = 2.2, y_spacing: float = 1.3
) -> dict[int, tuple[float, float]]:
    """Depth-by-longest-path layout: every node's column = longest distance from any source."""
    depth: dict[int, int] = {}
    try:
        topo = rx.topological_sort(g)
    except rx.DAGHasCycle:
        topo = list(g.node_indices())

    for n in topo:
        preds = g.predecessor_indices(n)
        depth[n] = max((depth.get(p, 0) + 1 for p in preds), default=0)

    by_col: dict[int, list[int]] = defaultdict(list)
    for n, d in depth.items():
        by_col[d].append(n)

    positions: dict[int, tuple[float, float]] = {}
    for col, nodes in by_col.items():
        n = len(nodes)
        for i, node in enumerate(nodes):
            y = (i - (n - 1) / 2.0) * y_spacing
            positions[node] = (col * x_spacing, y)
    return positions


def _render(
    g: rx.PyDiGraph,
    positions: dict[int, tuple[float, float]],
    title: str,
    out_path: Path | None,
    show: bool,
):
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    from matplotlib.patches import FancyArrowPatch

    n_cols = max((int(x / 2.2) for x, _ in positions.values()), default=1) + 1
    n_rows = max((len(list(v)) for v in positions.values()), default=1)
    fig_w = max(8, n_cols * 2.2)
    fig_h = max(5, 1 + n_rows * 0.8)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_axis_off()
    ax.set_title(title, fontsize=11)

    for u, v, port in g.edge_list_with_weights() if hasattr(g, "edge_list_with_weights") else [
        (a, b, g.get_edge_data(a, b)) for a, b in g.edge_list()
    ]:
        (x1, y1), (x2, y2) = positions[u], positions[v]
        arrow = FancyArrowPatch(
            (x1 + 0.55, y1),
            (x2 - 0.55, y2),
            arrowstyle="-|>",
            mutation_scale=12,
            color="#555",
            linewidth=1.0,
            connectionstyle="arc3,rad=0.08",
            zorder=1,
        )
        ax.add_patch(arrow)
        if port:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            label = str(port).split("|")[-1][:22]
            ax.text(
                mx,
                my + 0.10,
                label,
                fontsize=6,
                color="#333",
                ha="center",
                va="bottom",
                zorder=2,
            )

    for node_idx, (x, y) in positions.items():
        name, kind = g[node_idx]
        short = name if len(name) <= 26 else name[:23] + "..."
        fc = "#cfe8f3" if kind == "input" else "#e8f0d6"
        ec = "#3b7ea1" if kind == "input" else "#5b7a2a"
        box = FancyBboxPatch(
            (x - 0.95, y - 0.30),
            1.9,
            0.6,
            boxstyle="round,pad=0.04,rounding_size=0.12",
            facecolor=fc,
            edgecolor=ec,
            linewidth=1.1,
            zorder=3,
        )
        ax.add_patch(box)
        ax.text(
            x,
            y,
            short,
            ha="center",
            va="center",
            fontsize=7.5,
            zorder=4,
        )

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    if xs and ys:
        ax.set_xlim(min(xs) - 1.5, max(xs) + 1.5)
        ax.set_ylim(min(ys) - 1.5, max(ys) + 1.5)
    ax.set_aspect("equal")

    fig.tight_layout()
    if out_path:
        fig.savefig(out_path, dpi=160, bbox_inches="tight")
        print(f"wrote {out_path}")
    if show:
        plt.show()
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("yaml_path", help="Path to gxformat2 workflow YAML")
    ap.add_argument(
        "--out",
        help="Output image path (.svg/.png). Default: alongside YAML with same stem.",
    )
    ap.add_argument("--show", action="store_true", help="Also open an interactive window.")
    args = ap.parse_args()

    yaml_path = Path(args.yaml_path)
    if not yaml_path.exists():
        ap.error(f"not found: {yaml_path}")

    wf = _parse_workflow(yaml_path)
    g, _ids, _edges = _build_graph(wf)
    positions = _layered_positions(g)

    out_path = Path(args.out) if args.out else yaml_path.with_suffix(".svg")

    title = wf.get("name") or yaml_path.stem
    _render(g, positions, title, out_path, args.show)


if __name__ == "__main__":
    main()
