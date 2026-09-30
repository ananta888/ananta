"""Execute one Hub-delegated Organization Category research task on a Worker."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.runtime_policy import build_trace_record
from agent.services._task_scoped_cli_invocation import invoke_cli_runner
from agent.services.organization_research_delegation_policy_service import (
    context_bundle_integrity_digest,
)
from agent.services.task_context_bundle_access_service import (
    ContextBundleTaskAccessError,
    TaskContextBundleAccessPort,
    get_task_context_bundle_access_service,
)
from agent.services.task_execution_policy_service import resolve_execution_policy
from worker.planning.organization_category_research_contracts import (
    OrganizationCategoryResearchExecutionError,
)
from worker.planning.organization_category_research_output import (
    has_dependency_cycle,
    validate_research_output,
)
from worker.planning.organization_category_research_prompts import (
    build_prompt,
    prompt_context,
)
from worker.planning.organization_category_research_prompts import (
    repair_prompt as build_repair_prompt,
)

PLANNING_RESEARCH_EXECUTE_COMMAND = (
    "__ANANTA_EXECUTE_ORGANIZATION_CATEGORY_RESEARCH__"
)
_SOURCE_REF = re.compile(r"^SRC_[0-9]{4}$")
_RUN_REF = re.compile(r"^RUN_[0-9]{4}$")


@dataclass(frozen=True, slots=True)
class _PreparedExecution:
    task_id: str
    parent_task_id: str
    worker_job_id: str
    context_bundle_id: str
    context_bundle_digest: str
    source_catalog_id: str
    source_catalog_hash: str
    allowed_source_refs: tuple[str, ...]
    allowed_run_refs: tuple[str, ...]
    backend: str
    model: str
    prompt: str


class OrganizationCategoryResearchTaskHandler:
    """Bounded Worker executor; it never creates or delegates another task."""

    _prompt_context = staticmethod(prompt_context)

    def __init__(
        self,
        *,
        cli_runner: Callable[..., Any],
        bundle_access: TaskContextBundleAccessPort | None = None,
        cli_invoker: Callable[..., Any] = invoke_cli_runner,
        finalizer: Callable[..., dict[str, Any]] | None = None,
        agent_config: Mapping[str, Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        output_validator: Callable[..., tuple[dict[str, Any] | None, list[str]]] = validate_research_output,
    ) -> None:
        self._cli_runner = cli_runner
        self._bundle_access = (
            bundle_access or get_task_context_bundle_access_service()
        )
        self._cli_invoker = cli_invoker
        self._finalizer = finalizer
        self._agent_config = dict(agent_config or {})
        self._clock = clock
        self._output_validator = output_validator

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        task = self._mapping(kwargs.get("task"))
        task_id = str(kwargs.get("tid") or task.get("id") or "").strip()
        try:
            prepared = self._prepare(task=task, task_id=task_id)
        except OrganizationCategoryResearchExecutionError as exc:
            return self._denied(exc)
        return {
            "status": "executable",
            "proposal_status": "executable",
            "reason": "assignment_bound_category_research_ready",
            "reason_code": "assignment_bound_category_research_ready",
            "command": PLANNING_RESEARCH_EXECUTE_COMMAND,
            "tool_calls": [],
            "backend": prepared.backend,
            "model": prepared.model,
            "routing": {
                "task_kind": "planning_research",
                "effective_backend": prepared.backend,
                "inference_provider": prepared.backend,
                "inference_model": prepared.model,
                "reason": "hub_bound_research_destination",
            },
            "source_catalog_id": prepared.source_catalog_id,
            "source_catalog_hash": prepared.source_catalog_hash,
            "context_bundle_id": prepared.context_bundle_id,
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        task = self._mapping(kwargs.get("task"))
        task_id = str(kwargs.get("tid") or task.get("id") or "").strip()
        request_data = kwargs.get("request_data")
        command = str(getattr(request_data, "command", None) or "").strip()
        if not command:
            command = str(
                self._mapping(task.get("last_proposal")).get("command") or ""
            ).strip()
        if command != PLANNING_RESEARCH_EXECUTE_COMMAND:
            return self._denied(
                OrganizationCategoryResearchExecutionError(
                    "category_research_execute_command_invalid"
                )
            )
        try:
            prepared = self._prepare(task=task, task_id=task_id)
        except OrganizationCategoryResearchExecutionError as exc:
            return self._denied(exc)

        execution_policy = resolve_execution_policy(
            request_data,
            agent_cfg=self._agent_config,
            source="organization_category_research_execute",
        )
        explicit_fields = set(
            getattr(request_data, "model_fields_set", set()) or set()
        )
        timeout = int(execution_policy.timeout_seconds)
        if "timeout" not in explicit_fields:
            timeout = max(timeout, 300)

        started_at = self._clock()
        attempts: list[dict[str, Any]] = []
        rc, raw_output, stderr, backend_used = self._invoke(
            prepared=prepared,
            prompt=prepared.prompt,
            timeout=timeout,
        )
        attempts.append(
            {
                "attempt": 1,
                "returncode": rc,
                "backend": backend_used,
                "repair": False,
            }
        )
        candidate = None
        issues: list[str] = []
        if rc == 0 and str(raw_output or "").strip():
            candidate, issues = self._validate_output(
                raw_output,
                prepared=prepared,
            )
        else:
            issues = ["category_research_cli_failed"]

        if candidate is None and rc == 0 and str(raw_output or "").strip():
            repair_prompt = build_repair_prompt(
                prompt=prepared.prompt,
                raw_output=raw_output,
                issues=issues,
            )
            repair_rc, repaired_output, repair_stderr, repair_backend = self._invoke(
                prepared=prepared,
                prompt=repair_prompt,
                timeout=timeout,
            )
            attempts.append(
                {
                    "attempt": 2,
                    "returncode": repair_rc,
                    "backend": repair_backend,
                    "repair": True,
                }
            )
            rc = repair_rc
            raw_output = repaired_output
            stderr = repair_stderr
            backend_used = repair_backend
            if rc == 0 and str(raw_output or "").strip():
                candidate, issues = self._validate_output(
                    raw_output,
                    prepared=prepared,
                )
            else:
                issues = ["category_research_cli_repair_failed"]

        duration_ms = max(0, int((self._clock() - started_at) * 1000))
        if candidate is None:
            public_error = {
                "reason_code": issues[0]
                if issues
                else "category_research_output_invalid",
                "issues": issues[:30],
                "stderr": str(stderr or "")[-1000:],
            }
            return self._finalize(
                task=task,
                prepared=prepared,
                execution_policy=execution_policy,
                status="failed",
                output=json.dumps(public_error, sort_keys=True),
                exit_code=rc if isinstance(rc, int) and rc != 0 else 1,
                failure_type="output_contract_error",
                duration_ms=duration_ms,
                attempts=attempts,
                backend_used=backend_used,
            )

        normalized_output = json.dumps(
            candidate,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        return self._finalize(
            task=task,
            prepared=prepared,
            execution_policy=execution_policy,
            status="completed",
            output=normalized_output,
            exit_code=0,
            failure_type="success",
            duration_ms=duration_ms,
            attempts=attempts,
            backend_used=backend_used,
        )

    def _prepare(self, *, task: Mapping[str, Any], task_id: str) -> _PreparedExecution:
        if not task_id or str(task.get("task_kind") or "") != "planning_research":
            self._fail("category_research_worker_task_invalid")
        worker_context = self._mapping(task.get("worker_execution_context"))
        admission = self._mapping(
            worker_context.get("hub_research_dispatch_admission")
        )
        source_policy = self._mapping(
            worker_context.get("source_context_policy")
        )
        source_manifest = self._mapping(
            worker_context.get("source_context_bundle_manifest")
        )
        destination = self._mapping(
            worker_context.get("research_destination_binding")
        )
        research_binding = self._mapping(
            worker_context.get("planning_research_binding")
        )
        parent_task_id = str(task.get("parent_task_id") or "").strip()
        worker_job_id = str(task.get("current_worker_job_id") or "").strip()
        origin_bundle_id = str(
            worker_context.get("origin_context_bundle_id") or ""
        ).strip()
        origin_digest = str(
            source_policy.get("context_bundle_digest") or ""
        ).strip()
        if (
            admission.get("schema") != "organization_research_worker_admission.v1"
            or source_policy.get("schema")
            != "organization_research_source_context_policy.v1"
            or destination.get("schema")
            != "organization_research_destination_binding.v1"
            or source_manifest.get("schema")
            != "organization_research_context_manifest.v1"
            or str(admission.get("parent_task_id") or "") != parent_task_id
            or str(admission.get("worker_job_id") or "") != worker_job_id
            or str(admission.get("origin_context_bundle_id") or "")
            != origin_bundle_id
            or str(source_policy.get("context_bundle_id") or "")
            != origin_bundle_id
            or str(source_manifest.get("id") or "") != origin_bundle_id
            or str(source_manifest.get("task_id") or "") != parent_task_id
            or str(source_policy.get("llm_scope") or "") != "local_only"
            or str(destination.get("llm_scope") or "") != "local_only"
            or str(destination.get("provider_location") or "")
            != "local_container"
            or str(admission.get("destination_binding_digest") or "")
            != str(destination.get("binding_digest") or "")
            or not origin_digest
        ):
            self._fail("category_research_worker_binding_invalid")
        if not task.get("callback_url") or not task.get("callback_token"):
            self._fail("category_research_worker_callback_missing")

        try:
            bundle = self._bundle_access.resolve_task_reference(
                task=task,
                task_id=task_id,
            )
        except ContextBundleTaskAccessError as exc:
            raise OrganizationCategoryResearchExecutionError(
                str(exc.reason_code)
            ) from exc
        if bundle is None:
            self._fail("category_research_context_bundle_missing")

        bundle_metadata = self._mapping(self._value(bundle, "bundle_metadata"))
        dispatch_metadata = self._mapping(
            bundle_metadata.get("hub_research_dispatch")
        )
        if (
            dispatch_metadata.get("schema")
            != "organization_research_delegated_context.v1"
            or str(dispatch_metadata.get("payload_digest") or "")
            != str(admission.get("payload_digest") or "")
            or str(dispatch_metadata.get("worker_job_id") or "")
            != worker_job_id
            or str(dispatch_metadata.get("origin_context_bundle_id") or "")
            != origin_bundle_id
            or str(dispatch_metadata.get("origin_context_bundle_digest") or "")
            != origin_digest
            or str(dispatch_metadata.get("destination_binding_digest") or "")
            != str(destination.get("binding_digest") or "")
        ):
            self._fail("category_research_context_lineage_invalid")

        original_metadata = {
            key: value
            for key, value in bundle_metadata.items()
            if key != "hub_research_dispatch"
        }
        reconstructed_digest = context_bundle_integrity_digest(
            SimpleNamespace(
                id=origin_bundle_id,
                retrieval_run_id=str(
                    source_manifest.get("retrieval_run_id") or ""
                ),
                task_id=parent_task_id,
                bundle_type=str(source_manifest.get("bundle_type") or ""),
                context_text=str(self._value(bundle, "context_text") or ""),
                chunks=list(self._value(bundle, "chunks") or []),
                token_estimate=int(
                    self._value(bundle, "token_estimate") or 0
                ),
                bundle_metadata=original_metadata,
            )
        )
        if reconstructed_digest != origin_digest:
            self._fail("category_research_context_digest_mismatch")

        source_catalog_id = str(
            research_binding.get("source_catalog_id") or ""
        ).strip()
        source_catalog_hash = str(
            research_binding.get("source_catalog_hash") or ""
        ).strip()
        if (
            not source_catalog_id
            or not source_catalog_hash
            or source_catalog_id != str(source_policy.get("source_catalog_id") or "")
            or source_catalog_hash
            != str(source_policy.get("source_catalog_hash") or "")
            or source_catalog_id != str(bundle_metadata.get("catalog_id") or "")
            or source_catalog_hash
            != str(bundle_metadata.get("catalog_hash") or "")
        ):
            self._fail("category_research_source_catalog_binding_invalid")

        allowed_source_refs = self._reference_tuple(
            worker_context.get("allowed_source_refs"),
            pattern=_SOURCE_REF,
            reason_code="category_research_source_allowlist_invalid",
        )
        allowed_run_refs = self._reference_tuple(
            worker_context.get("allowed_run_refs"),
            pattern=_RUN_REF,
            reason_code="category_research_run_allowlist_invalid",
        )
        if (
            set(allowed_source_refs)
            != {
                str(value)
                for value in list(
                    research_binding.get("allowed_source_refs") or []
                )
            }
            or set(allowed_run_refs)
            != {
                str(value)
                for value in list(
                    research_binding.get("allowed_run_refs") or []
                )
            }
        ):
            self._fail("category_research_assignment_allowlist_mismatch")
        chunks = [
            dict(value)
            for value in list(self._value(bundle, "chunks") or [])
            if isinstance(value, Mapping)
        ]
        chunk_refs = {
            str(
                value.get("source_id")
                or self._mapping(value.get("metadata")).get("source_id")
                or ""
            )
            for value in chunks
        }
        chunk_refs.discard("")
        catalog = self._mapping(research_binding.get("source_catalog"))
        catalog_refs = {
            str(self._mapping(value).get("source_id") or "")
            for value in list(catalog.get("sources") or [])
            if isinstance(value, Mapping)
        }
        catalog_refs.discard("")
        if (
            not chunks
            or chunk_refs != set(allowed_source_refs)
            or catalog_refs != set(allowed_source_refs)
        ):
            self._fail("category_research_source_projection_mismatch")

        backend = str(destination.get("provider_id") or "").strip().lower()
        model = str(destination.get("model_id") or "").strip()
        if backend not in {"codex", "opencode", "claude_code"} or not model:
            self._fail("category_research_bound_cli_invalid")
        context_text = str(self._value(bundle, "context_text") or "").strip()
        if not context_text:
            self._fail("category_research_context_empty")
        context_text = prompt_context(chunks)
        prompt = build_prompt(
            task=task,
            context_text=context_text,
            source_catalog=catalog,
            source_catalog_id=source_catalog_id,
            source_catalog_hash=source_catalog_hash,
            allowed_source_refs=allowed_source_refs,
            allowed_run_refs=allowed_run_refs,
            repository_revision=str(
                bundle_metadata.get("repository_revision") or ""
            ),
        )
        return _PreparedExecution(
            task_id=task_id,
            parent_task_id=parent_task_id,
            worker_job_id=worker_job_id,
            context_bundle_id=str(self._value(bundle, "id") or ""),
            context_bundle_digest=origin_digest,
            source_catalog_id=source_catalog_id,
            source_catalog_hash=source_catalog_hash,
            allowed_source_refs=allowed_source_refs,
            allowed_run_refs=allowed_run_refs,
            backend=backend,
            model=model,
            prompt=prompt,
        )

    def _invoke(
        self,
        *,
        prepared: _PreparedExecution,
        prompt: str,
        timeout: int,
    ) -> tuple[int, str, str, str]:
        result = self._cli_invoker(
            self._cli_runner,
            prompt=prompt,
            options=["--no-interaction"],
            timeout=max(1, min(int(timeout), 3600)),
            backend=prepared.backend,
            model=prepared.model,
            routing_policy={
                "mode": "hub_bound",
                "task_kind": "planning_research",
                "source_catalog_id": prepared.source_catalog_id,
                "opencode_tool_mode": "toolless",
                "opencode_context_token_limit": 32768,
                "opencode_output_token_limit": 4096,
            },
        )
        if not isinstance(result, tuple) or len(result) not in {3, 4}:
            return -1, "", "category_research_cli_result_invalid", prepared.backend
        rc, output, stderr = result[:3]
        backend_used = result[3] if len(result) == 4 else prepared.backend
        try:
            normalized_rc = int(rc)
        except (TypeError, ValueError):
            normalized_rc = -1
        return (
            normalized_rc,
            str(output or ""),
            str(stderr or ""),
            str(backend_used or prepared.backend),
        )

    def _validate_output(
        self,
        raw_output: str,
        *,
        prepared: _PreparedExecution,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        return self._output_validator(raw_output, prepared=prepared)

    def _finalize(
        self,
        *,
        task: Mapping[str, Any],
        prepared: _PreparedExecution,
        execution_policy: Any,
        status: str,
        output: str,
        exit_code: int,
        failure_type: str,
        duration_ms: int,
        attempts: list[dict[str, Any]],
        backend_used: str,
    ) -> dict[str, Any]:
        pipeline = new_pipeline_trace(
            pipeline="organization_category_research_execute",
            task_kind="planning_research",
            policy_version="organization_category_research_worker_v1",
            metadata={
                "task_id": prepared.task_id,
                "source_catalog_id": prepared.source_catalog_id,
                "worker_job_id": prepared.worker_job_id,
            },
        )
        append_stage(
            pipeline,
            name="assignment_binding",
            status="ok",
            metadata={
                "context_bundle_id": prepared.context_bundle_id,
                "source_count": len(prepared.allowed_source_refs),
                "run_ref_count": len(prepared.allowed_run_refs),
            },
        )
        append_stage(
            pipeline,
            name="bound_cli_execution",
            status="ok" if status == "completed" else "failed",
            metadata={
                "backend": backend_used,
                "model": prepared.model,
                "attempt_count": len(attempts),
            },
        )
        trace = build_trace_record(
            task_id=prepared.task_id,
            event_type="execution_result",
            task_kind="planning_research",
            backend=backend_used,
            requested_backend=prepared.backend,
            routing_reason="hub_bound_research_destination",
            policy_version="organization_category_research_worker_v1",
            metadata={
                "source": "organization_category_research_worker",
                "source_catalog_id": prepared.source_catalog_id,
                "source_catalog_hash": prepared.source_catalog_hash,
                "context_bundle_id": prepared.context_bundle_id,
            },
        )
        finalizer = self._finalizer
        if finalizer is None:
            from agent.services.service_registry import get_core_services

            finalizer = (
                get_core_services()
                .task_execution_service.finalize_task_execution_response
            )
        return finalizer(
            tid=prepared.task_id,
            task=dict(task),
            status=status,
            reason=(
                "Assignment-bound Category research completed"
                if status == "completed"
                else "Assignment-bound Category research failed"
            ),
            command=PLANNING_RESEARCH_EXECUTE_COMMAND,
            tool_calls=None,
            output=output,
            exit_code=exit_code,
            retries_used=max(0, len(attempts) - 1),
            retry_history=list(attempts),
            failure_type=failure_type,
            execution_duration_ms=duration_ms,
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            extra_history={
                "organization_category_research": {
                    "schema": "organization_category_research_execution.v1",
                    "source_catalog_id": prepared.source_catalog_id,
                    "source_catalog_hash": prepared.source_catalog_hash,
                    "allowed_source_refs": list(prepared.allowed_source_refs),
                    "allowed_run_refs": list(prepared.allowed_run_refs),
                    "context_bundle_id": prepared.context_bundle_id,
                    "context_bundle_digest": prepared.context_bundle_digest,
                    "worker_job_id": prepared.worker_job_id,
                    "backend": backend_used,
                    "model": prepared.model,
                    "attempts": list(attempts),
                }
            },
        )

    @staticmethod
    def _reference_tuple(
        values: Any,
        *,
        pattern: re.Pattern[str],
        reason_code: str,
    ) -> tuple[str, ...]:
        if not isinstance(values, list) or not values:
            raise OrganizationCategoryResearchExecutionError(reason_code)
        normalized = tuple(str(value or "").strip() for value in values)
        if (
            len(set(normalized)) != len(normalized)
            or any(pattern.fullmatch(value) is None for value in normalized)
        ):
            raise OrganizationCategoryResearchExecutionError(reason_code)
        return normalized

    @staticmethod
    def _has_dependency_cycle(dependencies: Mapping[str, set[str]]) -> bool:
        return has_dependency_cycle(dependencies)

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _value(value: Any, name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

    @staticmethod
    def _denied(exc: OrganizationCategoryResearchExecutionError) -> dict[str, Any]:
        return {
            "status": "denied",
            "reason": exc.reason_code,
            "reason_code": exc.reason_code,
            "details": list(exc.details),
        }

    @staticmethod
    def _fail(reason_code: str) -> None:
        raise OrganizationCategoryResearchExecutionError(reason_code)


__all__ = [
    "OrganizationCategoryResearchExecutionError",
    "OrganizationCategoryResearchTaskHandler",
    "PLANNING_RESEARCH_EXECUTE_COMMAND",
]
