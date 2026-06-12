"""CCRDS-001/011: runtime domain scope data model and write-scope validation.

A ``DomainScope`` is an *explicit* user/hub selection of business domains
(``selected_domain_ids``). The ``DomainScopeResolver`` turns it into a
``ResolvedDomainScope`` with hard ``allowed_read_paths`` /
``allowed_write_paths``. Domain discovery artifacts are analysis output
only — a scope never activates itself (CCRDS-DD-001).

Namespace rule (CCRDS-DD-006): business domain ids are referenced with the
``domain:`` prefix (e.g. ``domain:bestellmodul``). Unprefixed values keep
their existing meaning as internal retrieval-profile hints and never
activate a runtime scope.

Write-scope checks reuse the same normalization/traversal rules as
``agent.services.ananta_workspace_mutation_policy`` (CCRDS-DD-007); the
central enforcement hook lives there via ``domain_allowed_write_paths``.

Docs: ``docs/codecompass-runtime-domain-scope.md``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

DOMAIN_SCOPE_HINT_PREFIX = "domain:"

# Mirror of the retrieval-profile DOMAIN_* constants. Kept as plain strings
# so this module stays importable without the profile service.
INTERNAL_RETRIEVAL_DOMAINS: frozenset[str] = frozenset({
    "codecompass", "ai_snake", "worker", "ananta_game", "operator_tui", "ops", "generic",
})

HINT_KIND_NONE = "none"
HINT_KIND_INTERNAL = "internal"
HINT_KIND_DOMAIN = "domain"
HINT_KIND_UNKNOWN = "unknown"

VIOLATION_UNKNOWN_DOMAIN = "unknown_domain"
VIOLATION_EMPTY_SCOPE = "empty_scope"
VIOLATION_ARTIFACT_ERROR = "artifact_error"
VIOLATION_WRITE_OUT_OF_SCOPE = "write_out_of_scope"
VIOLATION_PATH_BLOCKED = "path_blocked"

DECISION_ALLOW = "allow"
DECISION_BLOCKED = "blocked"
DECISION_APPROVAL_REQUIRED = "approval_required"

WRITE_ENFORCEMENT_MODE_STRICT = "strict"
WRITE_ENFORCEMENT_MODE_APPROVAL = "approval"


def parse_domain_hint(hint: str | None) -> tuple[str, str]:
    """Classify a ``chat_retrieval_domain_hint`` value (CCRDS-006).

    Returns ``(kind, value)`` where kind is one of:
      - ``none``: empty hint
      - ``internal``: known retrieval-profile domain (unchanged behaviour)
      - ``domain``: ``domain:<id>`` prefixed business domain; value is the id
      - ``unknown``: unprefixed unknown value (stays a soft/ignored hint)
    """
    raw = str(hint or "").strip()
    if not raw:
        return HINT_KIND_NONE, ""
    if raw.lower().startswith(DOMAIN_SCOPE_HINT_PREFIX):
        domain_id = raw[len(DOMAIN_SCOPE_HINT_PREFIX):].strip().lower()
        return (HINT_KIND_DOMAIN, domain_id) if domain_id else (HINT_KIND_UNKNOWN, raw)
    if raw in INTERNAL_RETRIEVAL_DOMAINS:
        return HINT_KIND_INTERNAL, raw
    return HINT_KIND_UNKNOWN, raw


def normalize_repo_relative_path(raw: str | None, *, repo_root: Path | None = None) -> str | None:
    """Normalize *raw* to a repo-relative POSIX path or return None.

    Absolute paths are only accepted when they resolve under *repo_root*.
    Any path escaping the repo via ``..`` is rejected (returns None) —
    same fail-closed stance as the workspace mutation policy.
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return None
    if text.startswith("~"):
        return None
    pure = PurePosixPath(text)
    if pure.is_absolute():
        if repo_root is None:
            return None
        try:
            rel = Path(text).resolve().relative_to(Path(repo_root).resolve())
        except (ValueError, OSError):
            return None
        pure = PurePosixPath(rel.as_posix())
    # Collapse '.' segments and reject any traversal that climbs out.
    parts: list[str] = []
    for part in pure.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        return None
    return "/".join(parts)


def is_path_within(rel_path: str, allowed_paths: list[str] | tuple[str, ...]) -> bool:
    """True when *rel_path* equals or lives under one of *allowed_paths*.

    Matching is segment-based: ``orders`` allows ``orders/service.py`` but
    not ``orders_extra/file.py``.
    """
    candidate = str(rel_path or "").strip("/")
    if not candidate:
        return False
    candidate_parts = candidate.split("/")
    for allowed in allowed_paths:
        allowed_clean = str(allowed or "").strip("/")
        if not allowed_clean:
            continue
        allowed_parts = allowed_clean.split("/")
        if candidate_parts[: len(allowed_parts)] == allowed_parts:
            return True
    return False


@dataclass(frozen=True)
class DomainScopeViolation:
    kind: str
    message: str
    requested_path: str = ""
    matched_domain: str = ""
    allowed_paths: tuple[str, ...] = ()
    severity: str = "high"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "requested_path": self.requested_path,
            "matched_domain": self.matched_domain,
            "allowed_paths": list(self.allowed_paths),
            "severity": self.severity,
        }


@dataclass(frozen=True)
class DomainScopeDecision:
    decision: str  # allow | blocked | approval_required
    reason: str
    violation: DomainScopeViolation | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "violation": self.violation.as_dict() if self.violation else None,
        }


@dataclass
class DomainScope:
    """Explicit runtime domain selection. Empty selection means: no scope."""

    selected_domain_ids: list[str] = field(default_factory=list)
    strict: bool = True
    allow_external_references: bool = False
    max_external_reference_chunks: int = 0
    requested_by: str = ""

    @property
    def is_empty(self) -> bool:
        return not [d for d in self.selected_domain_ids if str(d or "").strip()]

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_domain_ids": list(self.selected_domain_ids),
            "strict": self.strict,
            "allow_external_references": self.allow_external_references,
            "max_external_reference_chunks": self.max_external_reference_chunks,
            "requested_by": self.requested_by,
        }


@dataclass
class ResolvedDomainScope:
    """Hard path boundaries produced by the resolver.

    ``active`` is False for an empty scope (no filtering at all).
    ``ok`` is False when a strict scope could not be resolved — callers
    must then fail closed instead of falling back to global retrieval
    (CCRDS-DD-003).
    """

    active: bool = False
    strict: bool = True
    selected_domain_ids: list[str] = field(default_factory=list)
    allowed_read_paths: list[str] = field(default_factory=list)
    allowed_write_paths: list[str] = field(default_factory=list)
    source_domains: list[dict[str, Any]] = field(default_factory=list)
    allow_external_references: bool = False
    max_external_reference_chunks: int = 0
    warnings: list[str] = field(default_factory=list)
    violations: list[DomainScopeViolation] = field(default_factory=list)
    provenance: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def read_allowed(self, rel_path: str) -> bool:
        return is_path_within(rel_path, self.allowed_read_paths)

    def write_allowed(self, rel_path: str) -> bool:
        return is_path_within(rel_path, self.allowed_write_paths)

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "strict": self.strict,
            "selected_domain_ids": list(self.selected_domain_ids),
            "allowed_read_paths": list(self.allowed_read_paths),
            "allowed_write_paths": list(self.allowed_write_paths),
            "source_domains": [dict(d) for d in self.source_domains],
            "allow_external_references": self.allow_external_references,
            "max_external_reference_chunks": self.max_external_reference_chunks,
            "warnings": list(self.warnings),
            "violations": [v.as_dict() for v in self.violations],
            "provenance": list(self.provenance),
        }


def validate_write_path(scope: ResolvedDomainScope, raw_path: str) -> DomainScopeDecision:
    """CCRDS-011: validate one write target against the resolved scope."""
    if not scope.active:
        return DomainScopeDecision(decision=DECISION_ALLOW, reason="domain_scope_inactive")
    normalized = normalize_repo_relative_path(raw_path)
    if normalized is None:
        return DomainScopeDecision(
            decision=DECISION_BLOCKED,
            reason="path_normalization_failed",
            violation=DomainScopeViolation(
                kind=VIOLATION_PATH_BLOCKED,
                message=f"write path could not be normalized repo-relative: {raw_path!r}",
                requested_path=str(raw_path or ""),
                allowed_paths=tuple(scope.allowed_write_paths),
                severity="critical",
            ),
        )
    if scope.write_allowed(normalized):
        return DomainScopeDecision(decision=DECISION_ALLOW, reason="within_domain_write_scope")
    return DomainScopeDecision(
        decision=DECISION_BLOCKED,
        reason="outside_domain_write_scope",
        violation=DomainScopeViolation(
            kind=VIOLATION_WRITE_OUT_OF_SCOPE,
            message=f"write outside selected domain(s) {scope.selected_domain_ids}: {normalized}",
            requested_path=normalized,
            matched_domain=",".join(scope.selected_domain_ids),
            allowed_paths=tuple(scope.allowed_write_paths),
            severity="critical",
        ),
    )


def decide_cross_domain_write(
    violation: DomainScopeViolation,
    *,
    mode: str = WRITE_ENFORCEMENT_MODE_STRICT,
) -> DomainScopeDecision:
    """CCRDS-013: map a write violation to blocked or approval_required."""
    if str(mode or "").strip().lower() == WRITE_ENFORCEMENT_MODE_APPROVAL:
        return DomainScopeDecision(
            decision=DECISION_APPROVAL_REQUIRED,
            reason="cross_domain_write_requires_approval",
            violation=violation,
        )
    return DomainScopeDecision(
        decision=DECISION_BLOCKED,
        reason="cross_domain_write_blocked_strict",
        violation=violation,
    )


def build_approval_requirement(
    violation: DomainScopeViolation,
    *,
    tool_name: str = "workspace_write",
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """CCRDS-013: approval payload bound to the concrete path/arguments.

    The digest covers tool name, requested path and arguments so a grant
    only matches exactly this call — never the tool in general.
    """
    payload = {
        "tool_name": tool_name,
        "requested_path": violation.requested_path,
        "arguments": dict(arguments or {}),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "approval_class": "cross_domain_write",
        "tool_name": tool_name,
        "requested_path": violation.requested_path,
        "arguments_digest": digest,
        "violation": violation.as_dict(),
    }
