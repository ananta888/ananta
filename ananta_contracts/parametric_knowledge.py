"""Runtime-neutral contracts for governed parametric knowledge experts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SOURCE_IDENTIFIER = re.compile(r"^(?:SRC|RUN)_[A-Za-z0-9][A-Za-z0-9_.:-]{0,187}$")
_SENSITIVITY = frozenset({"public", "internal", "personal", "secret", "unknown"})
_EVALUATION = frozenset({"pending", "passed", "failed", "revoked"})
_BANK_STATUS = frozenset({"candidate", "admitted", "active", "superseded", "revoked"})


class ParametricKnowledgeContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def canonical_sha256(value: Mapping[str, Any] | Sequence[Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def knowledge_unit_set_digest(units: Sequence["ParametricKnowledgeUnit"]) -> str:
    bindings = sorted(unit.binding_digest for unit in units)
    if not bindings or len(set(bindings)) != len(bindings):
        raise ParametricKnowledgeContractError("knowledge_expert_units_invalid")
    return canonical_sha256(bindings)


def _closed(value: Any, *, allowed: frozenset[str], reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ParametricKnowledgeContractError(reason)
    if set(value).difference(allowed):
        raise ParametricKnowledgeContractError(f"{reason}_unknown_fields")
    return value


def _text(value: Any, *, reason: str, maximum: int = 512) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ParametricKnowledgeContractError(reason)
    return result


def _identifier(value: Any, *, reason: str) -> str:
    result = _text(value, reason=reason, maximum=192)
    if not _IDENTIFIER.fullmatch(result):
        raise ParametricKnowledgeContractError(reason)
    return result


def _digest(value: Any, *, reason: str) -> str:
    result = str(value or "").strip().lower()
    if not _DIGEST.fullmatch(result):
        raise ParametricKnowledgeContractError(reason)
    return result


def _source_identifier(value: Any, *, reason: str) -> str:
    result = _text(value, reason=reason, maximum=192)
    if not _SOURCE_IDENTIFIER.fullmatch(result):
        raise ParametricKnowledgeContractError(reason)
    return result


def _strings(value: Any, *, reason: str, maximum: int = 256) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ParametricKnowledgeContractError(reason)
    result: list[str] = []
    for item in value:
        token = _identifier(item, reason=reason)
        if token in result:
            raise ParametricKnowledgeContractError(f"{reason}_duplicate")
        result.append(token)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ParametricKnowledgeUnit:
    schema: str
    unit_id: str
    tenant_id: str
    workspace_id: str
    repository_id: str
    source_id: str
    source_revision: str
    content_hash: str
    provenance_digest: str
    domain: str
    parent_id: str
    relations: tuple[str, ...]
    sensitivity: str
    retention_until: str
    license_spdx: str
    citation_ref: str
    citation_required: bool
    stable: bool
    approval_state: str
    revoked: bool

    @classmethod
    def from_mapping(cls, value: Any) -> "ParametricKnowledgeUnit":
        data = _closed(
            value,
            allowed=frozenset(
                {
                    "schema",
                    "unit_id",
                    "tenant_id",
                    "workspace_id",
                    "repository_id",
                    "source_id",
                    "source_revision",
                    "content_hash",
                    "provenance_digest",
                    "domain",
                    "parent_id",
                    "relations",
                    "sensitivity",
                    "retention_until",
                    "license_spdx",
                    "citation_ref",
                    "citation_required",
                    "stable",
                    "approval_state",
                    "revoked",
                }
            ),
            reason="parametric_knowledge_unit_invalid",
        )
        if data.get("schema") != "ananta.parametric-knowledge-unit.v1":
            raise ParametricKnowledgeContractError("parametric_knowledge_unit_schema_invalid")
        sensitivity = str(data.get("sensitivity") or "unknown").strip().lower()
        if sensitivity not in _SENSITIVITY:
            raise ParametricKnowledgeContractError("parametric_knowledge_unit_sensitivity_invalid")
        approval_state = str(data.get("approval_state") or "unreviewed").strip().lower()
        if approval_state not in {"unreviewed", "approved", "denied"}:
            raise ParametricKnowledgeContractError("parametric_knowledge_unit_approval_invalid")
        booleans = ("citation_required", "stable", "revoked")
        if any(not isinstance(data.get(field), bool) for field in booleans):
            raise ParametricKnowledgeContractError("parametric_knowledge_unit_boolean_invalid")
        return cls(
            schema="ananta.parametric-knowledge-unit.v1",
            unit_id=_identifier(data.get("unit_id"), reason="parametric_knowledge_unit_id_invalid"),
            tenant_id=_identifier(data.get("tenant_id"), reason="parametric_knowledge_tenant_invalid"),
            workspace_id=_identifier(data.get("workspace_id"), reason="parametric_knowledge_workspace_invalid"),
            repository_id=_identifier(data.get("repository_id"), reason="parametric_knowledge_repository_invalid"),
            source_id=_source_identifier(
                data.get("source_id"),
                reason="parametric_knowledge_source_invalid",
            ),
            source_revision=_text(data.get("source_revision"), reason="parametric_knowledge_revision_invalid"),
            content_hash=_digest(data.get("content_hash"), reason="parametric_knowledge_content_hash_invalid"),
            provenance_digest=_digest(
                data.get("provenance_digest"), reason="parametric_knowledge_provenance_digest_invalid"
            ),
            domain=_identifier(data.get("domain"), reason="parametric_knowledge_domain_invalid"),
            parent_id=str(data.get("parent_id") or "").strip(),
            relations=_strings(data.get("relations") or [], reason="parametric_knowledge_relations_invalid"),
            sensitivity=sensitivity,
            retention_until=_text(
                data.get("retention_until"), reason="parametric_knowledge_retention_invalid", maximum=64
            ),
            license_spdx=_text(data.get("license_spdx"), reason="parametric_knowledge_license_invalid", maximum=128),
            citation_ref=_text(data.get("citation_ref"), reason="parametric_knowledge_citation_invalid"),
            citation_required=bool(data["citation_required"]),
            stable=bool(data["stable"]),
            approval_state=approval_state,
            revoked=bool(data["revoked"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "relations": list(self.relations)}

    @property
    def binding_digest(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class KnowledgeExpertCompatibility:
    base_model_digest: str
    tokenizer_digest: str
    architecture: str
    target_layer: str
    target_modules: tuple[str, ...]
    runtime_provider: str
    runtime_version: str
    kv_cache_safe: bool

    @classmethod
    def from_mapping(cls, value: Any) -> "KnowledgeExpertCompatibility":
        data = _closed(
            value,
            allowed=frozenset(
                {
                    "base_model_digest",
                    "tokenizer_digest",
                    "architecture",
                    "target_layer",
                    "target_modules",
                    "runtime_provider",
                    "runtime_version",
                    "kv_cache_safe",
                }
            ),
            reason="knowledge_expert_compatibility_invalid",
        )
        if not isinstance(data.get("kv_cache_safe"), bool):
            raise ParametricKnowledgeContractError("knowledge_expert_kv_cache_capability_invalid")
        target_modules = _strings(
            data.get("target_modules") or [],
            reason="knowledge_expert_target_modules_invalid",
            maximum=32,
        )
        if not target_modules:
            raise ParametricKnowledgeContractError("knowledge_expert_target_modules_invalid")
        return cls(
            base_model_digest=_digest(data.get("base_model_digest"), reason="knowledge_expert_model_digest_invalid"),
            tokenizer_digest=_digest(data.get("tokenizer_digest"), reason="knowledge_expert_tokenizer_digest_invalid"),
            architecture=_identifier(data.get("architecture"), reason="knowledge_expert_architecture_invalid"),
            target_layer=_identifier(data.get("target_layer"), reason="knowledge_expert_target_layer_invalid"),
            target_modules=target_modules,
            runtime_provider=_identifier(data.get("runtime_provider"), reason="knowledge_expert_runtime_invalid"),
            runtime_version=_text(
                data.get("runtime_version"), reason="knowledge_expert_runtime_version_invalid", maximum=128
            ),
            kv_cache_safe=bool(data["kv_cache_safe"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "target_modules": list(self.target_modules)}


@dataclass(frozen=True, slots=True)
class KnowledgeExpertManifest:
    schema: str
    expert_id: str
    generation_id: str
    tenant_id: str
    workspace_id: str
    repository_id: str
    knowledge_unit_ids: tuple[str, ...]
    knowledge_unit_digest: str
    adapter_format: str
    adapter_digest: str
    adapter_size_bytes: int
    compatibility: KnowledgeExpertCompatibility
    peft_configuration_digest: str
    training_dataset_digest: str
    evaluation_status: str
    evaluation_digest: str
    policy_decision_digest: str
    signing_key_id: str
    signature: str

    @classmethod
    def from_mapping(cls, value: Any) -> "KnowledgeExpertManifest":
        data = _closed(
            value,
            allowed=frozenset(
                {
                    "schema",
                    "expert_id",
                    "generation_id",
                    "tenant_id",
                    "workspace_id",
                    "repository_id",
                    "knowledge_unit_ids",
                    "knowledge_unit_digest",
                    "adapter_format",
                    "adapter_digest",
                    "adapter_size_bytes",
                    "compatibility",
                    "peft_configuration_digest",
                    "training_dataset_digest",
                    "evaluation_status",
                    "evaluation_digest",
                    "policy_decision_digest",
                    "signing_key_id",
                    "signature",
                }
            ),
            reason="knowledge_expert_manifest_invalid",
        )
        if data.get("schema") != "ananta.knowledge-expert-manifest.v1":
            raise ParametricKnowledgeContractError("knowledge_expert_manifest_schema_invalid")
        if data.get("adapter_format") != "safetensors":
            raise ParametricKnowledgeContractError("knowledge_expert_adapter_format_denied")
        evaluation_status = str(data.get("evaluation_status") or "").strip().lower()
        if evaluation_status not in _EVALUATION:
            raise ParametricKnowledgeContractError("knowledge_expert_evaluation_status_invalid")
        size = data.get("adapter_size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1 or size > 64 * 1024**3:
            raise ParametricKnowledgeContractError("knowledge_expert_adapter_size_invalid")
        return cls(
            schema="ananta.knowledge-expert-manifest.v1",
            expert_id=_identifier(data.get("expert_id"), reason="knowledge_expert_id_invalid"),
            generation_id=_identifier(data.get("generation_id"), reason="knowledge_expert_generation_invalid"),
            tenant_id=_identifier(data.get("tenant_id"), reason="knowledge_expert_tenant_invalid"),
            workspace_id=_identifier(data.get("workspace_id"), reason="knowledge_expert_workspace_invalid"),
            repository_id=_identifier(data.get("repository_id"), reason="knowledge_expert_repository_invalid"),
            knowledge_unit_ids=_strings(
                data.get("knowledge_unit_ids"), reason="knowledge_expert_units_invalid", maximum=4096
            ),
            knowledge_unit_digest=_digest(
                data.get("knowledge_unit_digest"), reason="knowledge_expert_units_digest_invalid"
            ),
            adapter_format="safetensors",
            adapter_digest=_digest(data.get("adapter_digest"), reason="knowledge_expert_adapter_digest_invalid"),
            adapter_size_bytes=size,
            compatibility=KnowledgeExpertCompatibility.from_mapping(data.get("compatibility")),
            peft_configuration_digest=_digest(
                data.get("peft_configuration_digest"), reason="knowledge_expert_peft_digest_invalid"
            ),
            training_dataset_digest=_digest(
                data.get("training_dataset_digest"), reason="knowledge_expert_dataset_digest_invalid"
            ),
            evaluation_status=evaluation_status,
            evaluation_digest=_digest(
                data.get("evaluation_digest"), reason="knowledge_expert_evaluation_digest_invalid"
            ),
            policy_decision_digest=_digest(
                data.get("policy_decision_digest"), reason="knowledge_expert_policy_digest_invalid"
            ),
            signing_key_id=_identifier(data.get("signing_key_id"), reason="knowledge_expert_signing_key_invalid"),
            signature=_text(data.get("signature"), reason="knowledge_expert_signature_invalid", maximum=1024),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["knowledge_unit_ids"] = list(self.knowledge_unit_ids)
        result["compatibility"] = self.compatibility.to_dict()
        return result

    def unsigned_payload(self) -> dict[str, Any]:
        result = self.to_dict()
        result["signature"] = ""
        return result

    @property
    def manifest_digest(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class KnowledgeExpertBank:
    schema: str
    bank_id: str
    generation_id: str
    tenant_id: str
    workspace_id: str
    repository_id: str
    previous_generation_id: str
    expert_manifest_digests: tuple[str, ...]
    status: str
    policy_digest: str
    created_at: str
    signing_key_id: str
    signature: str

    @classmethod
    def from_mapping(cls, value: Any) -> "KnowledgeExpertBank":
        data = _closed(
            value,
            allowed=frozenset(
                {
                    "schema",
                    "bank_id",
                    "generation_id",
                    "tenant_id",
                    "workspace_id",
                    "repository_id",
                    "previous_generation_id",
                    "expert_manifest_digests",
                    "status",
                    "policy_digest",
                    "created_at",
                    "signing_key_id",
                    "signature",
                }
            ),
            reason="knowledge_expert_bank_invalid",
        )
        if data.get("schema") != "ananta.knowledge-expert-bank.v1":
            raise ParametricKnowledgeContractError("knowledge_expert_bank_schema_invalid")
        status = str(data.get("status") or "").strip().lower()
        if status not in _BANK_STATUS:
            raise ParametricKnowledgeContractError("knowledge_expert_bank_status_invalid")
        digests = data.get("expert_manifest_digests")
        if not isinstance(digests, list) or not digests or len(digests) > 100_000:
            raise ParametricKnowledgeContractError("knowledge_expert_bank_manifests_invalid")
        parsed = tuple(_digest(item, reason="knowledge_expert_bank_manifest_digest_invalid") for item in digests)
        if len(set(parsed)) != len(parsed):
            raise ParametricKnowledgeContractError("knowledge_expert_bank_manifest_duplicate")
        return cls(
            schema="ananta.knowledge-expert-bank.v1",
            bank_id=_identifier(data.get("bank_id"), reason="knowledge_expert_bank_id_invalid"),
            generation_id=_identifier(data.get("generation_id"), reason="knowledge_expert_bank_generation_invalid"),
            tenant_id=_identifier(data.get("tenant_id"), reason="knowledge_expert_bank_tenant_invalid"),
            workspace_id=_identifier(data.get("workspace_id"), reason="knowledge_expert_bank_workspace_invalid"),
            repository_id=_identifier(data.get("repository_id"), reason="knowledge_expert_bank_repository_invalid"),
            previous_generation_id=str(data.get("previous_generation_id") or "").strip(),
            expert_manifest_digests=parsed,
            status=status,
            policy_digest=_digest(data.get("policy_digest"), reason="knowledge_expert_bank_policy_digest_invalid"),
            created_at=_text(data.get("created_at"), reason="knowledge_expert_bank_created_at_invalid", maximum=64),
            signing_key_id=_identifier(data.get("signing_key_id"), reason="knowledge_expert_bank_signing_key_invalid"),
            signature=_text(data.get("signature"), reason="knowledge_expert_bank_signature_invalid", maximum=1024),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "expert_manifest_digests": list(self.expert_manifest_digests)}

    @property
    def bank_digest(self) -> str:
        return canonical_sha256(self.to_dict())


__all__ = [
    "KnowledgeExpertBank",
    "KnowledgeExpertCompatibility",
    "KnowledgeExpertManifest",
    "ParametricKnowledgeContractError",
    "ParametricKnowledgeUnit",
    "canonical_sha256",
    "knowledge_unit_set_digest",
]
