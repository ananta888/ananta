"""RCHCS-001: referenced_context_hint.v1 schema.

Every hint produced by any generator (LLM, deterministic, restricted_transformer_inference,
manual, hybrid) uses the same contract.  No generator output reaches a ContextPackage
or a prompt without a valid, referencing hint record.

Staleness states
----------------
fresh            Source hash unchanged, age < 24 h.
possibly_stale   Age 24–72 h OR knowledge-index manifest changed.
stale            Source file hash changed since hint was written.
invalid          Source file no longer exists; hint must be excluded.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

HINT_SCHEMA_VERSION = "referenced_context_hint.v1"

# ── Hint kinds ────────────────────────────────────────────────────────────────
KIND_FILE_SUMMARY = "file_summary"
KIND_SYMBOL_HINT = "symbol_hint"
KIND_DOMAIN_SUMMARY = "domain_summary"
KIND_GRAPH_PATH_HINT = "graph_path_hint"
KIND_TEST_HINT = "test_hint"
KIND_ARCHITECTURE_HINT = "architecture_hint"
KIND_RISK_HINT = "risk_hint"
KIND_MANUAL_NOTE = "manual_note"

ALL_KINDS = frozenset({
    KIND_FILE_SUMMARY, KIND_SYMBOL_HINT, KIND_DOMAIN_SUMMARY, KIND_GRAPH_PATH_HINT,
    KIND_TEST_HINT, KIND_ARCHITECTURE_HINT, KIND_RISK_HINT, KIND_MANUAL_NOTE,
})

# ── Generator kinds ───────────────────────────────────────────────────────────
GEN_LLM = "llm"
GEN_DETERMINISTIC = "deterministic"
GEN_RESTRICTED_TRANSFORMER_INFERENCE = "restricted_transformer_inference"
GEN_MANUAL = "manual"
GEN_HYBRID = "hybrid"

ALL_GEN_KINDS = frozenset({
    GEN_LLM, GEN_DETERMINISTIC, GEN_RESTRICTED_TRANSFORMER_INFERENCE,
    GEN_MANUAL, GEN_HYBRID,
})

# ── Staleness states ──────────────────────────────────────────────────────────
STALENESS_FRESH = "fresh"
STALENESS_POSSIBLY_STALE = "possibly_stale"
STALENESS_STALE = "stale"
STALENESS_INVALID = "invalid"

ALL_STALENESS = frozenset({
    STALENESS_FRESH, STALENESS_POSSIBLY_STALE, STALENESS_STALE, STALENESS_INVALID,
})

# Tasks hints are NOT valid for (regardless of kind)
FORBIDDEN_FOR_AUTHORITATIVE = frozenset({
    "authoritative_code_review",
    "security_claim_without_original_source",
    "legal_compliance_determination",
})

# Freshness thresholds
FRESH_TTL_SECONDS = 86_400      # 24 h
POSSIBLY_STALE_TTL_SECONDS = 259_200  # 72 h


# ── Sub-types ─────────────────────────────────────────────────────────────────

@dataclass
class SourceRef:
    path: str
    sha256: str = ""
    line_start: int | None = None
    line_end: int | None = None
    role: str = "primary_source"  # primary_source | supporting | test | config

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceRef":
        return cls(
            path=str(d.get("path") or ""),
            sha256=str(d.get("sha256") or ""),
            line_start=d.get("line_start"),
            line_end=d.get("line_end"),
            role=str(d.get("role") or "primary_source"),
        )


@dataclass
class CodeCompassRef:
    record_id: str = ""
    node_id: str = ""
    manifest_hash: str = ""
    relation_types: list[str] = field(default_factory=list)  # defines|calls|reads|tests

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CodeCompassRef":
        return cls(
            record_id=str(d.get("record_id") or ""),
            node_id=str(d.get("node_id") or ""),
            manifest_hash=str(d.get("manifest_hash") or ""),
            relation_types=list(d.get("relation_types") or []),
        )


@dataclass
class GeneratorMetadata:
    kind: str = GEN_DETERMINISTIC
    model_id: str | None = None
    engine: str | None = None
    prompt_hash: str | None = None
    tool_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GeneratorMetadata":
        return cls(
            kind=str(d.get("kind") or GEN_DETERMINISTIC),
            model_id=d.get("model_id"),
            engine=d.get("engine"),
            prompt_hash=d.get("prompt_hash"),
            tool_version=d.get("tool_version"),
        )


@dataclass
class ConfidenceMetadata:
    score: float = 0.5
    method: str = "source_ref_coverage+generator_confidence+staleness"
    requires_human_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConfidenceMetadata":
        return cls(
            score=float(d.get("score") or 0.5),
            method=str(d.get("method") or ""),
            requires_human_review=bool(d.get("requires_human_review", False)),
        )


@dataclass
class ValidityMetadata:
    scope: str = "file"  # file|symbol|domain|graph_path|test|architecture
    valid_for_tasks: list[str] = field(default_factory=lambda: [
        "navigation", "explanation", "context_selection"
    ])
    not_valid_for: list[str] = field(default_factory=lambda: [
        "authoritative_code_review",
        "security_claim_without_original_source",
    ])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ValidityMetadata":
        return cls(
            scope=str(d.get("scope") or "file"),
            valid_for_tasks=list(d.get("valid_for_tasks") or []),
            not_valid_for=list(d.get("not_valid_for") or []),
        )

    def is_valid_for(self, task: str) -> bool:
        if task in self.not_valid_for or task in FORBIDDEN_FOR_AUTHORITATIVE:
            return False
        return not self.valid_for_tasks or task in self.valid_for_tasks


@dataclass
class HashMetadata:
    source_hash: str = ""
    context_hash: str = ""
    summary_hash: str = ""
    repo_commit: str | None = None
    knowledge_index_manifest_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HashMetadata":
        return cls(
            source_hash=str(d.get("source_hash") or ""),
            context_hash=str(d.get("context_hash") or ""),
            summary_hash=str(d.get("summary_hash") or ""),
            repo_commit=d.get("repo_commit"),
            knowledge_index_manifest_hash=str(d.get("knowledge_index_manifest_hash") or ""),
        )


@dataclass
class HintPolicyMetadata:
    external_cloud_safe: bool = False
    contains_original_code_excerpt: bool = False
    sensitivity: str = "unknown"  # unknown|low|medium|high
    redaction_status: str = "none"  # none|redacted|required

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HintPolicyMetadata":
        return cls(
            external_cloud_safe=bool(d.get("external_cloud_safe", False)),
            contains_original_code_excerpt=bool(d.get("contains_original_code_excerpt", False)),
            sensitivity=str(d.get("sensitivity") or "unknown"),
            redaction_status=str(d.get("redaction_status") or "none"),
        )


# ── Root hint record ──────────────────────────────────────────────────────────

@dataclass
class ReferencedContextHint:
    id: str
    kind: str
    title: str
    summary: str
    source_refs: list[SourceRef] = field(default_factory=list)
    codecompass_refs: list[CodeCompassRef] = field(default_factory=list)
    generator: GeneratorMetadata = field(default_factory=GeneratorMetadata)
    confidence: ConfidenceMetadata = field(default_factory=ConfidenceMetadata)
    validity: ValidityMetadata = field(default_factory=ValidityMetadata)
    hashes: HashMetadata = field(default_factory=HashMetadata)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    staleness_status: str = STALENESS_FRESH
    policy: HintPolicyMetadata = field(default_factory=HintPolicyMetadata)
    schema: str = HINT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "source_refs": [r.to_dict() for r in self.source_refs],
            "codecompass_refs": [r.to_dict() for r in self.codecompass_refs],
            "generator": self.generator.to_dict(),
            "confidence": self.confidence.to_dict(),
            "validity": self.validity.to_dict(),
            "hashes": self.hashes.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "staleness_status": self.staleness_status,
            "policy": self.policy.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReferencedContextHint":
        return cls(
            id=str(d.get("id") or ""),
            kind=str(d.get("kind") or KIND_FILE_SUMMARY),
            title=str(d.get("title") or ""),
            summary=str(d.get("summary") or ""),
            source_refs=[SourceRef.from_dict(r) for r in (d.get("source_refs") or [])],
            codecompass_refs=[CodeCompassRef.from_dict(r) for r in (d.get("codecompass_refs") or [])],
            generator=GeneratorMetadata.from_dict(d.get("generator") or {}),
            confidence=ConfidenceMetadata.from_dict(d.get("confidence") or {}),
            validity=ValidityMetadata.from_dict(d.get("validity") or {}),
            hashes=HashMetadata.from_dict(d.get("hashes") or {}),
            created_at=float(d.get("created_at") or time.time()),
            updated_at=float(d.get("updated_at") or time.time()),
            staleness_status=str(d.get("staleness_status") or STALENESS_FRESH),
            policy=HintPolicyMetadata.from_dict(d.get("policy") or {}),
            schema=str(d.get("schema") or HINT_SCHEMA_VERSION),
        )

    def is_fresh(self) -> bool:
        return self.staleness_status == STALENESS_FRESH

    def is_safe_for_task(self, task: str) -> bool:
        if self.staleness_status in (STALENESS_STALE, STALENESS_INVALID):
            return False
        return self.validity.is_valid_for(task)

    def is_safe_for_external_cloud(self) -> bool:
        return (
            self.policy.external_cloud_safe
            and not self.policy.contains_original_code_excerpt
            and self.policy.redaction_status != "required"
        )


# ── Validation ────────────────────────────────────────────────────────────────

class HintValidationError(ValueError):
    pass


def validate_hint(hint: ReferencedContextHint) -> None:
    """Raise HintValidationError if the hint is not fit to store."""
    if not hint.id:
        raise HintValidationError("hint.id must not be empty")
    if hint.kind not in ALL_KINDS:
        raise HintValidationError(f"unknown kind: {hint.kind!r}")
    if hint.generator.kind not in ALL_GEN_KINDS:
        raise HintValidationError(f"unknown generator.kind: {hint.generator.kind!r}")
    if not hint.summary.strip():
        raise HintValidationError("hint.summary must not be empty")

    # Require SourceRefs for non-manual hints
    if hint.generator.kind != GEN_MANUAL:
        if not hint.source_refs and not hint.codecompass_refs:
            raise HintValidationError(
                f"Non-manual hint '{hint.id}' must have at least one source_ref or codecompass_ref. "
                "Unreferenced hints cannot be stored."
            )

    # Confidence range
    if not 0.0 <= hint.confidence.score <= 1.0:
        raise HintValidationError(f"confidence.score {hint.confidence.score!r} outside [0,1]")


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_hint_id(path: str, kind: str, extra: str = "") -> str:
    """Stable, reproducible hint ID from path + kind + optional extra."""
    raw = f"{kind}:{path}:{extra}"
    return f"hint:{kind}:{path}:{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def hash_file(path_str: str) -> str:
    """SHA-256 of a file's contents; empty string if file not found."""
    import os
    try:
        with open(path_str, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except (OSError, PermissionError):
        return ""


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def compute_staleness(
    hint: ReferencedContextHint,
    *,
    current_source_hash: str = "",
    current_manifest_hash: str = "",
    source_exists: bool = True,
    now: float | None = None,
) -> str:
    """Return the updated staleness status for a hint.

    Parameters
    ----------
    current_source_hash:
        SHA-256 of the primary source file as it exists right now.
    current_manifest_hash:
        Current knowledge-index manifest hash.
    source_exists:
        False if the primary source file was deleted.
    """
    now = now or time.time()

    if not source_exists:
        return STALENESS_INVALID

    stored_hash = hint.hashes.source_hash
    if current_source_hash and stored_hash and current_source_hash != stored_hash:
        return STALENESS_STALE

    age = now - hint.updated_at
    manifest_changed = (
        current_manifest_hash
        and hint.hashes.knowledge_index_manifest_hash
        and current_manifest_hash != hint.hashes.knowledge_index_manifest_hash
    )

    if age >= POSSIBLY_STALE_TTL_SECONDS or manifest_changed:
        return STALENESS_POSSIBLY_STALE

    if age < FRESH_TTL_SECONDS:
        return STALENESS_FRESH

    return STALENESS_POSSIBLY_STALE


def compute_confidence(
    *,
    source_ref_count: int = 0,
    generator_kind: str = GEN_DETERMINISTIC,
    staleness_status: str = STALENESS_FRESH,
    base_generator_score: float | None = None,
) -> float:
    """Compute a [0,1] confidence score.

    Formula:
        0.4 * source_ref_coverage
      + 0.35 * generator_score
      + 0.25 * freshness_score

    INVALID staleness → 0.0 unconditionally (source no longer exists).
    """
    if staleness_status == STALENESS_INVALID:
        return 0.0

    # source ref coverage: saturates at 3 refs → 1.0
    ref_cov = min(1.0, source_ref_count / 3.0) if source_ref_count > 0 else 0.0

    # generator base score
    _gen_scores: dict[str, float] = {
        GEN_DETERMINISTIC: 0.90,
        GEN_RESTRICTED_TRANSFORMER_INFERENCE: 0.80,
        GEN_MANUAL: 0.85,
        GEN_HYBRID: 0.75,
        GEN_LLM: 0.65,
    }
    gen_score = base_generator_score if base_generator_score is not None else _gen_scores.get(generator_kind, 0.5)

    # freshness penalty
    _freshness: dict[str, float] = {
        STALENESS_FRESH: 1.0,
        STALENESS_POSSIBLY_STALE: 0.6,
        STALENESS_STALE: 0.2,
        STALENESS_INVALID: 0.0,
    }
    freshness = _freshness.get(staleness_status, 0.4)

    return round(min(1.0, max(0.0, 0.40 * ref_cov + 0.35 * gen_score + 0.25 * freshness)), 4)
