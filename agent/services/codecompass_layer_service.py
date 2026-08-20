"""Hub ports and dispatch boundary for delegated CodeCompass layer work."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol

LAYER_JOB_SCHEMA = "ananta.codecompass_layer_job.v1"
LAYER_RESULT_SCHEMA = "ananta.codecompass_layer_job_result.v1"
LAYER_DISPATCH_SCHEMA = "ananta.codecompass_layer_dispatch.v1"


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


class CodeCompassLayerBackendPort(Protocol):
    def list_profiles(self) -> list[str]: ...
    def show_head(self, profile_id: str) -> dict[str, Any] | None: ...
    def diff(self, **kwargs: Any) -> dict[str, Any]: ...
    def plan_update(self, **kwargs: Any) -> dict[str, Any]: ...
    def apply_update(self, **kwargs: Any) -> dict[str, Any]: ...
    def compact(self, **kwargs: Any) -> dict[str, Any]: ...
    def admit_result(self, result: Mapping[str, Any]) -> dict[str, Any]: ...


class CodeCompassLayerTaskQueuePort(Protocol):
    def dispatch(self, *, envelope: Mapping[str, Any]) -> Mapping[str, Any]: ...


class CodeCompassLayerDispatchRepositoryPort(Protocol):
    def get(self, task_id: str) -> Mapping[str, Any] | None: ...
    def save(self, record: Mapping[str, Any]) -> None: ...


class CodeCompassLayerPublisherPort(Protocol):
    def publish(
        self,
        *,
        dispatch: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class UnavailableCodeCompassLayerBackend:
    def _unavailable(self):
        raise RuntimeError("codecompass_layer_worker_dispatch_required")

    def list_profiles(self) -> list[str]: self._unavailable()
    def show_head(self, profile_id: str) -> dict[str, Any] | None: self._unavailable()
    def diff(self, **kwargs: Any) -> dict[str, Any]: self._unavailable()
    def plan_update(self, **kwargs: Any) -> dict[str, Any]: self._unavailable()
    def apply_update(self, **kwargs: Any) -> dict[str, Any]: self._unavailable()
    def compact(self, **kwargs: Any) -> dict[str, Any]: self._unavailable()
    def admit_result(self, result: Mapping[str, Any]) -> dict[str, Any]: self._unavailable()


class CodeCompassLayerDispatchBackend:
    """Hub-owned intent, dispatch and result-admission coordinator."""

    def __init__(
        self,
        *,
        query_backend: CodeCompassLayerBackendPort,
        task_queue: CodeCompassLayerTaskQueuePort,
        dispatch_repository: CodeCompassLayerDispatchRepositoryPort,
        publisher: CodeCompassLayerPublisherPort,
        writes_enabled: bool | Callable[[], bool] = False,
    ) -> None:
        self._query = query_backend
        self._queue = task_queue
        self._dispatches = dispatch_repository
        self._publisher = publisher
        self._writes_enabled = writes_enabled

    def _writes_are_enabled(self) -> bool:
        return bool(self._writes_enabled() if callable(self._writes_enabled) else self._writes_enabled)

    def _require_writes(self) -> None:
        if not self._writes_are_enabled():
            raise RuntimeError("codecompass_layer_writes_disabled")

    def list_profiles(self) -> list[str]:
        return self._query.list_profiles()

    def show_head(self, profile_id: str) -> dict[str, Any] | None:
        return self._query.show_head(profile_id)

    def diff(self, **kwargs: Any) -> dict[str, Any]:
        return self._query.diff(**kwargs)

    def plan_update(self, **kwargs: Any) -> dict[str, Any]:
        return self._query.plan_update(**kwargs)

    @staticmethod
    def _plan_binding(plan: Mapping[str, Any]) -> tuple[str, list[str]]:
        manifest = plan.get("new_manifest") if isinstance(plan.get("new_manifest"), Mapping) else {}
        revision = str(
            plan.get("input_revision")
            or plan.get("target_revision")
            or manifest.get("revision")
            or ""
        ).strip()
        raw_kinds = plan.get("artifact_kinds")
        if not raw_kinds:
            artifacts = manifest.get("artifacts") or manifest.get("layers") or plan.get("layers") or {}
            raw_kinds = artifacts.keys() if isinstance(artifacts, Mapping) else ()
        kinds = sorted({str(kind).strip() for kind in list(raw_kinds or []) if str(kind).strip()})
        if not revision:
            raise ValueError("codecompass_layer_input_revision_required")
        if not kinds:
            raise ValueError("codecompass_layer_artifact_kinds_required")
        return revision, kinds

    def _dispatch(
        self,
        *,
        action: str,
        plan: Mapping[str, Any],
        profile_ref: Any,
        profile_id: str,
        expected_generation: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_writes()
        if int(expected_generation) < 0:
            raise ValueError("codecompass_layer_generation_invalid")
        if not str(idempotency_key or "").strip():
            raise ValueError("codecompass_layer_idempotency_required")
        input_revision, artifact_kinds = self._plan_binding(plan)
        intent = {
            "schema": LAYER_JOB_SCHEMA,
            "action": action,
            "plan": dict(plan),
            "profile_ref": profile_ref,
            "profile_id": str(profile_id),
            "profile_digest": _canonical_digest(profile_ref),
            "input_revision": input_revision,
            "artifact_kinds": artifact_kinds,
            "expected_generation": int(expected_generation),
            "idempotency_key": str(idempotency_key),
        }
        intent_digest = _canonical_digest(intent)
        task_id = f"cc_layer_{intent_digest[:32]}"
        envelope = {**intent, "task_id": task_id, "intent_digest": intent_digest}
        existing = self._dispatches.get(task_id)
        if existing and str(existing.get("state")) in {"dispatched", "published"}:
            return self._dispatch_response(existing)
        planned = {
            "schema": LAYER_DISPATCH_SCHEMA,
            "state": "planned",
            "task_id": task_id,
            "intent": envelope,
        }
        self._dispatches.save(planned)
        binding = dict(self._queue.dispatch(envelope=envelope) or {})
        if str(binding.get("task_id") or "") != task_id:
            raise ValueError("codecompass_layer_queue_task_binding_invalid")
        if not str(binding.get("assignment_id") or "") or not str(binding.get("dispatch_lease_id") or ""):
            raise ValueError("codecompass_layer_queue_lease_binding_required")
        dispatched = {**planned, "state": "dispatched", "binding": binding}
        self._dispatches.save(dispatched)
        return self._dispatch_response(dispatched)

    @staticmethod
    def _dispatch_response(record: Mapping[str, Any]) -> dict[str, Any]:
        binding = dict(record.get("binding") or {})
        intent = dict(record.get("intent") or {})
        return {
            "status": "published" if record.get("state") == "published" else "queued",
            "task_id": str(record.get("task_id") or ""),
            "assignment_id": str(binding.get("assignment_id") or ""),
            "dispatch_lease_id": str(binding.get("dispatch_lease_id") or ""),
            "intent_digest": str(intent.get("intent_digest") or ""),
        }

    def apply_update(self, **kwargs: Any) -> dict[str, Any]:
        return self._dispatch(
            action="apply_update",
            plan=dict(kwargs["plan"]),
            profile_ref=kwargs["profile_ref"],
            profile_id=str(kwargs["profile_id"]),
            expected_generation=int(kwargs["expected_generation"]),
            idempotency_key=str(kwargs["idempotency_key"]),
        )

    def compact(self, **kwargs: Any) -> dict[str, Any]:
        dry_run = bool(kwargs.get("dry_run", True))
        plan = self._query.compact(**{**kwargs, "dry_run": True})
        if dry_run:
            return plan
        return self._dispatch(
            action="compact",
            plan=dict(plan),
            profile_ref={"profile_id": str(kwargs["profile_id"])},
            profile_id=str(kwargs["profile_id"]),
            expected_generation=int(kwargs["expected_generation"]),
            idempotency_key=str(kwargs["idempotency_key"]),
        )

    def admit_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        if str(result.get("schema") or "") != LAYER_RESULT_SCHEMA:
            raise ValueError("codecompass_layer_result_schema_invalid")
        task_id = str(result.get("task_id") or "")
        record = self._dispatches.get(task_id)
        if not record:
            raise ValueError("codecompass_layer_dispatch_unknown")
        if record.get("state") == "published":
            return dict(record.get("publication") or {})
        if record.get("state") != "dispatched":
            raise ValueError("codecompass_layer_dispatch_not_active")
        intent = dict(record.get("intent") or {})
        binding = dict(record.get("binding") or {})
        comparisons = {
            "task_id": task_id,
            "assignment_id": str(binding.get("assignment_id") or ""),
            "dispatch_lease_id": str(binding.get("dispatch_lease_id") or ""),
            "intent_digest": str(intent.get("intent_digest") or ""),
            "input_revision": str(intent.get("input_revision") or ""),
            "profile_digest": str(intent.get("profile_digest") or ""),
        }
        if any(str(result.get(field) or "") != expected for field, expected in comparisons.items()):
            raise ValueError("codecompass_layer_result_binding_invalid")
        expected_kinds = list(intent.get("artifact_kinds") or [])
        result_kinds = sorted({str(kind) for kind in list(result.get("artifact_kinds") or [])})
        artifact_set = result.get("artifact_set")
        if result_kinds != expected_kinds or not isinstance(artifact_set, Mapping):
            raise ValueError("codecompass_layer_result_artifact_set_incomplete")
        if sorted(str(kind) for kind in artifact_set) != expected_kinds:
            raise ValueError("codecompass_layer_result_artifact_set_incomplete")
        for artifact in artifact_set.values():
            if not isinstance(artifact, Mapping):
                raise ValueError("codecompass_layer_result_artifact_invalid")
            digest = str(artifact.get("content_digest") or artifact.get("digest") or "")
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("codecompass_layer_result_artifact_digest_invalid")
        if str(result.get("artifact_set_digest") or "") != _canonical_digest(artifact_set):
            raise ValueError("codecompass_layer_result_artifact_set_digest_invalid")
        if str(result.get("status") or "") != "completed":
            failed = {**record, "state": "failed", "reason_code": str(result.get("reason_code") or "worker_failed")}
            self._dispatches.save(failed)
            return {"status": "failed", "task_id": task_id, "reason_code": failed["reason_code"]}
        publication = dict(self._publisher.publish(dispatch=record, result=result))
        published = {**record, "state": "published", "publication": publication}
        self._dispatches.save(published)
        return publication


class CodeCompassLayerService:
    def __init__(self, backend: CodeCompassLayerBackendPort | None = None) -> None:
        self._backend = backend or UnavailableCodeCompassLayerBackend()

    def list_profiles(self) -> list[str]: return self._backend.list_profiles()
    def show_head(self, profile_id: str) -> dict[str, Any] | None: return self._backend.show_head(profile_id)
    def diff(self, **kwargs: Any) -> dict[str, Any]: return self._backend.diff(**kwargs)
    def plan_update(self, **kwargs: Any) -> dict[str, Any]: return self._backend.plan_update(**kwargs)
    def apply_update(self, **kwargs: Any) -> dict[str, Any]: return self._backend.apply_update(**kwargs)
    def compact(self, **kwargs: Any) -> dict[str, Any]: return self._backend.compact(**kwargs)
    def admit_result(self, result: Mapping[str, Any]) -> dict[str, Any]: return self._backend.admit_result(result)


_layer_service = CodeCompassLayerService()


def get_codecompass_layer_service() -> CodeCompassLayerService:
    try:
        from flask import current_app

        configured = current_app.extensions.get("codecompass_layer_service")
        if isinstance(configured, CodeCompassLayerService):
            return configured
    except RuntimeError:
        pass
    return _layer_service
