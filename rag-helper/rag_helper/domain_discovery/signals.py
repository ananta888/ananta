"""Deterministic domain signals: path roots, packages/namespaces, layers.

Signal model and precedence: docs/codecompass-domain-discovery.md section 3.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

DEFAULT_MIN_FILES = 3
DEFAULT_MAX_ROOT_DEPTH = 2
DEFAULT_DOMINANCE = 0.8


@dataclass
class RootCandidate:
    root_path: str
    file_count: int


@dataclass
class _PathNode:
    total: int = 0
    direct: int = 0
    children: dict[str, "_PathNode"] = field(default_factory=dict)


def _build_path_tree(files: list[str]) -> _PathNode:
    root = _PathNode()
    for file_path in files:
        normalized = str(file_path).replace("\\", "/").strip("/")
        if not normalized:
            continue
        segments = normalized.split("/")
        node = root
        node.total += 1
        for segment in segments[:-1]:
            node = node.children.setdefault(segment, _PathNode())
            node.total += 1
        node.direct += 1
    return root


def derive_root_path_candidates(
    files: list[str],
    *,
    min_files: int = DEFAULT_MIN_FILES,
    max_root_depth: int = DEFAULT_MAX_ROOT_DEPTH,
    dominance: float = DEFAULT_DOMINANCE,
) -> list[RootCandidate]:
    """Derive stable root-path candidates from record file paths.

    Walk rules (documented in docs/codecompass-domain-discovery.md):
      - descend into a single dominant child (>= dominance share) when the
        node has few direct files, e.g. rag-helper -> rag-helper/rag_helper
      - split a node into its children when it has few direct files but
        several sufficiently large children and depth allows it,
        e.g. agent -> agent/services, agent/routes
      - otherwise the node itself becomes the candidate
    Same input yields a byte-stable sorted candidate list.
    """
    tree = _build_path_tree(files)
    candidates: list[RootCandidate] = []

    def resolve(node: _PathNode, prefix: list[str]) -> None:
        depth = len(prefix)
        big_children = [
            (name, child)
            for name, child in sorted(node.children.items())
            if child.total >= min_files
        ]
        if node.direct < min_files and depth < max_root_depth:
            dominant = [
                (name, child)
                for name, child in big_children
                if child.total >= dominance * node.total
            ]
            if len(dominant) == 1:
                resolve(dominant[0][1], [*prefix, dominant[0][0]])
                return
            if len(big_children) >= 2:
                for name, child in big_children:
                    resolve(child, [*prefix, name])
                return
        candidates.append(RootCandidate(root_path="/".join(prefix), file_count=node.total))

    for name, child in sorted(tree.children.items()):
        if child.total >= min_files:
            resolve(child, [name])

    return sorted(candidates, key=lambda c: c.root_path)


def assign_root_path(file_path: str, root_paths: list[str]) -> str | None:
    """Longest-prefix match of a file path onto the candidate roots."""
    normalized = str(file_path).replace("\\", "/").strip("/")
    best: str | None = None
    for root in root_paths:
        if normalized == root or normalized.startswith(root + "/"):
            if best is None or len(root) > len(best):
                best = root
    return best


def collect_package_prefixes(records: list[dict], *, segments: int = 2) -> dict[str, int]:
    """Count java package / C# namespace prefixes over records.

    manifest.package_type_index only covers java packages; namespaces are
    therefore read from the records themselves (CCDD-007).
    """
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        raw = record.get("package") or record.get("namespace")
        if not raw:
            continue
        parts = str(raw).split(".")
        prefix = ".".join(parts[:segments]) if len(parts) > segments else str(raw)
        counts[prefix] += 1
    return dict(counts)


def package_prefixes_from_manifest(manifest: dict, *, segments: int = 2) -> dict[str, int]:
    """Strong package signal from manifest.package_type_index."""
    counts: dict[str, int] = defaultdict(int)
    package_type_index = manifest.get("package_type_index") or {}
    if not isinstance(package_type_index, dict):
        return {}
    for package, type_names in package_type_index.items():
        parts = str(package).split(".")
        prefix = ".".join(parts[:segments]) if len(parts) > segments else str(package)
        counts[prefix] += len(type_names or [])
    return dict(counts)


def technical_layers_for_records(records: list[dict], *, rich_mode: bool = True) -> list[str]:
    """Enrichment-only layer signal via gem_partitions classification.

    Technical layers (api/service/data-model/...) describe what kind of
    building blocks a domain contains; they are never a clustering criterion
    (CCDD-DD-003).
    """
    from rag_helper.application.gem_partitions import _classify_domain

    layers: set[str] = set()
    for record in records:
        layer = _classify_domain(record, rich_mode=rich_mode)
        if layer:
            layers.add(layer)
    return sorted(layers)
