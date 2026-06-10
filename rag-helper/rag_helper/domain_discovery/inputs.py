"""Loaders for existing CodeCompass outputs (CCDD-005).

Missing optional files produce warnings instead of hard failures; records
that lack the minimal fields are skipped and counted in warnings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AnalysisInputs:
    out_dir: Path | None
    index_records: list[dict] = field(default_factory=list)
    detail_records: list[dict] = field(default_factory=list)
    relation_records: list[dict] = field(default_factory=list)
    graph_nodes: list[dict] = field(default_factory=list)
    graph_edges: list[dict] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    loaded_files: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_memory(
        cls,
        *,
        index_records: list[dict],
        detail_records: list[dict],
        relation_records: list[dict],
        graph_nodes: list[dict],
        graph_edges: list[dict],
        manifest: dict,
    ) -> "AnalysisInputs":
        """Build inputs from in-memory records (used by process_project)."""
        inputs = cls(
            out_dir=None,
            index_records=list(index_records),
            detail_records=list(detail_records),
            relation_records=list(relation_records),
            manifest=dict(manifest),
            loaded_files={
                "index(in-memory)": len(index_records),
                "details(in-memory)": len(detail_records),
                "relations(in-memory)": len(relation_records),
                "graph_nodes(in-memory)": len(graph_nodes),
                "graph_edges(in-memory)": len(graph_edges),
            },
        )
        inputs.graph_nodes = _validate_nodes(graph_nodes, inputs.warnings)
        inputs.graph_edges = _validate_edges(graph_edges, inputs.warnings)
        return inputs


def _read_jsonl(path: Path, warnings: list[str]) -> list[dict]:
    records: list[dict] = []
    invalid_lines = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if isinstance(payload, dict):
                records.append(payload)
            else:
                invalid_lines += 1
    if invalid_lines:
        warnings.append(f"{path.name}: skipped {invalid_lines} invalid jsonl line(s)")
    return records


def _validate_nodes(nodes: list[dict], warnings: list[str]) -> list[dict]:
    valid: list[dict] = []
    skipped = 0
    for node in nodes:
        if node.get("id") and node.get("kind") is not None and node.get("file"):
            valid.append(node)
        else:
            skipped += 1
    if skipped:
        warnings.append(f"graph_nodes: skipped {skipped} node(s) missing id/kind/file")
    return valid


def _validate_edges(edges: list[dict], warnings: list[str]) -> list[dict]:
    valid: list[dict] = []
    skipped = 0
    for edge in edges:
        if edge.get("source") and edge.get("target") and edge.get("type"):
            valid.append(edge)
        else:
            skipped += 1
    if skipped:
        warnings.append(f"graph_edges: skipped {skipped} edge(s) missing source/target/type")
    return valid


def load_analysis_inputs(out_dir: Path) -> AnalysisInputs:
    """Load index/details/relations/graph/manifest outputs from out_dir.

    All files are optional: missing files are reported as warnings so that
    callers can decide which signals remain usable.
    """
    out_dir = Path(out_dir)
    inputs = AnalysisInputs(out_dir=out_dir)

    if not out_dir.is_dir():
        inputs.warnings.append(f"out_dir not found: {out_dir}")
        return inputs

    jsonl_targets = {
        "index.jsonl": "index_records",
        "details.jsonl": "detail_records",
        "relations.jsonl": "relation_records",
        "graph_nodes.jsonl": "graph_nodes",
        "graph_edges.jsonl": "graph_edges",
    }
    for filename, attribute in jsonl_targets.items():
        path = out_dir / filename
        if not path.is_file():
            inputs.warnings.append(f"{filename} not found in out_dir; related signals skipped")
            continue
        records = _read_jsonl(path, inputs.warnings)
        setattr(inputs, attribute, records)
        inputs.loaded_files[filename] = len(records)

    manifest_path = out_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            inputs.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            inputs.loaded_files["manifest.json"] = 1
        except json.JSONDecodeError:
            inputs.warnings.append("manifest.json is not valid JSON; manifest signals skipped")
    else:
        inputs.warnings.append("manifest.json not found in out_dir; manifest signals skipped")

    inputs.graph_nodes = _validate_nodes(inputs.graph_nodes, inputs.warnings)
    inputs.graph_edges = _validate_edges(inputs.graph_edges, inputs.warnings)
    return inputs
