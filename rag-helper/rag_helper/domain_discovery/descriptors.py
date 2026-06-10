"""Domain descriptor ingestion for domain discovery (CCDD-010).

Reads ``domains/<domain_id>/domain.json`` artifacts that already exist in
the project and feeds them into the analysis as the highest-priority
signal (CCDD-003 signal model: descriptor > path > package > graph).

The descriptor contract is documented in
``docs/architecture/domain_descriptor_reference.md``. Only static fields
are read; descriptor loading never imports, executes, or instantiates
anything defined in a descriptor (CCDD-DD-001 / domain foundation rule:
descriptors are declarative contracts, not runtime plugins).

Output:

  - ``index_existing_descriptors(root)`` -> dict[domain_id, descriptor dict]
  - ``build_descriptor_mismatches(descriptors, candidates)`` -> list of
    warning dicts to be passed into ``compute_boundary_metrics`` as the
    ``descriptor_mismatches`` argument (CCDD-009 wire-up).

A mismatch is reported when:
  - the descriptor names paths the analysis did not find any record under
  - the descriptor names a domain_id with no root_paths or no records
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from rag_helper.domain_discovery.contracts import DomainCandidate

WARNING_DESCRIPTOR_MISMATCH = "descriptor_mismatch"

DOMAIN_DESCRIPTOR_SCHEMA = "domain_descriptor.v1"
DOMAINS_DIRNAME = "domains"
DOMAIN_FILE_NAME = "domain.json"


@dataclass
class ExistingDescriptor:
    domain_id: str
    descriptor_path: str
    raw: dict[str, Any]
    code_paths: list[str]
    docs_paths: list[str]
    rag_profiles: list[dict]


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        return [value]
    return []


def _coerce_rag_profiles(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _normalize_descriptor(domain_id: str, raw: dict[str, Any], path: str) -> ExistingDescriptor:
    source_paths = raw.get("source_paths") or {}
    code_paths: list[str] = []
    docs_paths: list[str] = []
    rag_profiles: list[dict] = []
    if isinstance(source_paths, dict):
        code_paths = _coerce_str_list(source_paths.get("code_paths"))
        docs_paths = _coerce_str_list(source_paths.get("docs_paths"))
        rag_profiles = _coerce_rag_profiles(source_paths.get("rag_profiles"))
    return ExistingDescriptor(
        domain_id=domain_id,
        descriptor_path=path,
        raw=raw,
        code_paths=code_paths,
        docs_paths=docs_paths,
        rag_profiles=rag_profiles,
    )


def index_existing_descriptors(
    project_root: str | Path,
    *,
    domains_dirname: str = DOMAINS_DIRNAME,
    domain_filename: str = DOMAIN_FILE_NAME,
) -> dict[str, ExistingDescriptor]:
    """Walk ``<root>/<domains_dirname>/<id>/domain.json`` and index descriptors.

    Missing directories or unreadable files are silently skipped; the
    caller is expected to log this absence as a warning at a higher level.
    """
    root = Path(project_root)
    domains_dir = root / domains_dirname
    if not domains_dir.is_dir():
        return {}

    out: dict[str, ExistingDescriptor] = {}
    for child in sorted(domains_dir.iterdir()):
        if not child.is_dir():
            continue
        descriptor_path = child / domain_filename
        if not descriptor_path.is_file():
            continue
        try:
            raw = json.loads(descriptor_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw, dict):
            continue
        domain_id = str(raw.get("domain_id") or child.name)
        out[domain_id] = _normalize_descriptor(
            domain_id, raw, str(descriptor_path)
        )
    return out


def _candidate_paths(candidate: DomainCandidate) -> set[str]:
    return {p for p in candidate.root_paths}


def build_descriptor_mismatches(
    descriptors: Mapping[str, ExistingDescriptor],
    candidates: list[DomainCandidate],
) -> list[dict]:
    """Produce boundary_warnings entries for descriptor-vs-analysis mismatches.

    Three mismatch flavours are recognised:

      - paths_named_but_empty: descriptor has code_paths, the analysis has
        a candidate with that domain_id but no records under those paths.
      - no_matching_cluster: descriptor names a domain_id that the
        analysis did not surface as a candidate at all.
      - paths_under_different_root: descriptor's code_paths all fall
        under a different cluster's root_paths than the descriptor claims.
    """
    warnings: list[dict] = []
    by_id = {c.domain_id: c for c in candidates}
    known_roots: set[str] = set()
    for candidate in candidates:
        known_roots.update(candidate.root_paths)

    for descriptor in descriptors.values():
        candidate = by_id.get(descriptor.domain_id)
        if candidate is None:
            warnings.append(
                {
                    "source_domain": descriptor.domain_id,
                    "target_domain": "*",
                    "warning_type": WARNING_DESCRIPTOR_MISMATCH,
                    "severity": "warning",
                    "evidence": {
                        "kind": "no_matching_cluster",
                        "descriptor_path": descriptor.descriptor_path,
                        "code_paths": list(descriptor.code_paths),
                    },
                }
            )
            continue
        candidate_paths = _candidate_paths(candidate)
        matched_paths = [p for p in descriptor.code_paths if any(
            candidate_paths and (p == rp or p.startswith(rp + "/") or rp.startswith(p + "/"))
            for rp in candidate_paths
        )]
        if descriptor.code_paths and not matched_paths:
            warnings.append(
                {
                    "source_domain": descriptor.domain_id,
                    "target_domain": "*",
                    "warning_type": WARNING_DESCRIPTOR_MISMATCH,
                    "severity": "warning",
                    "evidence": {
                        "kind": "paths_named_but_empty",
                        "descriptor_path": descriptor.descriptor_path,
                        "code_paths": list(descriptor.code_paths),
                        "cluster_root_paths": sorted(candidate_paths),
                    },
                }
            )
            continue
        if descriptor.code_paths:
            for code_path in descriptor.code_paths:
                if not any(
                    code_path == rp or code_path.startswith(rp + "/")
                    for rp in candidate_paths
                ) and not any(
                    rp == code_path or rp.startswith(code_path + "/")
                    for rp in candidate_paths
                ):
                    warnings.append(
                        {
                            "source_domain": descriptor.domain_id,
                            "target_domain": "*",
                            "warning_type": WARNING_DESCRIPTOR_MISMATCH,
                            "severity": "info",
                            "evidence": {
                                "kind": "paths_under_different_root",
                                "descriptor_path": descriptor.descriptor_path,
                                "offending_path": code_path,
                                "cluster_root_paths": sorted(candidate_paths),
                            },
                        }
                    )
    return warnings


def apply_descriptor_signal(
    candidates: list[DomainCandidate],
    descriptors: Mapping[str, ExistingDescriptor],
) -> None:
    """Mark candidates whose domain_id is named by a descriptor.

    Mutates each candidate in-place: sets ``evidence.descriptor_signal``
    and toggles the ``descriptor_signal`` confidence contribution. The
    caller is still expected to recompute confidence if the weight
    changes; we only stamp the evidence here.
    """
    by_id = {c.domain_id: c for c in candidates}
    for descriptor in descriptors.values():
        candidate = by_id.get(descriptor.domain_id)
        if candidate is None:
            continue
        existing = dict(candidate.evidence or {})
        existing["descriptor_signal"] = {
            "descriptor_path": descriptor.descriptor_path,
            "code_paths": list(descriptor.code_paths),
        }
        candidate.evidence = existing
