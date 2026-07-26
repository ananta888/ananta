"""Hub authority for Recovery tool-run evidence.

Citation IDs are reserved in the authoritative Task aggregate before a Worker
sees them.  A Worker may return a candidate projection for that ID, but the
Hub publishes evidence only after binding the candidate to the current
dispatch lease and reproducing the result facts from its persisted Task data.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any

from ananta_contracts.recovery_run_evidence import (
    build_recovery_tool_run_context,
)


RECOVERY_HUB_TOOL_RUN_RECORD_SCHEMA = (
    "ananta.recovery_hub_tool_run_record.v1"
)
RECOVERY_HUB_RUN_EVIDENCE_CATALOG_SCHEMA = (
    "ananta.recovery_hub_run_evidence_catalog.v1"
)
_RESULT_BINDING_SCHEMA = (
    "ananta.recovery_hub_tool_run_result_binding.v1"
)
_RUN_SOURCE_ID = "RUN_0001"
_RECORD_FIELDS = frozenset(
    {
        "schema",
        "record_id",
        "source_id",
        "source_type",
        "task_id",
        "state",
        "reserved_at",
        "allowed_for_llm_scope",
        "worker_url",
        "proposal_lease",
        "execute_lease",
        "result_binding",
        "evidence_entry",
        "record_digest",
    }
)
_CATALOG_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "record_id",
        "result_binding",
        "entries",
        "catalog_digest",
    }
)
_LEASE_BINDING_FIELDS = frozenset(
    {
        "revision",
        "token_digest",
        "request_fingerprint",
        "worker_url",
    }
)
_RESULT_BINDING_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "record_id",
        "source_id",
        "lease_revision",
        "lease_token_digest",
        "request_fingerprint",
        "worker_url",
        "worker_result_digest",
        "command_sha256",
        "command_length",
        "output_sha256",
        "output_length",
        "exit_code",
        "response_status",
        "digest",
    }
)


class RecoveryHubRunEvidenceError(ValueError):
    """Raised when Hub run-evidence state is malformed or replayed."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RecoveryHubRunEvidenceError(
            "recovery_hub_run_evidence_not_json"
        ) from exc


def _digest(value: Mapping[str, Any], *, field: str) -> str:
    payload = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key != field
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _lease_binding(lease: Mapping[str, Any]) -> dict[str, Any]:
    try:
        revision = int(lease.get("revision") or 0)
    except (TypeError, ValueError) as exc:
        raise RecoveryHubRunEvidenceError(
            "recovery_hub_run_lease_binding_invalid"
        ) from exc
    binding = {
        "revision": revision,
        "token_digest": str(
            lease.get("token_digest") or ""
        ),
        "request_fingerprint": str(
            lease.get("request_fingerprint") or ""
        ),
        "worker_url": str(
            lease.get("worker_url") or ""
        ).strip().rstrip("/"),
    }
    if (
        revision < 1
        or len(binding["token_digest"]) != 64
        or len(binding["request_fingerprint"]) != 64
        or not binding["worker_url"]
    ):
        raise RecoveryHubRunEvidenceError(
            "recovery_hub_run_lease_binding_invalid"
        )
    return binding


def _latest_execution_event(task: Any) -> dict[str, Any]:
    for value in reversed(list(_value(task, "history") or [])):
        if (
            isinstance(value, Mapping)
            and str(value.get("event_type") or "")
            == "execution_result"
        ):
            return dict(value)
    return {}


class RecoveryHubRunEvidenceService:
    """Reserve, verify, persist, and read Recovery run evidence."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.time,
        record_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._clock = clock
        self._record_id_factory = (
            record_id_factory
            or (
                lambda: "recovery-tool-run-"
                + secrets.token_urlsafe(24)
            )
        )

    def _repos(self):
        if self._repository_provider is not None:
            return self._repository_provider()
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        return get_repository_registry()

    @staticmethod
    def _record_digest(record: Mapping[str, Any]) -> str:
        return _digest(record, field="record_digest")

    @staticmethod
    def _catalog_digest(catalog: Mapping[str, Any]) -> str:
        return _digest(catalog, field="catalog_digest")

    @staticmethod
    def _result_binding_digest(
        binding: Mapping[str, Any],
    ) -> str:
        return _digest(binding, field="digest")

    @classmethod
    def _validate_record(
        cls,
        value: Any,
        *,
        task_id: str,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_tool_run_record_missing"
            )
        record = copy.deepcopy(dict(value))
        if (
            set(record) != _RECORD_FIELDS
            or record.get("schema")
            != RECOVERY_HUB_TOOL_RUN_RECORD_SCHEMA
            or str(record.get("task_id") or "") != task_id
            or str(record.get("source_id") or "")
            != _RUN_SOURCE_ID
            or record.get("source_type") != "tool_run"
            or record.get("allowed_for_llm_scope") is not True
            or not str(record.get("record_id") or "").strip()
            or len(str(record.get("record_id") or "")) > 200
            or str(record.get("state") or "")
            not in {
                "reserved",
                "dispatched",
                "result_verified",
                "evidence_missing",
            }
            or not isinstance(record.get("reserved_at"), (int, float))
            or isinstance(record.get("reserved_at"), bool)
            or not str(record.get("worker_url") or "").strip()
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_tool_run_record_invalid"
            )
        for field in ("proposal_lease", "execute_lease"):
            binding = record.get(field)
            if binding is not None and (
                not isinstance(binding, Mapping)
                or set(binding) != _LEASE_BINDING_FIELDS
            ):
                raise RecoveryHubRunEvidenceError(
                    "recovery_hub_tool_run_record_invalid"
                )
            if isinstance(binding, Mapping):
                _lease_binding(binding)
        expected_digest = cls._record_digest(record)
        actual_digest = str(record.get("record_digest") or "")
        if (
            len(actual_digest) != 64
            or not hmac.compare_digest(
                actual_digest,
                expected_digest,
            )
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_tool_run_record_digest_mismatch"
            )
        return record

    def prepare_for_dispatch_lease(
        self,
        *,
        task_id: str,
        details: Mapping[str, Any],
        phase: str,
        lease: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist a RUN identifier before its matching Worker call."""

        normalized_task_id = str(task_id or "").strip()
        normalized_phase = str(phase or "").strip().lower()
        if (
            not normalized_task_id
            or normalized_phase not in {"propose", "execute"}
        ):
            return dict(details)
        binding = _lease_binding(lease)
        updated = copy.deepcopy(dict(details))
        existing_raw = updated.get("recovery_hub_tool_run_record")
        existing: dict[str, Any] | None = None
        if existing_raw is not None:
            existing = self._validate_record(
                existing_raw,
                task_id=normalized_task_id,
            )
        if existing is None:
            # A lease must never invent a citation identifier after the
            # request fingerprint was computed.  Production dispatch reserves
            # and transports the context first; legacy callers simply have no
            # RUN authority and therefore fail closed for RUN citations.
            return updated
        if str(existing.get("state") or "") != "reserved":
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_reservation_inactive"
            )
        record = existing
        if (
            str(record.get("worker_url") or "").rstrip("/")
            != binding["worker_url"]
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_tool_run_worker_mismatch"
            )
        if normalized_phase == "propose":
            record["proposal_lease"] = binding
        else:
            record["execute_lease"] = binding
            record["state"] = "dispatched"
        record["record_digest"] = self._record_digest(record)
        updated["recovery_hub_tool_run_record"] = record
        updated["recovery_tool_run_context"] = (
            build_recovery_tool_run_context(
                task_id=normalized_task_id,
                records=[record],
            )
        )
        return updated

    def reserve_context(
        self,
        *,
        task_id: str,
        details: Mapping[str, Any],
        worker_url: str,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Reserve the provided RUN ID before request fingerprinting."""

        normalized_task_id = str(task_id or "").strip()
        normalized_worker_url = str(
            worker_url or ""
        ).strip().rstrip("/")
        if not normalized_task_id or not normalized_worker_url:
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_reservation_binding_invalid"
            )
        updated = copy.deepcopy(dict(details))
        raw_existing = updated.get(
            "recovery_hub_tool_run_record"
        )
        if raw_existing is not None:
            existing = self._validate_record(
                raw_existing,
                task_id=normalized_task_id,
            )
            if not replace and (
                str(existing.get("state") or "") == "reserved"
                and str(
                    existing.get("worker_url") or ""
                ).rstrip("/")
                == normalized_worker_url
            ):
                return updated
        record: dict[str, Any] = {
            "schema": RECOVERY_HUB_TOOL_RUN_RECORD_SCHEMA,
            "record_id": str(
                self._record_id_factory() or ""
            ).strip(),
            "source_id": _RUN_SOURCE_ID,
            "source_type": "tool_run",
            "task_id": normalized_task_id,
            "state": "reserved",
            "reserved_at": float(self._clock()),
            "allowed_for_llm_scope": True,
            "worker_url": normalized_worker_url,
            "proposal_lease": None,
            "execute_lease": None,
            "result_binding": None,
            "evidence_entry": None,
            "record_digest": "",
        }
        record["record_digest"] = self._record_digest(record)
        updated["recovery_hub_tool_run_record"] = record
        updated["recovery_tool_run_context"] = (
            build_recovery_tool_run_context(
                task_id=normalized_task_id,
                records=[record],
            )
        )
        return updated

    @staticmethod
    def bind_request_context(
        *,
        task: Any,
        value: Any,
    ) -> dict[str, Any] | None:
        """Require the request context to equal the Hub Task projection."""

        from ananta_contracts.recovery_run_evidence import (
            RecoveryRunEvidenceContractError,
            validate_recovery_tool_run_context,
        )

        task_id = str(_value(task, "id") or "").strip()
        details = _mapping(_value(task, "status_reason_details"))
        authority_raw = details.get("recovery_tool_run_context")
        if authority_raw is None:
            if value is not None:
                raise RecoveryHubRunEvidenceError(
                    "recovery_tool_run_context_unexpected"
                )
            return None
        try:
            authority = validate_recovery_tool_run_context(
                authority_raw,
                task_id=task_id,
            )
            supplied = validate_recovery_tool_run_context(
                value,
                task_id=task_id,
            )
        except RecoveryRunEvidenceContractError as exc:
            raise RecoveryHubRunEvidenceError(str(exc)) from exc
        if authority != supplied or not hmac.compare_digest(
            str(authority.get("digest") or ""),
            str(supplied.get("digest") or ""),
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_tool_run_context_mismatch"
            )
        return supplied

    @classmethod
    def _validate_result_binding(
        cls,
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_result_binding_missing"
            )
        binding = copy.deepcopy(dict(value))
        if (
            set(binding) != _RESULT_BINDING_FIELDS
            or binding.get("schema") != _RESULT_BINDING_SCHEMA
            or str(binding.get("source_id") or "")
            != _RUN_SOURCE_ID
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_result_binding_invalid"
            )
        expected = cls._result_binding_digest(binding)
        actual = str(binding.get("digest") or "")
        if (
            len(actual) != 64
            or not hmac.compare_digest(actual, expected)
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_result_binding_digest_mismatch"
            )
        return binding

    @classmethod
    def _build_result_binding(
        cls,
        *,
        task_id: str,
        record: Mapping[str, Any],
        lease: Mapping[str, Any],
        worker_result_digest: str,
        command: str,
        output: str,
        exit_code: int | None,
        response_status: str,
    ) -> dict[str, Any]:
        lease_values = _lease_binding(lease)
        binding: dict[str, Any] = {
            "schema": _RESULT_BINDING_SCHEMA,
            "task_id": task_id,
            "record_id": str(record.get("record_id") or ""),
            "source_id": str(record.get("source_id") or ""),
            "lease_revision": lease_values["revision"],
            "lease_token_digest": lease_values["token_digest"],
            "request_fingerprint": lease_values[
                "request_fingerprint"
            ],
            "worker_url": lease_values["worker_url"],
            "worker_result_digest": worker_result_digest,
            "command_sha256": _sha256(command),
            "command_length": len(command),
            "output_sha256": _sha256(output),
            "output_length": len(output),
            "exit_code": exit_code,
            "response_status": response_status,
        }
        binding["digest"] = cls._result_binding_digest(binding)
        return binding

    @staticmethod
    def _worker_run_candidates(
        envelope: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        projection = _mapping(
            envelope.get("verification_projection")
        )
        answer_verification = _mapping(
            projection.get("answer_verification")
        )
        raw = answer_verification.get("tool_run_refs")
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise RecoveryHubRunEvidenceError(
                "recovery_worker_run_evidence_invalid"
            )
        if any(not isinstance(value, Mapping) for value in raw):
            raise RecoveryHubRunEvidenceError(
                "recovery_worker_run_evidence_invalid"
            )
        return [dict(value) for value in raw]

    @staticmethod
    def _result_payload(output: str) -> dict[str, Any] | None:
        try:
            value = json.loads(output)
        except (TypeError, ValueError):
            return None
        return dict(value) if isinstance(value, Mapping) else None

    @classmethod
    def _build_evidence_entry(
        cls,
        *,
        task_id: str,
        record: Mapping[str, Any],
        binding: Mapping[str, Any],
        command: str,
        output: str,
        exit_code: int,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "source_id": str(record.get("source_id") or ""),
            "source_type": "tool_run",
            "task_id": task_id,
            "run_id": str(record.get("record_id") or ""),
            "tool_name": "shell",
            "command": command,
            "exit_code": exit_code,
            "stdout_hash": _sha256(output)[:32],
            "stdout_sha256": _sha256(output),
            "stderr_hash": _sha256("")[:32],
            "artifact_paths": [],
            "allowed_for_llm_scope": True,
            "evidence_binding": copy.deepcopy(dict(binding)),
        }
        result_payload = cls._result_payload(output)
        if result_payload is not None:
            entry["result_payload"] = result_payload
        entry["evidence_digest"] = _digest(
            entry,
            field="evidence_digest",
        )
        return entry

    @staticmethod
    def _candidate_projection(
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        fields = (
            "source_id",
            "source_type",
            "task_id",
            "run_id",
            "tool_name",
            "command",
            "exit_code",
            "stdout_hash",
            "stderr_hash",
            "allowed_for_llm_scope",
        )
        return {field: value.get(field) for field in fields}

    def accept_worker_result(
        self,
        *,
        task_id: str,
        response: Mapping[str, Any],
        request_data: Any,
        repositories: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Verify one candidate against Hub state and persist its evidence."""

        normalized_task_id = str(task_id or "").strip()
        repos = repositories or self._repos()
        task = repos.task_repo.get_by_id(normalized_task_id)
        if task is None:
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_task_missing"
            )
        details = _mapping(_value(task, "status_reason_details"))
        if details.get("recovery_hub_tool_run_record") is None:
            return []
        record = self._validate_record(
            details.get("recovery_hub_tool_run_record"),
            task_id=normalized_task_id,
        )
        lease = _mapping(details.get("recovery_dispatch_lease"))
        if (
            lease.get("schema")
            != "ananta.recovery_dispatch_lease.v1"
            or lease.get("phase") != "execute"
            or lease.get("state") != "worker_admitted"
            or _mapping(record.get("execute_lease"))
            != _lease_binding(lease)
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_lease_binding_mismatch"
            )
        serializer = getattr(request_data, "model_dump", None)
        request_payload = (
            serializer()
            if callable(serializer)
            else dict(request_data)
            if isinstance(request_data, Mapping)
            else {}
        )
        from agent.services.recovery_dispatch_gate_service import (
            recovery_dispatch_request_fingerprint,
        )

        if not hmac.compare_digest(
            str(lease.get("request_fingerprint") or ""),
            recovery_dispatch_request_fingerprint(
                "execute",
                request_payload,
            ),
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_request_binding_mismatch"
            )
        from agent.services.recovery_worker_result_service import (
            RecoveryWorkerResultService,
        )

        raw_envelope = response.get("recovery_worker_result")
        if raw_envelope is None:
            return []
        envelope = RecoveryWorkerResultService.validate(
            raw_envelope,
            task_id=normalized_task_id,
            phase="execute",
        )
        verification = _mapping(_value(task, "verification_status"))
        persisted_envelope = _mapping(
            _mapping(
                verification.get("recovery_worker_results")
            ).get("execute")
        )
        if envelope != persisted_envelope:
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_worker_result_binding_mismatch"
            )
        command = str(request_payload.get("command") or "")
        output = str(response.get("output") or "")
        raw_exit_code = response.get("exit_code")
        exit_code = (
            int(raw_exit_code)
            if isinstance(raw_exit_code, int)
            and not isinstance(raw_exit_code, bool)
            else None
        )
        response_status = str(
            response.get("status") or ""
        ).strip().lower()
        if (
            str(_value(task, "last_output") or "") != output
            or _value(task, "last_exit_code") != raw_exit_code
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_result_persistence_mismatch"
            )
        event = _latest_execution_event(task)
        if (
            str(event.get("command") or "") != command
            or str(event.get("output") or "") != output
            or event.get("exit_code") != raw_exit_code
            or str(event.get("status") or "").strip().lower()
            != response_status
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_result_persistence_mismatch"
            )
        result_binding = self._build_result_binding(
            task_id=normalized_task_id,
            record=record,
            lease=lease,
            worker_result_digest=str(envelope.get("digest") or ""),
            command=command,
            output=output,
            exit_code=exit_code,
            response_status=response_status,
        )
        if str(record.get("state") or "") in {
            "result_verified",
            "evidence_missing",
        }:
            existing_binding = self._validate_result_binding(
                record.get("result_binding")
            )
            if existing_binding != result_binding:
                raise RecoveryHubRunEvidenceError(
                    "recovery_hub_run_result_replay_mismatch"
                )
            evidence = record.get("evidence_entry")
            return [copy.deepcopy(dict(evidence))] if isinstance(
                evidence,
                Mapping,
            ) else []

        candidates = self._worker_run_candidates(envelope)
        evidence_entry: dict[str, Any] | None = None
        if command and exit_code is not None and candidates:
            if len(candidates) != 1:
                raise RecoveryHubRunEvidenceError(
                    "recovery_worker_run_evidence_mismatch"
                )
            evidence_entry = self._build_evidence_entry(
                task_id=normalized_task_id,
                record=record,
                binding=result_binding,
                command=command,
                output=output,
                exit_code=exit_code,
            )
            if self._candidate_projection(candidates[0]) != (
                self._candidate_projection(evidence_entry)
            ):
                raise RecoveryHubRunEvidenceError(
                    "recovery_worker_run_evidence_mismatch"
                )
        elif candidates:
            raise RecoveryHubRunEvidenceError(
                "recovery_worker_run_evidence_mismatch"
            )

        record["state"] = (
            "result_verified"
            if evidence_entry is not None
            else "evidence_missing"
        )
        record["result_binding"] = result_binding
        record["evidence_entry"] = evidence_entry
        record["record_digest"] = self._record_digest(record)
        catalog: dict[str, Any] = {
            "schema": (
                RECOVERY_HUB_RUN_EVIDENCE_CATALOG_SCHEMA
            ),
            "task_id": normalized_task_id,
            "record_id": record["record_id"],
            "result_binding": copy.deepcopy(result_binding),
            "entries": (
                [copy.deepcopy(evidence_entry)]
                if evidence_entry is not None
                else []
            ),
        }
        catalog["catalog_digest"] = self._catalog_digest(
            catalog
        )
        details["recovery_hub_tool_run_record"] = record
        verification["recovery_hub_run_evidence"] = catalog
        task.status_reason_details = details
        task.verification_status = verification
        if hasattr(task, "updated_at"):
            task.updated_at = float(self._clock())
        repos.task_repo.save(task)
        return copy.deepcopy(catalog["entries"])

    @classmethod
    def _validate_catalog(
        cls,
        value: Any,
        *,
        task_id: str,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_evidence_catalog_missing"
            )
        catalog = copy.deepcopy(dict(value))
        entries = catalog.get("entries")
        if (
            set(catalog) != _CATALOG_FIELDS
            or catalog.get("schema")
            != RECOVERY_HUB_RUN_EVIDENCE_CATALOG_SCHEMA
            or str(catalog.get("task_id") or "") != task_id
            or str(catalog.get("record_id") or "")
            != str(record.get("record_id") or "")
            or not isinstance(entries, list)
            or len(entries) > 1
            or any(not isinstance(item, Mapping) for item in entries)
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_evidence_catalog_invalid"
            )
        if cls._validate_result_binding(
            catalog.get("result_binding")
        ) != cls._validate_result_binding(
            record.get("result_binding")
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_evidence_catalog_mismatch"
            )
        expected = cls._catalog_digest(catalog)
        actual = str(catalog.get("catalog_digest") or "")
        if (
            len(actual) != 64
            or not hmac.compare_digest(actual, expected)
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_evidence_catalog_digest_mismatch"
            )
        return catalog

    @classmethod
    def _validate_evidence_entry(
        cls,
        value: Any,
        *,
        task_id: str,
        record: Mapping[str, Any],
        binding: Mapping[str, Any],
        task: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_evidence_entry_invalid"
            )
        entry = copy.deepcopy(dict(value))
        output = str(_value(task, "last_output") or "")
        event = _latest_execution_event(task)
        raw_exit_code = _value(task, "last_exit_code")
        if not isinstance(raw_exit_code, int) or isinstance(
            raw_exit_code,
            bool,
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_evidence_entry_invalid"
            )
        expected = cls._build_evidence_entry(
            task_id=task_id,
            record=record,
            binding=binding,
            command=str(event.get("command") or ""),
            output=output,
            exit_code=raw_exit_code,
        )
        if entry != expected:
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_evidence_entry_mismatch"
            )
        return entry

    def for_task(
        self,
        task_id: str,
    ) -> list[dict[str, Any]] | None:
        """Load only evidence still bound to the authoritative Task result."""

        normalized_task_id = str(task_id or "").strip()
        task = self._repos().task_repo.get_by_id(
            normalized_task_id
        )
        if task is None:
            return None
        details = _mapping(_value(task, "status_reason_details"))
        raw_record = details.get("recovery_hub_tool_run_record")
        verification = _mapping(_value(task, "verification_status"))
        raw_catalog = verification.get(
            "recovery_hub_run_evidence"
        )
        if raw_record is None and raw_catalog is None:
            return None
        record = self._validate_record(
            raw_record,
            task_id=normalized_task_id,
        )
        if str(record.get("state") or "") not in {
            "result_verified",
            "evidence_missing",
        }:
            return None
        lease = _mapping(details.get("recovery_dispatch_lease"))
        if (
            lease.get("phase") != "execute"
            or str(lease.get("state") or "")
            not in {"worker_admitted", "result_accepted"}
            or _mapping(record.get("execute_lease"))
            != _lease_binding(lease)
        ):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_lease_binding_mismatch"
            )
        catalog = self._validate_catalog(
            raw_catalog,
            task_id=normalized_task_id,
            record=record,
        )
        binding = self._validate_result_binding(
            record.get("result_binding")
        )
        event = _latest_execution_event(task)
        verification_results = _mapping(
            verification.get("recovery_worker_results")
        )
        execute_envelope = _mapping(
            verification_results.get("execute")
        )
        expected_binding = self._build_result_binding(
            task_id=normalized_task_id,
            record=record,
            lease=lease,
            worker_result_digest=str(
                execute_envelope.get("digest") or ""
            ),
            command=str(event.get("command") or ""),
            output=str(_value(task, "last_output") or ""),
            exit_code=(
                int(_value(task, "last_exit_code"))
                if isinstance(
                    _value(task, "last_exit_code"),
                    int,
                )
                and not isinstance(
                    _value(task, "last_exit_code"),
                    bool,
                )
                else None
            ),
            response_status=str(
                event.get("status") or ""
            ).strip().lower(),
        )
        if binding != expected_binding:
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_result_binding_mismatch"
            )
        entries = [
            self._validate_evidence_entry(
                value,
                task_id=normalized_task_id,
                record=record,
                binding=binding,
                task=task,
            )
            for value in catalog["entries"]
        ]
        if (
            str(record.get("state") or "") == "result_verified"
        ) != bool(entries):
            raise RecoveryHubRunEvidenceError(
                "recovery_hub_run_evidence_state_mismatch"
            )
        return entries


_SERVICE = RecoveryHubRunEvidenceService()


def get_recovery_hub_run_evidence_service() -> (
    RecoveryHubRunEvidenceService
):
    return _SERVICE


__all__ = [
    "RECOVERY_HUB_RUN_EVIDENCE_CATALOG_SCHEMA",
    "RECOVERY_HUB_TOOL_RUN_RECORD_SCHEMA",
    "RecoveryHubRunEvidenceError",
    "RecoveryHubRunEvidenceService",
    "get_recovery_hub_run_evidence_service",
]
