"""COMBO-002 + DD-016: graph evidence / trust policy at the import edge.

Public functions:

* :func:`validate_graph_evidence`  -- validate one evidence entry
* :func:`validate_repository_intelligence_snapshot` -- validate a RIG snapshot
* :func:`enforce_import_invariants` -- apply path/size/secret rules and
  reject ambiguous entries from policy decisions

This module is *the* import-edge invariant. It is small, capability-based
and intentionally framework-free so it can be unit-tested in isolation
and called from CLI, worker, or Hub policy layer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from referencing import Registry

try:
    import jsonschema
    from referencing import Registry as _Registry, Resource
except ImportError:  # pragma: no cover -- keep import light
    jsonschema = None  # type: ignore[assignment]
    _Registry = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]


_HERE = Path(__file__).resolve()
# File lives at <repo>/agent/services/tools/graph_evidence.py
_REPO_ROOT = _HERE.parents[3]
SCHEMA_DIR = _REPO_ROOT / "schemas"

GRAPH_EVIDENCE_SCHEMA_PATH = SCHEMA_DIR / "codecompass.graph-evidence.schema.json"
RIG_SNAPSHOT_SCHEMA_PATH = SCHEMA_DIR / "codecompass.repository-intelligence.schema.json"

# Policy-allowed trust levels for hard decisions (security / rights / build-truth).
POLICY_ALLOWED_TRUST = frozenset({"deterministic", "extracted", "manual"})

# Maximum payload sizes (bytes) for fail-closed import.
MAX_RIG_SNAPSHOT_BYTES = 8 * 1024 * 1024  # 8 MiB
MAX_RIG_ENTITIES_PER_KIND = 50_000

# Secret-like keys whose values must never appear in evidence excerpts.
_SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class ValidationFailure:
    """A single fail-closed validation error."""
    path: str
    reason: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "reason": self.reason}
        if self.detail is not None:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class ValidationResult:
    failures: tuple[ValidationFailure, ...]
    diagnostics: dict[str, Any]

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failures": [f.as_dict() for f in self.failures],
            "diagnostics": dict(self.diagnostics),
        }


def _load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_registry() -> Registry | None:
    """Register both schemas in a referencing.Registry so cross-schema
    $ref resolution works without network access.
    """
    if _Registry is None or Resource is None:
        return None
    registry = _Registry()
    for path in (GRAPH_EVIDENCE_SCHEMA_PATH, RIG_SNAPSHOT_SCHEMA_PATH):
        if not path.exists():
            continue
        resource = Resource.from_contents(json.loads(path.read_text(encoding="utf-8")))
        registry = registry.with_resource(uri=path.name, resource=resource)
        registry = registry.with_resource(uri=path.as_uri(), resource=resource)
    return registry


_REGISTRY: Registry | None = None


def _get_registry() -> Registry | None:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def _validate_against_schema(payload: dict[str, Any], schema: dict[str, Any]) -> list[ValidationFailure]:
    if jsonschema is None:
        return [ValidationFailure(path="", reason="jsonschema_unavailable",
                                  detail="jsonschema not importable; cannot enforce schema")]
    registry = _get_registry()
    if registry is not None:
        validator = jsonschema.Draft202012Validator(schema, registry=registry)
    else:
        validator = jsonschema.Draft202012Validator(schema)
    failures: list[ValidationFailure] = []
    for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
        path = "/" + "/".join(str(p) for p in err.absolute_path)
        failures.append(ValidationFailure(path=path, reason=err.validator or "schema_violation",
                                          detail=err.message))
    return failures


def validate_graph_evidence(evidence: dict[str, Any]) -> ValidationResult:
    """Validate one graph evidence entry against the COMBO-002 / DD-016 schema.

    Returns all failures (does not short-circuit). The caller decides
    whether to persist, warn, or refuse the import.
    """
    diagnostics: dict[str, Any] = {"schema": "codecompass.graph-evidence.v1"}
    failures: list[ValidationFailure] = []

    if not isinstance(evidence, dict):
        return ValidationResult(
            failures=(ValidationFailure(path="", reason="not_an_object",
                                        detail=f"expected object, got {type(evidence).__name__}"),),
            diagnostics=diagnostics,
        )

    schema = _load_schema(GRAPH_EVIDENCE_SCHEMA_PATH)
    failures.extend(_validate_against_schema(evidence, schema))

    trust = evidence.get("trust_level")
    verification = evidence.get("verification_status")
    if trust in POLICY_ALLOWED_TRUST and verification == "failed":
        failures.append(ValidationFailure(
            path="/verification_status",
            reason="trust_level_mismatch",
            detail=f"trust_level={trust} requires verification_status != failed",
        ))

    diagnostics["trust_level"] = trust
    diagnostics["verification_status"] = verification
    return ValidationResult(failures=tuple(failures), diagnostics=diagnostics)


def validate_repository_intelligence_snapshot(snapshot: dict[str, Any]) -> ValidationResult:
    """Validate a RIG snapshot against the versioned DD-014 schema."""
    diagnostics: dict[str, Any] = {"schema": "codecompass.repository-intelligence.v1"}
    failures: list[ValidationFailure] = []

    if not isinstance(snapshot, dict):
        return ValidationResult(
            failures=(ValidationFailure(path="", reason="not_an_object",
                                        detail=f"expected object, got {type(snapshot).__name__}"),),
            diagnostics=diagnostics,
        )

    schema = _load_schema(RIG_SNAPSHOT_SCHEMA_PATH)
    failures.extend(_validate_against_schema(snapshot, schema))

    coverage = snapshot.get("coverage") or {}
    status = coverage.get("status")
    diagnostics["coverage_status"] = status

    entities = snapshot.get("entities") or {}
    for kind, items in entities.items():
        if not isinstance(items, list):
            failures.append(ValidationFailure(
                path=f"/entities/{kind}",
                reason="not_an_array",
                detail=f"expected list, got {type(items).__name__}",
            ))
            continue
        if len(items) > MAX_RIG_ENTITIES_PER_KIND:
            failures.append(ValidationFailure(
                path=f"/entities/{kind}",
                reason="payload_too_large",
                detail=f"{kind} has {len(items)} entries (max {MAX_RIG_ENTITIES_PER_KIND})",
            ))

    edges = snapshot.get("edges") or []
    if isinstance(edges, list) and len(edges) > MAX_RIG_ENTITIES_PER_KIND:
        failures.append(ValidationFailure(
            path="/edges",
            reason="payload_too_large",
            detail=f"edges has {len(edges)} entries (max {MAX_RIG_ENTITIES_PER_KIND})",
        ))

    if isinstance(edges, list):
        graph_evidence_schema = _load_schema(GRAPH_EVIDENCE_SCHEMA_PATH)
        for edge_idx, edge in enumerate(edges):
            if not isinstance(edge, dict):
                failures.append(ValidationFailure(
                    path=f"/edges/{edge_idx}",
                    reason="not_an_object",
                    detail=f"expected object, got {type(edge).__name__}",
                ))
                continue
            # Validate edge.trust against the graph-evidence schema in full
            # (this is the trust/verification/policy object).
            trust = edge.get("trust")
            if trust is not None:
                if not isinstance(trust, dict):
                    failures.append(ValidationFailure(
                        path=f"/edges/{edge_idx}/trust",
                        reason="not_an_object",
                        detail=f"expected object, got {type(trust).__name__}",
                    ))
                else:
                    failures.extend([
                        ValidationFailure(
                            path=f"/edges/{edge_idx}/trust{f.path}",
                            reason=f.reason,
                            detail=f.detail,
                        )
                        for f in _validate_against_schema(trust, graph_evidence_schema)
                    ])
            # edge.evidence is a minimal record pointer (source_file +
            # source_kind + source_record_id/source_run_id). It is *not* a
            # full graph-evidence-policy object. We only enforce the
            # identifying-ID rule from the graph-evidence schema here.
            ev = edge.get("evidence")
            if isinstance(ev, dict):
                has_id = bool(ev.get("source_record_id") or ev.get("source_run_id"))
                allowed_reasons = {"manual_fixture", "aggregated_from_children"}
                has_acceptable_reason = ev.get("reason") in allowed_reasons
                if not has_id and not has_acceptable_reason:
                    failures.append(ValidationFailure(
                        path=f"/edges/{edge_idx}/evidence",
                        reason="missing_source_id",
                        detail=("evidence must include source_record_id or source_run_id, "
                                "or reason in {manual_fixture, aggregated_from_children}"),
                    ))

    return ValidationResult(failures=tuple(failures), diagnostics=diagnostics)


def _path_within_workspace(path: str, workspace_dir: Path) -> bool:
    """Return True iff ``path`` is inside ``workspace_dir`` after resolving
    relative components and symlinks."""
    try:
        candidate = Path(path).resolve(strict=False)
        workspace = workspace_dir.resolve(strict=False)
        # use commonpath rather than string-prefix to avoid sibling-dir false positives
        candidate.relative_to(workspace)
        return True
    except (ValueError, RuntimeError):
        return False


def _contains_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def enforce_import_invariants(
    *,
    snapshot: dict[str, Any],
    workspace_dir: Path,
    raw_bytes: bytes | None = None,
) -> ValidationResult:
    """Apply DD-013 / DD-016 fail-closed invariants to a RIG snapshot.

    Combines schema validation with workspace-bound path checks, size
    limits and secret-redaction. Returns *all* failures.
    """
    diagnostics: dict[str, Any] = {"stage": "import_edge"}
    failures: list[ValidationFailure] = []

    if raw_bytes is not None and len(raw_bytes) > MAX_RIG_SNAPSHOT_BYTES:
        failures.append(ValidationFailure(
            path="",
            reason="payload_too_large",
            detail=f"snapshot is {len(raw_bytes)} bytes (max {MAX_RIG_SNAPSHOT_BYTES})",
        ))

    failures.extend(validate_repository_intelligence_snapshot(snapshot).failures)

    repo = snapshot.get("repository") or {}
    ws_field = repo.get("workspace_dir")
    if ws_field and not _path_within_workspace(ws_field, workspace_dir):
        failures.append(ValidationFailure(
            path="/repository/workspace_dir",
            reason="path_outside_workspace",
            detail=f"{ws_field!r} not within {str(workspace_dir)!r}",
        ))

    # Source-file evidence must also live within workspace_dir
    for edge_idx, edge in enumerate(snapshot.get("edges") or []):
        if not isinstance(edge, dict):
            continue
        ev = edge.get("evidence") or {}
        if not isinstance(ev, dict):
            continue
        sf = ev.get("source_file")
        if sf and not _path_within_workspace(sf, workspace_dir):
            failures.append(ValidationFailure(
                path=f"/edges/{edge_idx}/evidence/source_file",
                reason="path_outside_workspace",
                detail=str(sf),
            ))
        for forbidden in ("source_record_id", "source_run_id"):
            value = ev.get(forbidden)
            if isinstance(value, str) and _contains_secret(value):
                failures.append(ValidationFailure(
                    path=f"/edges/{edge_idx}/evidence/{forbidden}",
                    reason="secret_like_value",
                    detail="evidence field looks like a credential",
                ))

    return ValidationResult(failures=tuple(failures), diagnostics=diagnostics)


__all__ = [
    "GRAPH_EVIDENCE_SCHEMA_PATH",
    "RIG_SNAPSHOT_SCHEMA_PATH",
    "POLICY_ALLOWED_TRUST",
    "MAX_RIG_SNAPSHOT_BYTES",
    "MAX_RIG_ENTITIES_PER_KIND",
    "ValidationFailure",
    "ValidationResult",
    "validate_graph_evidence",
    "validate_repository_intelligence_snapshot",
    "enforce_import_invariants",
]