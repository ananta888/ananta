"""Domain-discovery artifact writer (CCDD-013) and descriptor suggestions (CCDD-015).

This module owns the IO boundary for the domain-discovery pipeline. It
takes the in-memory analysis result (a payload + a BoundaryResult) and
turns it into the three on-disk artifacts documented in
``docs/codecompass-domain-discovery.md``:

  - ``domains.detected.json`` (codecompass_domain_analysis.v1)
  - ``domain_boundaries.jsonl`` (one warning per line, schema is
    inline-validated by ``devtools.validate_codecompass_domain_discovery``)
  - ``domain_coupling.json`` (codecompass_domain_coupling.v1)

In addition, when explicitly opted in, this module writes descriptor
suggestions under ``<out>/domain_descriptor_suggestions/<id>/domain.json``
(CCDD-015). Suggestions are opt-in and never overwrite existing
``<out>/domains/<id>/domain.json`` files (CCDD-DD-005).

The module also offers ``run_domain_discovery`` (CCDD-013 acceptance:
end-to-end pipeline from ``AnalysisInputs`` to artifacts) and a
``DomainDiscoveryResult`` dataclass that callers (CCDD-014 manifest
integration, CCDD-012 CLI option) can inspect without re-running the
analysis.

Dry-run mode writes nothing to disk; the planned output paths are
returned in the result so the caller can echo them or include them in
the manifest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag_helper.domain_discovery.boundaries import (
    BoundaryResult,
    compute_boundary_metrics,
)
from rag_helper.domain_discovery.clustering import (
    ClusteringResult,
    cluster_domains,
)
from rag_helper.domain_discovery.contracts import (
    DOMAIN_ANALYSIS_SCHEMA,
    DOMAIN_COUPLING_SCHEMA,
    DomainCandidate,
    build_analysis_payload,
)
from rag_helper.domain_discovery.descriptors import (
    ExistingDescriptor,
    build_descriptor_mismatches,
    index_existing_descriptors,
)
from rag_helper.domain_discovery.graph_model import DomainGraph
from rag_helper.domain_discovery.inputs import AnalysisInputs

# Writer constants (CCDD-013 / CCDD-015)
DOMAINS_DETECTED_FILENAME = "domains.detected.json"
DOMAIN_BOUNDARIES_FILENAME = "domain_boundaries.jsonl"
DOMAIN_COUPLING_FILENAME = "domain_coupling.json"
DOMAIN_DESCRIPTOR_SUGGESTIONS_DIRNAME = "domain_descriptor_suggestions"
DOMAIN_DESCRIPTOR_FILE_NAME = "domain.json"
DOMAIN_DESCRIPTOR_SCHEMA = "domain_descriptor.v1"
DOMAIN_DESCRIPTOR_SUGGESTED_LIFECYCLE = "foundation_only"
DOMAIN_DESCRIPTOR_SUGGESTED_RUNTIME = "descriptor_only"

# Coupling pair sort key (byte-stable).
_COUPLING_PAIR_SORT_KEY = lambda pair: (  # noqa: E731
    pair.get("source", ""),
    pair.get("target", ""),
)
# Boundary warning sort key (byte-stable).
_BOUNDARY_SORT_KEY = lambda warning: (  # noqa: E731
    warning.get("warning_type", ""),
    warning.get("source_domain", ""),
    warning.get("target_domain", ""),
    warning.get("severity", ""),
)


@dataclass
class DomainDiscoveryResult:
    """Aggregated output of the domain-discovery pipeline.

    ``payload`` is the validated ``codecompass_domain_analysis.v1`` dict
    (already sorted and stable). ``coupling_payload`` is the
    ``codecompass_domain_coupling.v1`` dict. ``boundary_warnings`` is
    the list of warning dicts that went into ``domain_boundaries.jsonl``.
    ``output_files`` lists the on-disk paths actually written, or the
    paths that would have been written in dry-run mode.
    """

    payload: dict[str, Any]
    coupling_payload: dict[str, Any]
    boundary_warnings: list[dict[str, Any]]
    domain_candidates: list[DomainCandidate] = field(default_factory=list)
    unassigned_records: list[str] = field(default_factory=list)
    descriptor_suggestions: list[Path] = field(default_factory=list)
    output_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_coupling_payload(
    *,
    project_root: str,
    generated_at: str,
    pairs: list[dict],
) -> dict[str, Any]:
    """Build the stable, validator-compatible coupling payload."""
    sorted_pairs = sorted(pairs, key=_COUPLING_PAIR_SORT_KEY)
    return {
        "schema": DOMAIN_COUPLING_SCHEMA,
        "project_root": project_root,
        "generated_at": generated_at,
        "pair_count": len(sorted_pairs),
        "pairs": sorted_pairs,
    }


def _to_coupling_pairs(coupling_pairs: list[dict]) -> list[dict]:
    """Normalise a ``BoundaryResult.coupling_pairs`` entry to a JSON-friendly dict.

    The internal ``_build_coupling_pairs`` produces
    ``{source, target, edge_count, edge_type_counts}``; we expose both
    fields so the validator (which only checks ``source``/``target``)
    and the analysis consumer (which wants per-type counts) are happy.
    """
    out: list[dict] = []
    for entry in coupling_pairs:
        if not isinstance(entry, dict):
            continue
        counts = entry.get("edge_type_counts") or {}
        if isinstance(counts, dict):
            counts_clean = {str(k): int(v) for k, v in sorted(counts.items())}
        else:
            counts_clean = {}
        out.append(
            {
                "source": str(entry.get("source", "")),
                "target": str(entry.get("target", "")),
                "edge_count": int(entry.get("edge_count", 0) or 0),
                "edge_type_counts": counts_clean,
            }
        )
    return out


def run_domain_discovery(
    inputs: AnalysisInputs,
    *,
    project_root: str | Path | None = None,
    generated_at: str | None = None,
    domains_dirname: str = "domains",
) -> DomainDiscoveryResult:
    """Run the full CCDD analysis pipeline (cluster + boundary + descriptors).

    The result is ready to be written to disk via :func:`write_domain_artifacts`.
    This is the only place that wires ``DomainGraph``, ``cluster_domains``,
    ``compute_boundary_metrics`` and ``index_existing_descriptors``
    together; callers (CCDD-012 CLI, CCDD-014 manifest integration) use
    it to avoid duplicating pipeline steps.
    """
    if project_root is None:
        if inputs.out_dir is not None:
            project_root = str(Path(inputs.out_dir).parent)
        else:
            project_root = "."
    project_root_s = str(project_root)
    generated = generated_at or _now_iso()

    graph = DomainGraph.build(inputs)
    clustering: ClusteringResult = cluster_domains(graph, records=inputs.index_records)
    descriptors: dict[str, ExistingDescriptor] = index_existing_descriptors(
        project_root_s, domains_dirname=domains_dirname
    )
    mismatches = build_descriptor_mismatches(descriptors, clustering.candidates)
    boundary: BoundaryResult = compute_boundary_metrics(
        clustering, graph, descriptor_mismatches=mismatches
    )

    warnings: list[str] = []
    warnings.extend(inputs.warnings)
    warnings.extend(clustering.warnings)
    warnings.extend(boundary.warnings)

    payload = build_analysis_payload(
        project_root=project_root_s,
        generated_at=generated,
        inputs=dict(inputs.loaded_files),
        domains=boundary.candidates,
        unassigned_records=clustering.unassigned_records,
        warnings=warnings,
    )
    coupling_payload = _build_coupling_payload(
        project_root=project_root_s,
        generated_at=generated,
        pairs=_to_coupling_pairs(boundary.coupling_pairs),
    )
    return DomainDiscoveryResult(
        payload=payload,
        coupling_payload=coupling_payload,
        boundary_warnings=list(boundary.boundary_warnings),
        domain_candidates=list(boundary.candidates),
        unassigned_records=list(clustering.unassigned_records),
        warnings=warnings,
    )


def write_domain_artifacts(
    result: DomainDiscoveryResult,
    out_dir: str | Path,
    *,
    dry_run: bool = False,
) -> DomainDiscoveryResult:
    """Write the three domain-discovery artifacts under ``out_dir``.

    Returns a copy of ``result`` with ``output_files`` populated. In
    dry-run mode no file is written; the would-be paths are still
    recorded so the caller can include them in its own summary.
    """
    out_dir = Path(out_dir)
    written: list[Path] = []
    domains_path = out_dir / DOMAINS_DETECTED_FILENAME
    boundaries_path = out_dir / DOMAIN_BOUNDARIES_FILENAME
    coupling_path = out_dir / DOMAIN_COUPLING_FILENAME
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        domains_path.write_text(
            json.dumps(result.payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(domains_path)
        with boundaries_path.open("w", encoding="utf-8") as handle:
            for warning in sorted(result.boundary_warnings, key=_BOUNDARY_SORT_KEY):
                handle.write(json.dumps(warning, ensure_ascii=False) + "\n")
        written.append(boundaries_path)
        coupling_path.write_text(
            json.dumps(result.coupling_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(coupling_path)
    else:
        written.extend([domains_path, boundaries_path, coupling_path])
    return DomainDiscoveryResult(
        payload=result.payload,
        coupling_payload=result.coupling_payload,
        boundary_warnings=list(result.boundary_warnings),
        domain_candidates=list(result.domain_candidates),
        unassigned_records=list(result.unassigned_records),
        descriptor_suggestions=list(result.descriptor_suggestions),
        output_files=written,
        warnings=list(result.warnings),
    )


# ---------------------------------------------------------------------------
# CCDD-015: Domain descriptor suggestions (opt-in).
# ---------------------------------------------------------------------------


def _build_descriptor_suggestion(
    candidate: DomainCandidate,
    *,
    descriptor_dirname: str,
) -> dict[str, Any]:
    """Build a non-runtime descriptor suggestion for a domain candidate.

    Per CCDD-DD-005 the suggestion is intentionally narrow: it uses
    ``domain_descriptor.v1``, marks the lifecycle as
    ``foundation_only`` and the runtime status as ``descriptor_only``,
    and never claims a ``bridge_adapter_type`` or pulls in any plugin
    hooks. The caller decides whether to write it.
    """
    return {
        "schema": DOMAIN_DESCRIPTOR_SCHEMA,
        "domain_id": candidate.domain_id,
        "display_name": candidate.display_name,
        "lifecycle_status": DOMAIN_DESCRIPTOR_SUGGESTED_LIFECYCLE,
        "runtime_status": DOMAIN_DESCRIPTOR_SUGGESTED_RUNTIME,
        "source_paths": {
            "code_paths": sorted(candidate.root_paths),
            "docs_paths": [],
            "rag_profiles": [],
        },
        "evidence": {
            "confidence": round(float(candidate.confidence), 4),
            "package_prefixes": sorted(candidate.package_prefixes),
            "technical_layers": sorted(candidate.technical_layers),
            "core_records": list(candidate.core_records),
            "metrics": dict(candidate.metrics),
            "auto_generated": True,
            "suggestion_dirname": descriptor_dirname,
        },
    }


def write_descriptor_suggestions(
    result: DomainDiscoveryResult,
    out_dir: str | Path,
    *,
    opt_in: bool = False,
    dry_run: bool = False,
    descriptor_dirname: str = DOMAIN_DESCRIPTOR_SUGGESTIONS_DIRNAME,
    existing_domains_dirname: str = "domains",
) -> DomainDiscoveryResult:
    """Write opt-in descriptor suggestions under ``domain_descriptor_suggestions/``.

    Skipped silently when ``opt_in`` is False (the default). Suggestions
    never overwrite or touch ``<out>/domains/<id>/domain.json`` -- that
    directory is the source of truth for already-approved descriptors
    and is read by :func:`index_existing_descriptors`. The result is
    returned with ``descriptor_suggestions`` populated with the
    written (or planned) paths.
    """
    out_dir = Path(out_dir)
    written: list[Path] = []
    if opt_in:
        suggestions_root = out_dir / descriptor_dirname
        for candidate in result.domain_candidates:
            suggestion = _build_descriptor_suggestion(
                candidate, descriptor_dirname=descriptor_dirname
            )
            target = suggestions_root / candidate.domain_id / DOMAIN_DESCRIPTOR_FILE_NAME
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(suggestion, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            written.append(target)
    return DomainDiscoveryResult(
        payload=result.payload,
        coupling_payload=result.coupling_payload,
        boundary_warnings=list(result.boundary_warnings),
        domain_candidates=list(result.domain_candidates),
        unassigned_records=list(result.unassigned_records),
        descriptor_suggestions=written,
        output_files=list(result.output_files),
        warnings=list(result.warnings),
    )
