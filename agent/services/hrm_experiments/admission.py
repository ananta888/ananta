"""Fail-closed admission policies for HRM datasets and checkpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.services.hrm_experiments.contracts import (
    HrmContractValidator,
    default_hrm_contract_validator,
)

_DATASET_MEDIA_TYPES = frozenset(
    {"application/json", "application/x-ndjson", "application/octet-stream"}
)
_SAFETENSORS_MEDIA_TYPES = frozenset(
    {"application/vnd.safetensors", "application/octet-stream"}
)
_LOCATOR_PREFIXES = ("artifact:", "fixture:", "generated:")


class HrmAdmissionError(ValueError):
    """A bounded, non-enumerating admission denial."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class HrmAdmissionScope:
    tenant_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class HrmArtifactInspection:
    """Trusted metadata returned by the Hub artifact security boundary."""

    content_digest: str
    size_bytes: int
    media_type: str
    verified: bool
    canonical_content_digest: str | None = None
    shape_digest: str | None = None
    dtypes: tuple[str, ...] = ()
    format_name: str | None = None


class HrmArtifactInspectionPort(Protocol):
    def inspect_locator(self, locator: str) -> HrmArtifactInspection: ...

    def inspect_digest(self, content_digest: str) -> HrmArtifactInspection: ...


class HrmAdmissionRepositoryPort(Protocol):
    def save_dataset(self, manifest: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def save_checkpoint(self, manifest: Mapping[str, Any]) -> Mapping[str, Any]: ...


class HrmManifestAdmissionService:
    """Validate immutable manifests before durable Hub metadata is changed."""

    def __init__(
        self,
        *,
        artifacts: HrmArtifactInspectionPort,
        repository: HrmAdmissionRepositoryPort,
        contracts: HrmContractValidator | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._repository = repository
        self._contracts = contracts or default_hrm_contract_validator

    def admit_dataset(
        self,
        manifest: Mapping[str, Any],
        *,
        scope: HrmAdmissionScope,
    ) -> Mapping[str, Any]:
        candidate = self._copy(manifest)
        self._contracts.validate("puzzle_dataset_manifest", candidate)
        self._require_scope(candidate, scope)
        plugin = candidate["plugin"]
        if plugin["signature_verified"] is not True:
            raise HrmAdmissionError("hrm.dataset_plugin_unverified")
        source = candidate["source"]
        locator = str(source["locator"])
        if not locator.startswith(_LOCATOR_PREFIXES):
            raise HrmAdmissionError("hrm.dataset_locator_forbidden")
        inspection = self._artifacts.inspect_locator(locator)
        if not inspection.verified:
            raise HrmAdmissionError("hrm.dataset_artifact_unverified")
        if inspection.media_type not in _DATASET_MEDIA_TYPES:
            raise HrmAdmissionError("hrm.dataset_media_type_forbidden")
        if inspection.content_digest != source["digest"]:
            raise HrmAdmissionError("hrm.dataset_source_digest_mismatch")
        if (
            inspection.canonical_content_digest is None
            or inspection.canonical_content_digest
            != candidate["canonical_content_digest"]
        ):
            raise HrmAdmissionError("hrm.dataset_canonical_digest_mismatch")
        saved = self._repository.save_dataset(candidate)
        self._contracts.validate("puzzle_dataset_manifest", saved)
        return saved

    def admit_checkpoint(
        self,
        manifest: Mapping[str, Any],
        *,
        scope: HrmAdmissionScope,
        expected_runtime_digest: str,
    ) -> Mapping[str, Any]:
        candidate = self._copy(manifest)
        self._contracts.validate("checkpoint_manifest", candidate)
        self._require_scope(candidate, scope)
        if candidate["state"] != "quarantined":
            raise HrmAdmissionError("hrm.checkpoint_quarantine_required")
        if candidate["compatibility"]["verified"] is not False:
            raise HrmAdmissionError("hrm.checkpoint_preadmitted_forbidden")
        inspection = self._artifacts.inspect_digest(candidate["content_digest"])
        if not inspection.verified:
            raise HrmAdmissionError("hrm.checkpoint_artifact_unverified")
        if inspection.media_type not in _SAFETENSORS_MEDIA_TYPES:
            raise HrmAdmissionError("hrm.checkpoint_media_type_forbidden")
        if inspection.format_name != "safetensors":
            raise HrmAdmissionError("hrm.checkpoint_format_forbidden")
        if inspection.content_digest != candidate["content_digest"]:
            raise HrmAdmissionError("hrm.checkpoint_content_digest_mismatch")
        if inspection.size_bytes != candidate["size_bytes"]:
            raise HrmAdmissionError("hrm.checkpoint_size_mismatch")
        if inspection.shape_digest != candidate["shape_digest"]:
            raise HrmAdmissionError("hrm.checkpoint_shape_digest_mismatch")
        if not inspection.dtypes or not set(inspection.dtypes).issubset(
            set(candidate["dtype_allowlist"])
        ):
            raise HrmAdmissionError("hrm.checkpoint_dtype_forbidden")
        if candidate["compatibility"]["runtime_digest"] != expected_runtime_digest:
            raise HrmAdmissionError("hrm.checkpoint_runtime_incompatible")
        candidate["state"] = "verified"
        candidate["compatibility"]["verified"] = True
        self._contracts.validate("checkpoint_manifest", candidate)
        saved = self._repository.save_checkpoint(candidate)
        self._contracts.validate("checkpoint_manifest", saved)
        return saved

    @staticmethod
    def _require_scope(candidate: Mapping[str, Any], scope: HrmAdmissionScope) -> None:
        candidate_scope = candidate.get("scope")
        if not isinstance(candidate_scope, Mapping):
            raise HrmAdmissionError("hrm.scope_invalid")
        if (
            candidate_scope.get("tenant_id") != scope.tenant_id
            or candidate_scope.get("project_id") != scope.project_id
        ):
            raise HrmAdmissionError("hrm.scope_mismatch")

    @staticmethod
    def _copy(value: Mapping[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(value, ensure_ascii=True))


__all__ = [
    "HrmAdmissionError",
    "HrmAdmissionRepositoryPort",
    "HrmAdmissionScope",
    "HrmArtifactInspection",
    "HrmArtifactInspectionPort",
    "HrmManifestAdmissionService",
]
