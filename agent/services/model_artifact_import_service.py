"""Hub-side admission for immutable model import assignments.

This service validates policy and creates a closed worker assignment. It never
downloads model bytes; materialization remains a delegated worker concern.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from ananta_contracts.model_source_manifest import ModelSourceArtifact, ModelSourceManifest

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelArtifactAdmissionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class ModelArtifactImportRequest:
    tenant_id: str
    project_id: str
    task_id: str
    assignment_id: str
    dispatch_lease_id: str
    artifact_id: str
    expected_sha256: str
    network_authorized: bool
    purpose: str = "evaluation"


@dataclass(frozen=True, slots=True)
class ModelArtifactImportAssignment:
    schema: str
    tenant_id: str
    project_id: str
    task_id: str
    assignment_id: str
    dispatch_lease_id: str
    source_artifact_id: str
    model_id: str
    revision: str
    relative_path: str
    expected_sha256: str
    expected_size_bytes: int
    model_format: str
    quantization: str
    network_authorized: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ModelImportPolicy:
    allowed_publishers: frozenset[str]
    allowed_formats: frozenset[str]
    forbidden_extensions: tuple[str, ...]
    maximum_artifact_bytes: int
    allow_remote_code: bool
    allow_symlinks: bool
    publish_read_only: bool


class ModelImportPolicyLoader:
    def load(self, path: str | Path) -> ModelImportPolicy:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if payload.get("schema") != "ananta.model-import-policy.v1":
                raise ValueError
            fields = (
                payload["allowed_publishers"],
                payload["allowed_formats"],
                payload["forbidden_extensions"],
            )
            if any(not isinstance(value, list) or not value for value in fields):
                raise ValueError
            maximum = payload["maximum_artifact_bytes"]
            if type(maximum) is not int or not 1 <= maximum <= 1024 * 1024**3:
                raise ValueError
            if any(
                type(payload[key]) is not bool
                for key in ("allow_remote_code", "allow_symlinks", "publish_read_only")
            ):
                raise ValueError
            return ModelImportPolicy(
                allowed_publishers=frozenset(map(str, payload["allowed_publishers"])),
                allowed_formats=frozenset(map(str, payload["allowed_formats"])),
                forbidden_extensions=tuple(map(str, payload["forbidden_extensions"])),
                maximum_artifact_bytes=maximum,
                allow_remote_code=payload["allow_remote_code"],
                allow_symlinks=payload["allow_symlinks"],
                publish_read_only=payload["publish_read_only"],
            )
        except Exception as exc:
            raise ModelArtifactAdmissionError("model_import_policy_invalid") from exc


class ModelSourceManifestLoader:
    def load(self, path: str | Path) -> ModelSourceManifest:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return ModelSourceManifest.model_validate(payload)
        except ModelArtifactAdmissionError:
            raise
        except Exception as exc:
            raise ModelArtifactAdmissionError("model_source_manifest_invalid") from exc


class ModelArtifactImportService:
    """Admit an exact artifact without becoming an execution worker."""

    def __init__(self, manifest: ModelSourceManifest, policy: ModelImportPolicy | None = None) -> None:
        self._manifest = manifest
        self._artifacts = {item.artifact_id: item for item in manifest.artifacts}
        self._policy = policy

    def prepare(self, request: ModelArtifactImportRequest) -> ModelArtifactImportAssignment:
        self._validate_request(request)
        artifact = self._artifacts.get(request.artifact_id)
        if artifact is None:
            raise ModelArtifactAdmissionError("model_artifact_unknown")
        if request.expected_sha256 != artifact.sha256:
            raise ModelArtifactAdmissionError("model_artifact_digest_mismatch")
        self._validate_import_policy(artifact)
        self._validate_policy(artifact, request.purpose)
        return ModelArtifactImportAssignment(
            schema="ananta.model-artifact-import-assignment.v1",
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            task_id=request.task_id,
            assignment_id=request.assignment_id,
            dispatch_lease_id=request.dispatch_lease_id,
            source_artifact_id=artifact.artifact_id,
            model_id=artifact.repository_id,
            revision=artifact.revision,
            relative_path=artifact.relative_path,
            expected_sha256=artifact.sha256,
            expected_size_bytes=artifact.size_bytes,
            model_format=artifact.format,
            quantization=artifact.quantization,
            network_authorized=request.network_authorized,
        )

    def _validate_import_policy(self, artifact: ModelSourceArtifact) -> None:
        policy = self._policy
        if policy is None:
            return
        if artifact.publisher not in policy.allowed_publishers:
            raise ModelArtifactAdmissionError("model_artifact_publisher_forbidden")
        if artifact.format not in policy.allowed_formats:
            raise ModelArtifactAdmissionError("model_artifact_format_forbidden")
        if artifact.size_bytes > policy.maximum_artifact_bytes:
            raise ModelArtifactAdmissionError("model_artifact_size_exceeded")
        if artifact.relative_path.lower().endswith(policy.forbidden_extensions):
            raise ModelArtifactAdmissionError("model_artifact_extension_forbidden")
        if policy.allow_remote_code or policy.allow_symlinks or not policy.publish_read_only:
            raise ModelArtifactAdmissionError("model_import_policy_unsafe")

    @staticmethod
    def _validate_request(request: ModelArtifactImportRequest) -> None:
        if any(
            _IDENTIFIER.fullmatch(value) is None
            for value in (
                request.tenant_id,
                request.project_id,
                request.task_id,
                request.assignment_id,
                request.dispatch_lease_id,
                request.artifact_id,
            )
        ):
            raise ModelArtifactAdmissionError("model_import_binding_invalid")
        if _SHA256.fullmatch(request.expected_sha256) is None:
            raise ModelArtifactAdmissionError("model_artifact_digest_invalid")
        if not request.network_authorized:
            raise ModelArtifactAdmissionError("model_import_network_not_authorized")
        if request.purpose not in {"evaluation", "production"}:
            raise ModelArtifactAdmissionError("model_import_purpose_invalid")

    @staticmethod
    def _validate_policy(artifact: ModelSourceArtifact, purpose: str) -> None:
        if artifact.license.status in {"unknown", "rejected"}:
            raise ModelArtifactAdmissionError("model_artifact_license_unverified")
        if purpose == "production" and (
            artifact.activation != "eligible" or artifact.license.status != "approved"
        ):
            raise ModelArtifactAdmissionError("model_artifact_production_not_eligible")
        if purpose == "evaluation" and artifact.activation == "default_off":
            raise ModelArtifactAdmissionError("model_artifact_evaluation_not_approved")


__all__ = [
    "ModelArtifactAdmissionError",
    "ModelArtifactImportAssignment",
    "ModelArtifactImportRequest",
    "ModelArtifactImportService",
    "ModelImportPolicy",
    "ModelImportPolicyLoader",
    "ModelSourceManifestLoader",
]
