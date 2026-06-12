"""CCRDS-004/005/006: resolve an explicit domain selection to hard paths.

Inputs, in priority order per domain id:
  1. ``domains/<id>/domain.json`` descriptor (manual correction source) —
     only when it is a *business* descriptor carrying code paths; the
     existing capability descriptors (blender, freecad, ...) describe tool
     domains and are never treated as path scopes.
  2. ``domains.detected.json`` (``codecompass_domain_analysis.v1``)
     candidate ``root_paths``.

Descriptors win over detected root_paths but the conflict is surfaced as
a warning (CCRDS-005). Unknown domains fail closed in strict mode and
produce a warning + empty scope otherwise — never invented paths.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.codecompass.domain_scope import (
    HINT_KIND_DOMAIN,
    DomainScope,
    DomainScopeViolation,
    ResolvedDomainScope,
    VIOLATION_ARTIFACT_ERROR,
    VIOLATION_EMPTY_SCOPE,
    VIOLATION_UNKNOWN_DOMAIN,
    normalize_repo_relative_path,
    parse_domain_hint,
)

DOMAIN_ANALYSIS_SCHEMA = "codecompass_domain_analysis.v1"
DOMAIN_DESCRIPTOR_SCHEMA = "domain_descriptor.v1"

logger = logging.getLogger(__name__)


def scope_from_domain_hint(
    hint: str | None,
    *,
    enabled: bool,
    strict: bool = True,
    allow_external_references: bool = False,
    max_external_reference_chunks: int = 0,
    requested_by: str = "chat_retrieval_domain_hint",
) -> DomainScope | None:
    """CCRDS-002/006: turn a ``domain:``-prefixed hint into a DomainScope.

    Internal profile hints, unknown unprefixed values and a disabled
    feature flag all return None — the hint then keeps its old soft
    retrieval-profile meaning.
    """
    if not enabled:
        return None
    kind, value = parse_domain_hint(hint)
    if kind != HINT_KIND_DOMAIN:
        return None
    return DomainScope(
        selected_domain_ids=[value],
        strict=strict,
        allow_external_references=allow_external_references,
        max_external_reference_chunks=max_external_reference_chunks,
        requested_by=requested_by,
    )


class DomainScopeResolver:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        artifact_path: str | Path | None = None,
        descriptor_root: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.artifact_path = self._absolutize(artifact_path or "artifacts/codecompass/domains.detected.json")
        self.descriptor_root = self._absolutize(descriptor_root or "domains")

    def _absolutize(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (self.repo_root / path)

    # ---------------------------------------------------------------- artifacts

    def load_detected_domains(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Load domain candidates from ``domains.detected.json``.

        Returns ``(domains, errors)``; a missing file, broken JSON or a
        wrong schema produce errors instead of raising.
        """
        if not self.artifact_path.exists():
            return [], [f"domain_artifact_missing:{self.artifact_path.name}"]
        try:
            payload = json.loads(self.artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [], [f"domain_artifact_unreadable:{exc.__class__.__name__}"]
        if not isinstance(payload, dict):
            return [], ["domain_artifact_invalid:not_an_object"]
        schema = str(payload.get("schema") or "")
        if schema != DOMAIN_ANALYSIS_SCHEMA:
            return [], [f"domain_artifact_schema_mismatch:{schema or 'missing'}"]
        domains = [d for d in list(payload.get("domains") or []) if isinstance(d, dict)]
        errors: list[str] = []
        if not domains:
            errors.append("domain_artifact_empty:no_domains")
        seen: set[str] = set()
        for entry in domains:
            domain_id = str(entry.get("domain_id") or "").strip().lower()
            if domain_id in seen:
                errors.append(f"domain_artifact_duplicate_id:{domain_id}")
            seen.add(domain_id)
        return domains, errors

    def list_domains(self) -> dict[str, Any]:
        """CCRDS-016: stable sorted domain list for API/UI consumption."""
        domains, errors = self.load_detected_domains()
        rows = []
        for entry in domains:
            domain_id = str(entry.get("domain_id") or "").strip().lower()
            if not domain_id:
                continue
            rows.append(
                {
                    "domain_id": domain_id,
                    "display_name": str(entry.get("display_name") or domain_id),
                    "confidence": float(entry.get("confidence") or 0.0),
                    "root_paths": sorted(str(p) for p in list(entry.get("root_paths") or [])),
                    "boundary_warnings": list(entry.get("boundary_warnings") or []),
                    "has_descriptor": self._descriptor_path(domain_id).exists(),
                }
            )
        rows.sort(key=lambda row: row["domain_id"])
        return {"domains": rows, "errors": errors, "artifact_path": self._safe_artifact_label()}

    def _safe_artifact_label(self) -> str:
        # Never leak absolute host paths through the API (CCRDS-016).
        try:
            return self.artifact_path.relative_to(self.repo_root).as_posix()
        except ValueError:
            return self.artifact_path.name

    # -------------------------------------------------------------- descriptors

    def _descriptor_path(self, domain_id: str) -> Path:
        return self.descriptor_root / domain_id / "domain.json"

    def load_descriptor_paths(self, domain_id: str) -> tuple[list[str] | None, list[str]]:
        """Read allowed paths from a manual descriptor, if one applies.

        Returns ``(paths, warnings)``. ``paths`` is None when no usable
        business descriptor exists (capability descriptors without code
        paths are ignored). No code from descriptors is ever executed.
        """
        descriptor_file = self._descriptor_path(domain_id)
        if not descriptor_file.exists():
            return None, []
        try:
            payload = json.loads(descriptor_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, [f"descriptor_unreadable:{domain_id}:{exc.__class__.__name__}"]
        if not isinstance(payload, dict):
            return None, [f"descriptor_invalid:{domain_id}:not_an_object"]
        schema = str(payload.get("schema") or "")
        if schema != DOMAIN_DESCRIPTOR_SCHEMA:
            return None, [f"descriptor_schema_mismatch:{domain_id}:{schema or 'missing'}"]

        source_paths = payload.get("source_paths")
        raw_paths: list[str] = []
        if isinstance(source_paths, dict):
            raw_paths.extend(str(p) for p in list(source_paths.get("code_paths") or []))
        elif isinstance(source_paths, list):
            raw_paths.extend(str(p) for p in source_paths)
        for profile in list(payload.get("rag_profiles") or []):
            if isinstance(profile, dict):
                raw_paths.extend(str(p) for p in list(profile.get("allowed_paths") or []))

        warnings: list[str] = []
        normalized: list[str] = []
        for raw in raw_paths:
            norm = normalize_repo_relative_path(raw, repo_root=self.repo_root)
            if norm is None:
                warnings.append(f"descriptor_path_rejected:{domain_id}:{raw}")
                continue
            if norm not in normalized:
                normalized.append(norm)
        if not normalized:
            # Capability/foundation descriptor without code paths — not a
            # business path scope (see module docstring).
            return None, warnings
        return sorted(normalized), warnings

    # ------------------------------------------------------------------ resolve

    def resolve(self, scope: DomainScope | None) -> ResolvedDomainScope:
        if scope is None or scope.is_empty:
            return ResolvedDomainScope(active=False, strict=bool(scope.strict if scope else True))

        resolved = ResolvedDomainScope(
            active=True,
            strict=scope.strict,
            selected_domain_ids=[str(d).strip().lower() for d in scope.selected_domain_ids if str(d or "").strip()],
            allow_external_references=scope.allow_external_references,
            max_external_reference_chunks=max(0, int(scope.max_external_reference_chunks or 0)),
        )
        domains, artifact_errors = self.load_detected_domains()
        detected_by_id = {
            str(entry.get("domain_id") or "").strip().lower(): entry for entry in domains
        }
        detected_by_display = {
            str(entry.get("display_name") or "").strip().lower(): entry for entry in domains
        }

        hard_artifact_errors = [e for e in artifact_errors if not e.startswith("domain_artifact_empty")]
        for error in artifact_errors:
            resolved.warnings.append(error)

        read_paths: list[str] = []
        write_paths: list[str] = []
        for domain_id in resolved.selected_domain_ids:
            descriptor_paths, descriptor_warnings = self.load_descriptor_paths(domain_id)
            resolved.warnings.extend(descriptor_warnings)
            entry = detected_by_id.get(domain_id) or detected_by_display.get(domain_id)

            detected_paths: list[str] = []
            if entry is not None:
                for raw in list(entry.get("root_paths") or []):
                    norm = normalize_repo_relative_path(raw, repo_root=self.repo_root)
                    if norm is None:
                        resolved.warnings.append(f"detected_path_rejected:{domain_id}:{raw}")
                    elif norm not in detected_paths:
                        detected_paths.append(norm)

            if descriptor_paths is not None:
                domain_paths = descriptor_paths
                provenance = f"descriptor:domains/{domain_id}/domain.json"
                if detected_paths and sorted(detected_paths) != sorted(descriptor_paths):
                    resolved.warnings.append(
                        f"descriptor_overrides_detected:{domain_id}:"
                        f"descriptor={sorted(descriptor_paths)}:detected={sorted(detected_paths)}"
                    )
            elif detected_paths:
                domain_paths = detected_paths
                provenance = f"detected:{self._safe_artifact_label()}"
            else:
                if hard_artifact_errors and entry is None:
                    resolved.violations.append(
                        DomainScopeViolation(
                            kind=VIOLATION_ARTIFACT_ERROR,
                            message=f"domain artifacts unusable while resolving '{domain_id}': {hard_artifact_errors}",
                            matched_domain=domain_id,
                        )
                    )
                elif entry is not None:
                    # Known domain, but neither detected root_paths nor a
                    # descriptor yield a single usable path.
                    if scope.strict:
                        resolved.violations.append(
                            DomainScopeViolation(
                                kind=VIOLATION_EMPTY_SCOPE,
                                message=f"domain '{domain_id}' resolved to zero allowed paths",
                                matched_domain=domain_id,
                            )
                        )
                    else:
                        resolved.warnings.append(f"domain_without_paths_ignored:{domain_id}")
                else:
                    message = f"unknown domain id '{domain_id}' (no detected entry, no descriptor)"
                    if scope.strict:
                        resolved.violations.append(
                            DomainScopeViolation(
                                kind=VIOLATION_UNKNOWN_DOMAIN,
                                message=message,
                                matched_domain=domain_id,
                            )
                        )
                    else:
                        resolved.warnings.append(f"unknown_domain_ignored:{domain_id}")
                continue

            resolved.provenance.append(f"{domain_id}<-{provenance}")
            display_name = str((entry or {}).get("display_name") or domain_id)
            confidence = float((entry or {}).get("confidence") or 0.0)
            resolved.source_domains.append(
                {
                    "domain_id": domain_id,
                    "display_name": display_name,
                    "confidence": confidence,
                    "paths": sorted(domain_paths),
                }
            )
            for path in domain_paths:
                if path not in read_paths:
                    read_paths.append(path)
                # CCRDS-011: without an explicit write contract the write
                # scope defaults to the domain's own paths.
                if path not in write_paths:
                    write_paths.append(path)

        resolved.allowed_read_paths = sorted(read_paths)
        resolved.allowed_write_paths = sorted(write_paths)

        if scope.strict and not resolved.allowed_read_paths and not resolved.violations:
            resolved.violations.append(
                DomainScopeViolation(
                    kind=VIOLATION_EMPTY_SCOPE,
                    message=f"strict domain scope resolved to zero allowed paths for {resolved.selected_domain_ids}",
                )
            )
        return resolved
