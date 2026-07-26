"""Typed Worker-to-Hub projection for fenced Recovery executions.

The Worker may report bounded diagnostics that were produced while its local
Task writes were suppressed.  The Hub stores that report as Worker evidence;
it never treats it as a Hub verification record or orchestration command.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.common.recovery_result_write_boundary import (
    DeferredRecoveryTaskWrites,
    RECOVERY_WORKER_VERIFICATION_PROJECTION_FIELDS,
)


RECOVERY_WORKER_RESULT_SCHEMA = "ananta.recovery_worker_result.v1"
_MAX_ENCODED_BYTES = 1024 * 1024
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 20_000
_MAX_COLLECTION_ENTRIES = 2_048
_MAX_STRING_BYTES = 131_072
_MAX_TOTAL_STRING_BYTES = 786_432
_MAX_OBJECT_KEY_BYTES = 256
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_DANGEROUS_OBJECT_KEYS = frozenset(
    {"__proto__", "prototype", "constructor"}
)
_PHASES = frozenset({"propose", "execute"})
_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "phase",
        "verification_projection",
        "digest",
    }
)


class RecoveryWorkerResultError(ValueError):
    """Raised when a Worker result projection is malformed or unbound."""


@dataclass
class _JsonBudget:
    nodes: int = 0
    total_string_bytes: int = 0


def _bounded_json(
    value: Any,
    *,
    depth: int = 1,
    budget: _JsonBudget | None = None,
) -> None:
    """Validate the generic JSON leaves allowed inside projection fields."""

    state = budget or _JsonBudget()
    if depth > _MAX_JSON_DEPTH:
        raise RecoveryWorkerResultError(
            "recovery_worker_result_json_depth_exceeded"
        )
    state.nodes += 1
    if state.nodes > _MAX_JSON_NODES:
        raise RecoveryWorkerResultError(
            "recovery_worker_result_json_node_limit_exceeded"
        )
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise RecoveryWorkerResultError(
                "recovery_worker_result_json_integer_range_exceeded"
            )
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RecoveryWorkerResultError(
                "recovery_worker_result_not_json"
            )
        return
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeError as exc:
            raise RecoveryWorkerResultError(
                "recovery_worker_result_not_json"
            ) from exc
        if len(encoded) > _MAX_STRING_BYTES:
            raise RecoveryWorkerResultError(
                "recovery_worker_result_json_string_limit_exceeded"
            )
        state.total_string_bytes += len(encoded)
        if state.total_string_bytes > _MAX_TOTAL_STRING_BYTES:
            raise RecoveryWorkerResultError(
                "recovery_worker_result_json_string_budget_exceeded"
            )
        return
    if isinstance(value, Mapping):
        if len(value) > _MAX_COLLECTION_ENTRIES:
            raise RecoveryWorkerResultError(
                "recovery_worker_result_json_collection_limit_exceeded"
            )
        for key, child in value.items():
            if not isinstance(key, str):
                raise RecoveryWorkerResultError(
                    "recovery_worker_result_json_key_invalid"
                )
            try:
                encoded_key = key.encode("utf-8")
            except UnicodeError as exc:
                raise RecoveryWorkerResultError(
                    "recovery_worker_result_json_key_invalid"
                ) from exc
            if (
                not key
                or len(encoded_key) > _MAX_OBJECT_KEY_BYTES
                or key in _DANGEROUS_OBJECT_KEYS
            ):
                raise RecoveryWorkerResultError(
                    "recovery_worker_result_json_key_invalid"
                )
            state.total_string_bytes += len(encoded_key)
            if state.total_string_bytes > _MAX_TOTAL_STRING_BYTES:
                raise RecoveryWorkerResultError(
                    "recovery_worker_result_json_string_budget_exceeded"
                )
            _bounded_json(
                child,
                depth=depth + 1,
                budget=state,
            )
        return
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_COLLECTION_ENTRIES:
            raise RecoveryWorkerResultError(
                "recovery_worker_result_json_collection_limit_exceeded"
            )
        for child in value:
            _bounded_json(
                child,
                depth=depth + 1,
                budget=state,
            )
        return
    raise RecoveryWorkerResultError(
        "recovery_worker_result_not_json"
    )


def _canonical(value: Any) -> tuple[Any, bytes]:
    _bounded_json(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        normalized = json.loads(encoded.decode("utf-8"))
    except (
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        UnicodeError,
    ) as exc:
        raise RecoveryWorkerResultError(
            "recovery_worker_result_not_json"
        ) from exc
    if len(encoded) > _MAX_ENCODED_BYTES:
        raise RecoveryWorkerResultError(
            "recovery_worker_result_too_large"
        )
    return normalized, encoded


def _payload_digest(payload: Mapping[str, Any]) -> str:
    digest_payload = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key != "digest"
    }
    _, encoded = _canonical(digest_payload)
    return hashlib.sha256(encoded).hexdigest()


class RecoveryWorkerResultService:
    """Build, validate, carry, and persist a closed Worker result projection."""

    @staticmethod
    def build(
        boundary: DeferredRecoveryTaskWrites,
    ) -> dict[str, Any]:
        projection: dict[str, Any] = {}
        for mutation in list(boundary.mutations or []):
            if not isinstance(mutation, Mapping):
                continue
            reported = mutation.get("verification_projection")
            if isinstance(reported, Mapping):
                projection.update(copy.deepcopy(dict(reported)))
        payload: dict[str, Any] = {
            "schema": RECOVERY_WORKER_RESULT_SCHEMA,
            "task_id": str(boundary.task_id or "").strip(),
            "phase": str(boundary.phase or "").strip().lower(),
            "verification_projection": projection,
        }
        normalized, _encoded = _canonical(payload)
        normalized["digest"] = _payload_digest(normalized)
        return RecoveryWorkerResultService.validate(
            normalized,
            task_id=normalized["task_id"],
            phase=normalized["phase"],
        )

    def attach(
        self,
        *,
        boundary: DeferredRecoveryTaskWrites,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        envelope = self.build(boundary)
        response["recovery_worker_result"] = envelope
        return envelope

    @staticmethod
    def validate(
        value: Any,
        *,
        task_id: str,
        phase: str,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise RecoveryWorkerResultError(
                "recovery_worker_result_required"
            )
        raw = dict(value)
        if set(raw) != _FIELDS:
            raise RecoveryWorkerResultError(
                "recovery_worker_result_fields_invalid"
            )
        normalized, _encoded = _canonical(raw)
        expected_task_id = str(task_id or "").strip()
        expected_phase = str(phase or "").strip().lower()
        if (
            not isinstance(normalized.get("schema"), str)
            or normalized.get("schema")
            != RECOVERY_WORKER_RESULT_SCHEMA
            or not expected_task_id
            or not isinstance(normalized.get("task_id"), str)
            or str(normalized.get("task_id") or "")
            != expected_task_id
            or expected_phase not in _PHASES
            or not isinstance(normalized.get("phase"), str)
            or str(normalized.get("phase") or "")
            != expected_phase
            or not isinstance(
                normalized.get("verification_projection"),
                dict,
            )
        ):
            raise RecoveryWorkerResultError(
                "recovery_worker_result_binding_invalid"
            )
        projection = normalized["verification_projection"]
        if not set(projection).issubset(
            RECOVERY_WORKER_VERIFICATION_PROJECTION_FIELDS
        ):
            raise RecoveryWorkerResultError(
                "recovery_worker_result_projection_fields_invalid"
            )
        _bounded_json(projection)
        raw_digest = normalized.get("digest")
        actual_digest = (
            raw_digest if isinstance(raw_digest, str) else ""
        )
        expected_digest = _payload_digest(normalized)
        if (
            len(actual_digest) != 64
            or actual_digest.lower() != actual_digest
            or any(
                character not in "0123456789abcdef"
                for character in actual_digest
            )
            or not hmac.compare_digest(
                actual_digest,
                expected_digest,
            )
        ):
            raise RecoveryWorkerResultError(
                "recovery_worker_result_digest_mismatch"
            )
        return normalized

    def merge_response(
        self,
        *,
        task_id: str,
        phase: str,
        response: Mapping[str, Any],
        verification_status: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        merged = dict(verification_status or {})
        raw = response.get("recovery_worker_result")
        if raw is None:
            return merged
        envelope = self.validate(
            raw,
            task_id=task_id,
            phase=phase,
        )
        results = dict(merged.get("recovery_worker_results") or {})
        results[phase] = envelope
        merged["recovery_worker_results"] = results
        return merged

    def accept_proposal_response(
        self,
        *,
        task_id: str,
        response: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        raw = response.get("recovery_worker_result")
        if raw is None:
            return None
        from agent.services.repository_registry import (
            get_repository_registry,
        )
        from agent.services.task_runtime_service import (
            update_local_task_status,
        )

        task = get_repository_registry().task_repo.get_by_id(
            str(task_id or "")
        )
        if task is None:
            raise RecoveryWorkerResultError(
                "recovery_worker_result_task_missing"
            )
        from agent.services.recovery_task_mutation_policy import (
            recovery_task_role,
        )

        if recovery_task_role(task) != "child":
            raise RecoveryWorkerResultError(
                "recovery_worker_result_unexpected"
            )
        current = dict(
            getattr(task, "verification_status", None) or {}
        )
        merged = self.merge_response(
            task_id=str(task_id),
            phase="propose",
            response=response,
            verification_status=current,
        )
        update_local_task_status(
            str(task_id),
            str(getattr(task, "status", "") or "proposing"),
            verification_status=merged,
        )
        return dict(
            merged["recovery_worker_results"]["propose"]
        )

    @staticmethod
    def _proposal_context_state(
        task: Any,
    ) -> tuple[bool, dict[str, Any] | None]:
        raw_verification = (
            task.get("verification_status")
            if isinstance(task, Mapping)
            else getattr(task, "verification_status", None)
        )
        if raw_verification is None:
            return True, None
        if not isinstance(raw_verification, Mapping):
            return False, None
        raw_results = raw_verification.get(
            "recovery_worker_results"
        )
        if raw_results is None:
            return True, None
        if not isinstance(raw_results, Mapping):
            return False, None
        value = raw_results.get("propose")
        if value is None:
            return True, None
        if not isinstance(value, Mapping):
            return False, None
        return True, copy.deepcopy(dict(value))

    @classmethod
    def proposal_context_from_task(
        cls,
        task: Any,
    ) -> dict[str, Any] | None:
        valid, value = cls._proposal_context_state(task)
        if not valid:
            raise RecoveryWorkerResultError(
                "recovery_proposal_context_authority_invalid"
            )
        if value is None:
            return None
        task_id = (
            str(task.get("id") or "")
            if isinstance(task, Mapping)
            else str(getattr(task, "id", "") or "")
        )
        return cls.validate(
            value,
            task_id=task_id,
            phase="propose",
        )

    def proposal_context_for_task(
        self,
        task_id: str,
    ) -> dict[str, Any] | None:
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        task = get_repository_registry().task_repo.get_by_id(
            str(task_id or "")
        )
        return self.proposal_context_from_task(task)

    def bind_execute_proposal_context(
        self,
        *,
        task: Any,
        value: Any,
    ) -> dict[str, Any] | None:
        """Bind execute input to the exact Hub-persisted proposal envelope."""

        task_id = (
            str(task.get("id") or "")
            if isinstance(task, Mapping)
            else str(getattr(task, "id", "") or "")
        )
        authority_valid, authoritative_raw = (
            self._proposal_context_state(task)
        )
        if not authority_valid:
            raise RecoveryWorkerResultError(
                "recovery_proposal_context_authority_invalid"
            )
        if authoritative_raw is None:
            if value is not None:
                raise RecoveryWorkerResultError(
                    "recovery_proposal_context_unexpected"
                )
            return None
        authoritative = self.validate(
            authoritative_raw,
            task_id=task_id,
            phase="propose",
        )
        if value is None:
            raise RecoveryWorkerResultError(
                "recovery_proposal_context_required"
            )
        supplied = self.validate(
            value,
            task_id=task_id,
            phase="propose",
        )
        if (
            not hmac.compare_digest(
                str(authoritative["digest"]),
                str(supplied["digest"]),
            )
            or authoritative != supplied
        ):
            raise RecoveryWorkerResultError(
                "recovery_proposal_context_mismatch"
            )
        return copy.deepcopy(authoritative)

    def apply_proposal_context(
        self,
        *,
        task: dict[str, Any],
        value: Any,
    ) -> None:
        if value is None:
            return
        task_id = str(task.get("id") or "")
        envelope = self.validate(
            value,
            task_id=task_id,
            phase="propose",
        )
        raw_verification = task.get("verification_status")
        if raw_verification is not None and not isinstance(
            raw_verification,
            Mapping,
        ):
            raise RecoveryWorkerResultError(
                "recovery_proposal_context_authority_invalid"
            )
        verification = dict(raw_verification or {})
        projection = dict(
            envelope.get("verification_projection") or {}
        )
        for key in sorted(
            RECOVERY_WORKER_VERIFICATION_PROJECTION_FIELDS
        ):
            if key in projection and key not in verification:
                verification[key] = copy.deepcopy(
                    projection[key]
                )
        raw_results = verification.get(
            "recovery_worker_results"
        )
        if raw_results is not None and not isinstance(
            raw_results,
            Mapping,
        ):
            raise RecoveryWorkerResultError(
                "recovery_proposal_context_authority_invalid"
            )
        results = dict(raw_results or {})
        existing_proposal = results.get("propose")
        if existing_proposal is not None:
            authoritative = self.validate(
                existing_proposal,
                task_id=task_id,
                phase="propose",
            )
            if (
                not hmac.compare_digest(
                    str(authoritative["digest"]),
                    str(envelope["digest"]),
                )
                or authoritative != envelope
            ):
                raise RecoveryWorkerResultError(
                    "recovery_proposal_context_mismatch"
                )
            envelope = authoritative
        results["propose"] = envelope
        verification["recovery_worker_results"] = results
        task["verification_status"] = verification


_service = RecoveryWorkerResultService()


def get_recovery_worker_result_service() -> RecoveryWorkerResultService:
    return _service


__all__ = [
    "RECOVERY_WORKER_RESULT_SCHEMA",
    "RecoveryWorkerResultError",
    "RecoveryWorkerResultService",
    "get_recovery_worker_result_service",
]
