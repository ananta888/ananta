"""Hub-side verification of a fenced Recovery Worker result."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from agent.services.recovery_grounding_verification_service import (
    get_recovery_grounding_verification_service,
)
from agent.services.recovery_task_mutation_policy import (
    recovery_task_role,
)
from ananta_contracts.recovery_artifact_ingress import (
    MAX_RECOVERY_ARTIFACT_BYTES,
    recovery_artifact_assignment_fingerprint,
)

_RECOVERY_ARTIFACT_RECEIPT_FIELDS = frozenset(
    {
        "kind",
        "task_id",
        "artifact_id",
        "artifact_version_id",
        "filename",
        "media_type",
        "workspace_relative_path",
        "content_hash",
        "size_bytes",
        "provenance_summary",
    }
)
_FORWARDING_NORMALIZATION_FIELDS = frozenset({"id", "path"})


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _relative_path(artifact: dict[str, Any]) -> str:
    candidate = str(
        artifact.get("workspace_relative_path")
        or artifact.get("relative_path")
        or artifact.get("path")
        or artifact.get("filename")
        or ""
    )
    path = PurePosixPath(candidate)
    if (
        not candidate
        or candidate != candidate.strip()
        or "\\" in candidate
        or "\x00" in candidate
        or any(character in candidate for character in "\r\n\t")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != candidate
    ):
        return ""
    return str(path)


class RecoveryResultVerificationService:
    """Verify result evidence through Hub-owned repositories and policies."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any] | None = None,
        verification_service_provider: Callable[[], Any] | None = None,
        grounding_verification_service_provider: (
            Callable[[], Any] | None
        ) = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._verification_service_provider = (
            verification_service_provider
        )
        self._grounding_verification_service_provider = (
            grounding_verification_service_provider
        )

    def _repos(self):
        if self._repository_provider is not None:
            return self._repository_provider()
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        return get_repository_registry()

    def _verification(self):
        if self._verification_service_provider is not None:
            return self._verification_service_provider()
        from agent.services.verification_service import (
            get_verification_service,
        )

        return get_verification_service()

    def _grounding_verification(self):
        if (
            self._grounding_verification_service_provider
            is not None
        ):
            return self._grounding_verification_service_provider()
        return get_recovery_grounding_verification_service()

    def verify_and_record(
        self,
        *,
        task_id: str,
        response: dict[str, Any],
        artifacts: list[dict[str, Any]] | None,
        publish_failure_status: bool = True,
    ) -> dict[str, Any] | None:
        """Record one Hub-derived verification result for a Recovery child.

        A caller holding a result-acceptance guard may defer publication of the
        terminal failure status until its candidate commit has succeeded.
        """

        repos = self._repos()
        task = repos.task_repo.get_by_id(str(task_id or ""))
        if task is None or recovery_task_role(task) != "child":
            return None

        expected = [
            dict(value)
            for value in list(
                _mapping(
                    _value(task, "verification_spec")
                ).get("expected_artifacts")
                or _value(task, "expected_artifacts")
                or []
            )
            if isinstance(value, dict)
            and bool(value.get("required", True))
        ]
        authoritative_artifacts = [
            self._verify_artifact(
                repos=repos,
                task=task,
                artifact=dict(value),
            )
            for value in list(artifacts or [])
            if isinstance(value, dict)
        ]
        artifact_result = self._verification().verify_from_artifacts(
            task_id=str(task_id),
            artifacts=authoritative_artifacts,
            expected_artifacts=expected,
            verification_required=bool(
                expected or authoritative_artifacts
            ),
        )
        grounding_result = self._grounding_verification().verify(
            task=task,
            output=str(response.get("output") or ""),
            artifacts=authoritative_artifacts,
        )
        task.verification_status = {
            **_mapping(_value(task, "verification_status")),
            "execution_artifacts": authoritative_artifacts,
            "artifact_verification": artifact_result,
            "grounding_verification": grounding_result,
        }
        repos.task_repo.save(task)
        response_status = str(
            response.get("status") or ""
        ).strip().lower()
        exit_code = response.get("exit_code")
        execution_passed = (
            response_status == "completed"
            and exit_code in (None, 0)
        )
        artifact_passed = (
            str(artifact_result.get("status") or "").lower()
            == "passed"
        )
        gate_results = {
            "passed": bool(
                execution_passed
                and artifact_passed
                and grounding_result.get("passed") is True
            ),
            "execution_passed": execution_passed,
            "artifact_verification": artifact_result,
            "grounding_verification": grounding_result,
        }
        record = self._verification().create_or_update_record(
            str(task_id),
            trace_id=str(
                _mapping(response.get("trace")).get("trace_id")
                or ""
            )
            or None,
            output=str(response.get("output") or ""),
            exit_code=exit_code,
            gate_results=gate_results,
        )
        record_status = str(
            getattr(record, "status", "") or ""
        ).strip().lower()
        if record_status != "passed" and publish_failure_status:
            from agent.services.task_runtime_service import (
                update_local_task_status,
            )

            update_local_task_status(
                str(task_id),
                "verification_failed",
                force=True,
                event_type="recovery_result_verification_failed",
                event_actor="hub_recovery_result_verifier",
                event_details={
                    "record_id": getattr(record, "id", None),
                    "artifact_status": artifact_result.get(
                        "status"
                    ),
                    "grounding_reason_code": (
                        grounding_result.get("reason_code")
                    ),
                },
                status_reason_code=(
                    "recovery_result_verification_failed"
                ),
            )
        return {
            "record_id": getattr(record, "id", None),
            "status": record_status or "failed",
            "gate_results": gate_results,
            "artifacts": authoritative_artifacts,
            "grounding_verification": grounding_result,
        }

    @staticmethod
    def _verify_artifact(
        *,
        repos: Any,
        task: Any,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        row = dict(artifact)
        artifact_id = str(row.get("artifact_id") or "").strip()
        version_id = str(
            row.get("artifact_version_id") or ""
        ).strip()
        content_hash = str(row.get("content_hash") or "").strip()
        artifact_record = (
            repos.artifact_repo.get_by_id(artifact_id)
            if artifact_id
            else None
        )
        version = (
            repos.artifact_version_repo.get_by_id(version_id)
            if version_id
            else None
        )
        relative_path = _relative_path(row)
        worker_url = str(
            _value(task, "assigned_agent_url") or ""
        ).strip().rstrip("/")
        authoritative_hash = str(
            _value(version, "sha256") or ""
        ).strip()
        artifact_metadata = _mapping(
            _value(artifact_record, "artifact_metadata")
        )
        artifact_binding = _mapping(
            artifact_metadata.get(
                "recovery_artifact_ingress"
            )
        )
        version_binding = _mapping(
            _mapping(
                _value(version, "version_metadata")
            ).get("recovery_artifact_ingress")
        )
        lease = _mapping(
            _mapping(
                _value(task, "status_reason_details")
            ).get("recovery_dispatch_lease")
        )
        provenance = _mapping(
            row.get("provenance_summary")
        )
        identity_payload = {
            key: artifact_binding.get(key)
            for key in (
                "schema",
                "task_id",
                "worker_url",
                "request_fingerprint",
                "lease_token_digest",
                "manifest_digest",
                "source_index",
                "kind",
                "relative_path",
                "sha256",
                "size_bytes",
            )
        }
        try:
            identity_digest = hashlib.sha256(
                json.dumps(
                    identity_payload,
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError, UnicodeError):
            identity_digest = ""
        expected_assignment = ""
        try:
            expected_assignment = (
                recovery_artifact_assignment_fingerprint(
                    task_id=str(_value(task, "id") or ""),
                    worker_url=worker_url,
                )
            )
        except Exception:
            pass
        stored_content_valid = (
            _stored_artifact_content_valid(
                version=version,
                expected_hash=authoritative_hash,
                expected_size=_integer(
                    _value(version, "size_bytes"),
                    default=-1,
                ),
            )
            if version is not None
            else False
        )
        receipt_fields_valid = bool(
            _RECOVERY_ARTIFACT_RECEIPT_FIELDS.issubset(row)
            and set(row).issubset(
                _RECOVERY_ARTIFACT_RECEIPT_FIELDS
                | _FORWARDING_NORMALIZATION_FIELDS
            )
            and (
                "id" not in row
                or str(row.get("id") or "") == artifact_id
            )
            and (
                "path" not in row
                or str(row.get("path") or "")
                in {
                    str(row.get("filename") or ""),
                    str(
                        row.get("workspace_relative_path") or ""
                    ),
                }
            )
        )
        valid = bool(
            receipt_fields_valid
            and artifact_record is not None
            and version is not None
            and str(_value(version, "artifact_id") or "")
            == artifact_id
            and str(
                _value(artifact_record, "latest_version_id") or ""
            )
            == version_id
            and len(authoritative_hash) == 64
            and len(content_hash) == 64
            and hmac.compare_digest(
                authoritative_hash,
                content_hash,
            )
            and stored_content_valid
            and str(_value(artifact_record, "created_by") or "")
            == "hub_recovery_artifact_ingress"
            and artifact_metadata.get("ingestion_mode")
            == "hub_recovery_artifact_ingress"
            and artifact_binding
            and artifact_binding == version_binding
            and artifact_binding.get("schema")
            == "ananta.recovery_artifact_identity.v1"
            and hmac.compare_digest(
                str(artifact_binding.get("task_id") or ""),
                str(_value(task, "id") or ""),
            )
            and hmac.compare_digest(
                str(artifact_binding.get("worker_url") or ""),
                worker_url,
            )
            and hmac.compare_digest(
                str(
                    artifact_binding.get(
                        "assignment_fingerprint"
                    )
                    or ""
                ),
                expected_assignment,
            )
            and hmac.compare_digest(
                str(
                    artifact_binding.get(
                        "request_fingerprint"
                    )
                    or ""
                ),
                str(lease.get("request_fingerprint") or ""),
            )
            and hmac.compare_digest(
                str(
                    artifact_binding.get(
                        "lease_token_digest"
                    )
                    or ""
                ),
                str(lease.get("token_digest") or ""),
            )
            and hmac.compare_digest(
                str(
                    artifact_binding.get("identity_digest")
                    or ""
                ),
                identity_digest,
            )
            and artifact_id
            == f"recovery-artifact-{identity_digest[:32]}"
            and version_id
            == (
                "recovery-artifact-version-"
                f"{identity_digest[:32]}"
            )
            and hmac.compare_digest(
                str(artifact_binding.get("sha256") or ""),
                authoritative_hash,
            )
            and _integer(
                artifact_binding.get("size_bytes"),
                default=-1,
            )
            == _integer(
                _value(version, "size_bytes"),
                default=-2,
            )
            and str(artifact_binding.get("kind") or "")
            == str(row.get("kind") or "")
            and relative_path
            and str(
                artifact_binding.get("relative_path") or ""
            )
            == relative_path
            and str(
                artifact_binding.get("workspace_path") or ""
            )
            == relative_path
            and str(_value(version, "original_filename") or "")
            == str(row.get("filename") or "")
            and str(_value(version, "media_type") or "")
            == str(row.get("media_type") or "")
            and _integer(
                _value(version, "size_bytes"),
                default=-1,
            )
            == _integer(
                row.get("size_bytes"),
                default=-2,
            )
            and str(
                _value(artifact_record, "latest_sha256")
                or ""
            )
            == authoritative_hash
            and str(
                _value(artifact_record, "latest_filename")
                or ""
            )
            == str(row.get("filename") or "")
            and str(
                _value(artifact_record, "latest_media_type")
                or ""
            )
            == str(row.get("media_type") or "")
            and _integer(
                _value(artifact_record, "size_bytes"),
                default=-1,
            )
            == _integer(
                row.get("size_bytes"),
                default=-2,
            )
            and provenance.get("schema")
            == "ananta.recovery_artifact_provenance.v1"
            and provenance.get("authority") == "hub"
            and provenance.get("ingress") == "workspace"
            and hmac.compare_digest(
                str(provenance.get("worker_url") or ""),
                worker_url,
            )
            and hmac.compare_digest(
                str(
                    provenance.get("manifest_digest") or ""
                ),
                str(
                    artifact_binding.get("manifest_digest")
                    or ""
                ),
            )
            and provenance.get("source_index")
            == artifact_binding.get("source_index")
            and str(row.get("task_id") or "") == str(
                _value(task, "id") or ""
            )
        )
        row["relative_path"] = relative_path
        row["_exists"] = valid
        row["_hash_verified"] = valid
        # Every artifact claimed by a Recovery result is evidence, not advisory
        # decoration, and therefore must pass the Hub check.
        row["required"] = True
        return row


def _stored_artifact_content_valid(
    *,
    version: Any,
    expected_hash: str,
    expected_size: int,
) -> bool:
    if (
        expected_size < 0
        or expected_size > MAX_RECOVERY_ARTIFACT_BYTES
        or len(expected_hash) != 64
    ):
        return False
    path = Path(str(_value(version, "storage_path") or ""))
    try:
        if (
            not path.is_file()
            or path.is_symlink()
            or int(path.stat().st_size) != expected_size
        ):
            return False
        content = path.read_bytes()
    except OSError:
        return False
    return (
        len(content) == expected_size
        and hmac.compare_digest(
            hashlib.sha256(content).hexdigest(),
            expected_hash,
        )
    )


def _integer(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


_service = RecoveryResultVerificationService()


def get_recovery_result_verification_service() -> (
    RecoveryResultVerificationService
):
    return _service
