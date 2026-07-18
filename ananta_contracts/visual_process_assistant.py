"""Runtime-neutral contracts for contextual Visual Process assistance.

The models validate identities supplied by the Hub.  They never synthesize a
``SRC_*`` or ``RUN_*`` identifier and keep trust separate from verification.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

EDITOR_CONTEXT_VERSION = "ananta.visual_process.editor_context.v1"
HELP_RESPONSE_VERSION = "ananta.visual_process.help_response.v1"
WORKFLOW_PATCH_VERSION = "ananta.visual_process.workflow_patch.v1"
ASSISTANT_RETRIEVAL_JOB_VERSION = "ananta.visual_process_assistant.retrieval_job.v1"
ASSISTANT_RETRIEVAL_RESULT_VERSION = "ananta.visual_process_assistant.retrieval_result.v1"
ASSISTANT_INFERENCE_JOB_VERSION = "ananta.visual_process_assistant.inference_job.v1"
ASSISTANT_INFERENCE_RESULT_VERSION = "ananta.visual_process_assistant.inference_result.v1"
ASSISTANT_CONTEXT_POLICY_VERSION = 1
MAX_EDITOR_CONTEXT_BYTES = 256 * 1024
MAX_PATCH_OPERATIONS = 100
MAX_SAFE_INTEGER = 9_007_199_254_740_991

_SOURCE_ID = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")
_SHA256 = re.compile(r"^(?:sha256:)?[a-fA-F0-9]{64}$")
_EXTENSION_NAMESPACE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_SECRET_KEYS = frozenset(
    {"api_key", "apikey", "access_token", "refresh_token", "password", "credential", "client_secret", "private_key"}
)
_VOLATILE_CONTEXT_KEYS = frozenset(
    {
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "duration_ms",
        "duration_seconds",
        "poll_interval_ms",
        "animation_frame",
        "dom_id",
        "client_x",
        "client_y",
        "screen_x",
        "screen_y",
    }
)


class TrustLevel(StrEnum):
    extracted = "extracted"
    declared = "declared"
    inferred = "inferred"
    manual = "manual"


class VerificationStatus(StrEnum):
    verified = "verified"
    unverified = "unverified"
    failed = "failed"


class AssistantLocation(BaseModel):
    target_kind: Literal["node", "field", "edge", "canvas", "validation", "runtime", "palette_item"]
    graph_id: str = Field(min_length=1, max_length=200)
    entity_id: str | None = Field(default=None, max_length=200)
    field_path: str | None = Field(default=None, max_length=500)
    role: str | None = Field(default=None, max_length=120)

    model_config = {"extra": "forbid"}


class EvidenceRef(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=200)
    source_id: str | None = None
    source_version: str | None = Field(default=None, max_length=256)
    tenant_id: str | None = Field(default=None, max_length=256)
    scope: str | None = Field(default=None, max_length=256)
    provenance_digest: str | None = None
    path: str | None = Field(default=None, max_length=1000)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    trust_level: TrustLevel
    verification_status: VerificationStatus
    excerpt: str | None = Field(default=None, max_length=4000)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_identity(self) -> "EvidenceRef":
        if self.source_id is not None and _SOURCE_ID.fullmatch(self.source_id) is None:
            raise ValueError("evidence_source_id_invalid")
        if self.provenance_digest is not None and _SHA256.fullmatch(self.provenance_digest) is None:
            raise ValueError("evidence_provenance_digest_invalid")
        if self.line_end is not None and self.line_start is not None and self.line_end < self.line_start:
            raise ValueError("evidence_line_range_invalid")
        if self.verification_status == VerificationStatus.verified:
            if not all((self.source_id, self.source_version, self.tenant_id, self.scope, self.provenance_digest)):
                raise ValueError("verified_evidence_authority_fields_required")
        elif self.excerpt:
            # Unverified/failed material may be counted and diagnosed but must
            # never reach an assistant prompt as content.
            raise ValueError("unverified_evidence_excerpt_forbidden")
        return self


class AssistantClaim(BaseModel):
    claim_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    verification_status: VerificationStatus

    model_config = {"extra": "forbid"}


class WorkflowPatchOperation(BaseModel):
    operation_id: str = Field(min_length=1, max_length=160)
    op: Literal[
        "add_step",
        "remove_step",
        "update_step_field",
        "add_edge",
        "remove_edge",
        "update_edge_condition",
    ]
    step_id: str | None = Field(default=None, max_length=200)
    edge_id: str | None = Field(default=None, max_length=200)
    temp_id: str | None = Field(default=None, max_length=200)
    path: str | None = Field(default=None, max_length=500)
    value: Any = None
    expected_old_value: Any = None
    source: str | None = Field(default=None, max_length=200)
    target: str | None = Field(default=None, max_length=200)
    condition: dict[str, Any] | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_operation(self) -> "WorkflowPatchOperation":
        if self.op == "add_step":
            if not self.temp_id or not isinstance(self.value, dict):
                raise ValueError("patch_add_step_requires_temp_id_and_value")
        elif self.op == "remove_step":
            if not self.step_id:
                raise ValueError("patch_remove_step_requires_step_id")
        elif self.op == "update_step_field":
            if not self.step_id or not self.path or not self.path.startswith("/"):
                raise ValueError("patch_update_step_field_requires_pointer")
            if "expected_old_value" not in self.model_fields_set:
                raise ValueError("patch_update_step_field_requires_expected_old_value")
        elif self.op == "add_edge":
            if not self.temp_id or not self.source or not self.target:
                raise ValueError("patch_add_edge_requires_temp_id_and_endpoints")
        elif self.op in {"remove_edge", "update_edge_condition"}:
            if not self.edge_id:
                raise ValueError("patch_edge_operation_requires_edge_id")
            if self.op == "update_edge_condition" and self.condition is None:
                raise ValueError("patch_update_edge_condition_requires_condition")
            if self.op == "update_edge_condition" and "expected_old_value" not in self.model_fields_set:
                raise ValueError("patch_update_edge_condition_requires_expected_old_value")
        return self


class WorkflowPatch(BaseModel):
    contract_version: Literal[WORKFLOW_PATCH_VERSION] = WORKFLOW_PATCH_VERSION
    graph_id: str = Field(min_length=1, max_length=200)
    definition_revision: int = Field(ge=0)
    base_graph_hash: str
    operations: list[WorkflowPatchOperation] = Field(min_length=1, max_length=MAX_PATCH_OPERATIONS)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    extensions: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_patch(self) -> "WorkflowPatch":
        if _SHA256.fullmatch(self.base_graph_hash) is None:
            raise ValueError("patch_base_graph_hash_invalid")
        operation_ids = [item.operation_id for item in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("patch_operation_id_duplicate")
        temp_ids = [item.temp_id for item in self.operations if item.temp_id]
        if len(temp_ids) != len(set(temp_ids)):
            raise ValueError("patch_temp_id_duplicate")
        _validate_extensions(self.extensions)
        _reject_inline_secrets(self.model_dump(), "")
        return self


class HelpResponse(BaseModel):
    contract_version: Literal[HELP_RESPONSE_VERSION] = HELP_RESPONSE_VERSION
    context_id: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=4000)
    location: AssistantLocation
    explanation: str = Field(default="", max_length=20_000)
    options: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    next_actions: list[str] = Field(default_factory=list, max_length=50)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    claims: list[AssistantClaim] = Field(default_factory=list, max_length=100)
    workflow_patch: WorkflowPatch | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_evidence_links(self) -> "HelpResponse":
        _validate_extensions(self.extensions)
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("response_evidence_id_duplicate")
        known = set(ids)
        for claim in self.claims:
            unknown = set(claim.evidence_refs) - known
            if unknown:
                raise ValueError("claim_evidence_ref_unknown")
            if claim.verification_status == VerificationStatus.verified and not claim.evidence_refs:
                raise ValueError("verified_claim_evidence_required")
        if self.workflow_patch is not None:
            patch_refs = set(self.workflow_patch.evidence_refs)
            patch_refs.update(ref for item in self.workflow_patch.operations for ref in item.evidence_refs)
            if not patch_refs:
                raise ValueError("patch_verified_evidence_required")
            if patch_refs - known:
                raise ValueError("patch_evidence_ref_unknown")
            evidence_by_id = {item.evidence_id: item for item in self.evidence}
            if any(
                evidence_by_id[reference].verification_status != VerificationStatus.verified for reference in patch_refs
            ):
                raise ValueError("patch_verified_evidence_required")
        _reject_inline_secrets(self.model_dump(), "")
        return self


class EditorContextEnvelope(BaseModel):
    contract_version: Literal[EDITOR_CONTEXT_VERSION] = EDITOR_CONTEXT_VERSION
    graph_id: str = Field(min_length=1, max_length=200)
    repository_revision: str = Field(min_length=1, max_length=256)
    codecompass_manifest_hash: str = Field(min_length=1, max_length=256)
    source_allowlist_version: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=160)
    graph_schema_version: str = Field(min_length=1, max_length=100)
    node_registry_version: str = Field(min_length=1, max_length=100)
    definition_revision: int = Field(ge=0)
    definition_hash: str
    draft_hash: str
    runtime_snapshot_hash: str | None = None
    editor_mode: Literal["editor", "ai_snake", "read_only"]
    locale: str = Field(default="de", min_length=2, max_length=20)
    location: AssistantLocation
    graph_excerpt: dict[str, Any]
    effective_configuration: dict[str, Any] = Field(default_factory=dict)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    runtime_overlay: dict[str, Any] | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    allowed_mutations: list[str] = Field(default_factory=list, max_length=100)
    extensions: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_context(self) -> "EditorContextEnvelope":
        for name in ("definition_hash", "draft_hash"):
            if _SHA256.fullmatch(str(getattr(self, name))) is None:
                raise ValueError(f"{name}_invalid")
        if self.runtime_snapshot_hash is not None and _SHA256.fullmatch(self.runtime_snapshot_hash) is None:
            raise ValueError("runtime_snapshot_hash_invalid")
        _validate_extensions(self.extensions)
        _reject_inline_secrets(self.model_dump(), "")
        return self

    def canonical_bytes(self) -> bytes:
        value = _canonical_context_value(self.model_dump())
        payload = _canonical_json(value).encode("utf-8")
        if len(payload) > MAX_EDITOR_CONTEXT_BYTES:
            raise ValueError("editor_context_size_limit_exceeded")
        return payload

    def context_id(self) -> str:
        return f"ctx-sha256:{hashlib.sha256(self.canonical_bytes()).hexdigest()}"


def _validate_extensions(extensions: dict[str, Any]) -> None:
    for key in extensions:
        if _EXTENSION_NAMESPACE.fullmatch(str(key)) is None:
            raise ValueError("assistant_extension_namespace_invalid")


def _reject_inline_secrets(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            child = f"{path}/{key}" if path else f"/{key}"
            if normalized in _SECRET_KEYS and not normalized.endswith("_secret_ref"):
                raise ValueError(f"inline_secret_forbidden:{child}")
            _reject_inline_secrets(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_inline_secrets(item, f"{path}/{index}")


def _canonical_context_value(value: Any, *, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = unicodedata.normalize("NFC", str(raw_key))
            if key in _VOLATILE_CONTEXT_KEYS:
                continue
            if key in normalized:
                raise ValueError("canonical_context_duplicate_normalized_key")
            normalized[key] = _canonical_context_value(item, parent_key=key)
        return normalized
    if isinstance(value, list):
        items = [_canonical_context_value(item, parent_key=parent_key) for item in value]
        sorters = {
            "steps": lambda item: (str(item.get("id", "")),),
            "edges": lambda item: (str(item.get("id", "")), str(item.get("source", "")), str(item.get("target", ""))),
            "validation_issues": lambda item: (
                str(item.get("path", "")),
                str(item.get("code", "")),
                str(item.get("message", "")),
            ),
            "evidence_refs": lambda item: (
                str(item.get("source_id", "")),
                str(item.get("source_version", "")),
                str(item.get("evidence_id", "")),
            ),
        }
        if parent_key in sorters and all(isinstance(item, dict) for item in items):
            items.sort(key=sorters[parent_key])
        return items
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def _canonical_json(value: Any) -> str:
    """Serialize the bounded cross-platform numeric/string contract.

    Floats use fixed decimal notation with at most twelve fractional digits;
    values outside that interoperable domain are rejected instead of hashing
    differently in Python and JavaScript.
    """

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("canonical_context_integer_out_of_range")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical_context_number_non_finite")
        if value == 0:
            return "0"
        if abs(value) > 1_000_000_000_000 or abs(value) < 0.000_000_001:
            raise ValueError("canonical_context_float_out_of_range")
        decimal = Decimal(str(value))
        if max(0, -decimal.as_tuple().exponent) > 12:
            raise ValueError("canonical_context_float_precision_exceeded")
        rendered = format(decimal, "f").rstrip("0").rstrip(".")
        return rendered or "0"
    if isinstance(value, str):
        return json.dumps(unicodedata.normalize("NFC", value), ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=lambda item: tuple(ord(char) for char in item))
        return "{" + ",".join(f"{_canonical_json(key)}:{_canonical_json(value[key])}" for key in keys) + "}"
    raise ValueError("canonical_context_value_unsupported")


def canonical_context_bytes(value: dict[str, Any]) -> bytes:
    payload = _canonical_json(_canonical_context_value(value)).encode("utf-8")
    if len(payload) > MAX_EDITOR_CONTEXT_BYTES:
        raise ValueError("editor_context_size_limit_exceeded")
    return payload


def canonical_context_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_context_bytes(value)).hexdigest()


__all__ = [
    "ASSISTANT_INFERENCE_JOB_VERSION",
    "ASSISTANT_INFERENCE_RESULT_VERSION",
    "ASSISTANT_RETRIEVAL_JOB_VERSION",
    "ASSISTANT_RETRIEVAL_RESULT_VERSION",
    "AssistantClaim",
    "AssistantLocation",
    "EDITOR_CONTEXT_VERSION",
    "EditorContextEnvelope",
    "EvidenceRef",
    "HELP_RESPONSE_VERSION",
    "HelpResponse",
    "MAX_EDITOR_CONTEXT_BYTES",
    "TrustLevel",
    "VerificationStatus",
    "WORKFLOW_PATCH_VERSION",
    "WorkflowPatch",
    "WorkflowPatchOperation",
    "canonical_context_bytes",
    "canonical_context_hash",
]
