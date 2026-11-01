from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from agent.cli_backends.sgpt import run_sgpt_command  # noqa: F401  (test monkeypatch seam)
from agent.common.utils.structured_action_utils import (
    extract_structured_action_fields,
    locally_repair_structured_action_output,
    normalize_structured_action_payload,
    parse_structured_action_payload,
    sanitize_structured_output_text,
)
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.live_terminal_session_service import get_live_terminal_session_service
from agent.services.planning_context_compactor_service import get_planning_context_compactor_service
from agent.services.propose_policy_service import get_propose_policy_service
from agent.services.repository_registry import get_repository_registry
from agent.services.research_context_bridge_service import get_research_context_bridge_service
from agent.services.service_registry import get_core_services
from agent.services.task_runtime_service import update_local_task_status

if TYPE_CHECKING:
    from agent.services.domain_action_router import DomainActionRouter


def _build_workspace_state_sync_record(*, task, materialization_manifest, workspace_artifact_refs, git_pushed):
    from agent.services.task_scoped.workspace_sync import build_workspace_state_sync_record as _impl

    return _impl(
        task=task,
        materialization_manifest=materialization_manifest,
        workspace_artifact_refs=workspace_artifact_refs,
        git_pushed=git_pushed,
    )


@dataclass(frozen=True)
class TaskScopedRouteResponse:
    data: dict
    status: str = "success"
    message: str | None = None
    code: int = 200


class TaskScopedExecutionService:
    """Thin orchestrator delegating to ``_task_scoped_*`` helper modules.

    ``forwarding_dependencies`` is the explicit port bundle of the forwarding
    and step modules; ``None`` uses the current application's installed bundle
    (see ``install_task_scoped_forwarding_dependencies``) or production.
    """

    def __init__(self, *, forwarding_dependencies: Any | None = None) -> None:
        self._forwarding_dependencies = forwarding_dependencies

    @property
    def forwarding_dependencies(self) -> Any:
        if self._forwarding_dependencies is not None:
            return self._forwarding_dependencies
        from agent.services.task_scoped.forwarding_dependencies import (
            current_task_scoped_forwarding_dependencies,
        )

        return current_task_scoped_forwarding_dependencies()

    # --- cluster: config_policy (resolvers, bounded_*, normalize) ---
    @staticmethod
    def _allow_synthetic_llm_profile_fallback() -> bool:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.allow_synthetic_llm_profile_fallback.
        from agent.services.task_scoped.config_policy import (
            allow_synthetic_llm_profile_fallback,
        )

        return allow_synthetic_llm_profile_fallback()

    @staticmethod
    def _is_interactive_terminal_session(session_payload: dict | None) -> bool:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.is_interactive_terminal_session.
        from agent.services.task_scoped.config_policy import (
            is_interactive_terminal_session,
        )

        return is_interactive_terminal_session(session_payload)

    @staticmethod
    def _normalize_temperature(value: float | int | str | None) -> float | None:
        # Delegating wrapper for SPLIT-001b. Implementation lives in
        # agent.services.task_scoped.config_policy.normalize_temperature.
        from agent.services.task_scoped.config_policy import normalize_temperature

        return normalize_temperature(value)

    @staticmethod
    def _default_model(agent_cfg: dict) -> str | None:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.default_model.
        from agent.services.task_scoped.config_policy import default_model

        return default_model(agent_cfg)

    @classmethod
    def _resolve_requested_model(cls, *, agent_cfg: dict, requested_model: str | None) -> str | None:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.resolve_requested_model.
        from agent.services.task_scoped.config_policy import (
            resolve_requested_model,
        )

        return resolve_requested_model(agent_cfg=agent_cfg, requested_model=requested_model)

    @staticmethod
    def _resolve_task_propose_timeout(agent_cfg: dict, task_kind: str) -> int:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.resolve_task_propose_timeout.
        from agent.services.task_scoped.config_policy import (
            resolve_task_propose_timeout,
        )

        return resolve_task_propose_timeout(agent_cfg, task_kind)

    @staticmethod
    def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
        # Delegating wrapper for SPLIT-001b. Implementation lives in
        # agent.services.task_scoped.config_policy.bounded_int.
        from agent.services.task_scoped.config_policy import bounded_int

        return bounded_int(value, default=default, minimum=minimum, maximum=maximum)

    @staticmethod
    def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
        # Delegating wrapper for SPLIT-001b. Implementation lives in
        # agent.services.task_scoped.config_policy.bounded_float.
        from agent.services.task_scoped.config_policy import bounded_float

        return bounded_float(value, default=default, minimum=minimum, maximum=maximum)

    # --- cluster: workspace_runtime (command rewrite) ---
    @staticmethod
    def _rewrite_runtime_command_for_workspace_tools(
        *, command: str | None, workspace_dir: str | None
    ) -> tuple[str | None, dict | None]:
        # SPLIT-001v: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.workspace_runtime.rewrite_runtime_command_for_workspace_tools.
        from agent.services.task_scoped.workspace_runtime import (
            rewrite_runtime_command_for_workspace_tools,
        )

        return rewrite_runtime_command_for_workspace_tools(
            command=command,
            workspace_dir=workspace_dir,
        )

    @classmethod
    def _resolve_worker_semantic_output_correction_policy(cls, agent_cfg: dict | None) -> dict:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.resolve_worker_semantic_output_correction_policy
        from agent.services.task_scoped.config_policy import (
            resolve_worker_semantic_output_correction_policy,
        )

        return resolve_worker_semantic_output_correction_policy(agent_cfg)

    @classmethod
    def _resolve_interactive_context_profile(cls, agent_cfg: dict | None, *, retry: bool = False) -> dict:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.resolve_interactive_context_profile
        from agent.services.task_scoped.config_policy import (
            resolve_interactive_context_profile,
        )

        return resolve_interactive_context_profile(agent_cfg, retry=retry)

    @classmethod
    def _compact_research_context(
        cls,
        research_context: dict | None,
        *,
        profile: dict | None,
    ) -> dict | None:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.compact_research_context
        from agent.services.task_scoped.config_policy import compact_research_context

        return compact_research_context(research_context, profile=profile)

    @staticmethod
    def _interactive_timeout_like_failure(*, rc: int, output: str, stderr: str) -> bool:
        # SPLIT-001q: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.repair.is_interactive_timeout_like_failure.
        from agent.services.task_scoped.repair import (
            is_interactive_timeout_like_failure,
        )

        return is_interactive_timeout_like_failure(rc=rc, output=output, stderr=stderr)

    @classmethod
    def _resolve_interactive_propose_timeout(cls, agent_cfg: dict | None, *, fallback: int) -> int:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.resolve_interactive_propose_timeout
        from agent.services.task_scoped.config_policy import (
            resolve_interactive_propose_timeout,
        )

        return resolve_interactive_propose_timeout(agent_cfg, fallback=fallback)

    @classmethod
    def _resolve_interactive_retry_timeout(cls, agent_cfg: dict | None, *, fallback: int) -> int:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.resolve_interactive_retry_timeout
        from agent.services.task_scoped.config_policy import (
            resolve_interactive_retry_timeout,
        )

        return resolve_interactive_retry_timeout(agent_cfg, fallback=fallback)

    @staticmethod
    def _extract_retrieval_trace_link(context_payload: dict | None) -> dict[str, str | None]:
        # SPLIT-001j: delegating wrapper.
        from agent.services.task_scoped.citation import extract_retrieval_trace_link

        return extract_retrieval_trace_link(context_payload)

    # --- cluster: citation_grounded (source catalog, citation contract, flow metrics) ---
    def _build_source_catalog_from_execution_context(
        self,
        *,
        tid: str,
        task: dict,
        llm_scope: str = "local_only",
    ) -> dict | None:
        # SPLIT-001j: delegating wrapper.
        from agent.services.task_scoped.citation import build_source_catalog_from_execution_context

        return build_source_catalog_from_execution_context(tid=tid, task=task, llm_scope=llm_scope)

    @staticmethod
    def _render_citation_contract_prompt(
        source_catalog: dict | None,
        *,
        run_evidence_context: dict | None = None,
    ) -> str:
        # SPLIT-001j: delegating wrapper.
        from agent.services.task_scoped.citation import render_citation_contract_prompt

        return render_citation_contract_prompt(
            source_catalog,
            run_evidence_context=run_evidence_context,
        )

    @staticmethod
    def _extract_grounded_answer_payload(output: str | None) -> dict | None:
        # SPLIT-001j: delegating wrapper.
        from agent.services.task_scoped.citation import extract_grounded_answer_payload

        return extract_grounded_answer_payload(output)

    @staticmethod
    def _invoke_cli_runner(cli_runner: Callable, **cli_kwargs):
        # SPLIT-001r: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.cli_invocation.invoke_cli_runner.
        from agent.services.task_scoped.cli_invocation import invoke_cli_runner

        return invoke_cli_runner(cli_runner, **cli_kwargs)

    # --- cluster: propose + execute (public API) + task_aux ---
    def propose_task_step(
        self,
        tid: str,
        request_data,
        *,
        cli_runner: Callable,
        forwarder: Callable,
        tool_definitions_resolver: Callable,
    ) -> TaskScopedRouteResponse:
        from agent.services.task_scoped.step_orchestrator import run_propose_step

        return run_propose_step(
            self,
            tid,
            request_data,
            cli_runner=cli_runner,
            forwarder=forwarder,
            tool_definitions_resolver=tool_definitions_resolver,
            step_ports=self.forwarding_dependencies.steps,
        )

    def execute_task_step(
        self,
        tid: str,
        request_data,
        *,
        forwarder: Callable,
        cli_runner: Callable | None = None,
        tool_definitions_resolver: Callable | None = None,
    ) -> TaskScopedRouteResponse:
        from agent.services.task_scoped.step_orchestrator import run_execute_step

        return run_execute_step(
            self,
            tid,
            request_data,
            forwarder=forwarder,
            cli_runner=cli_runner,
            tool_definitions_resolver=tool_definitions_resolver,
            step_ports=self.forwarding_dependencies.steps,
        )

    @staticmethod
    # --- cluster: domain_action (router, comparison, single-propose, goal-artifact-output) ---
    def _build_domain_action_router() -> DomainActionRouter:
        # SPLIT-001e-1: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.domain_action.build_domain_action_router.
        # We import through ``self.__class__.__module__`` indirectly: tests
        # that monkeypatch ``TaskScopedExecutionService._build_domain_action_router``
        # continue to work because the patched attribute shadows this method
        # at call time, and the patched return value is returned.
        from agent.services.task_scoped.domain_action import build_domain_action_router

        return build_domain_action_router()

    def _register_goal_artifact_outputs(self, *, task: dict, tid: str, artifact_refs: list[dict]) -> list[dict]:
        # SPLIT-001e-1: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.domain_action.register_goal_artifact_outputs
        from agent.services.task_scoped.domain_action import register_goal_artifact_outputs

        return register_goal_artifact_outputs(
            task=task,
            tid=tid,
            artifact_refs=artifact_refs,
            get_system_prompt_for_task=self._get_system_prompt_for_task,
        )

    @staticmethod
    def _resolve_domain_action_payload(*, task: dict, command: str | None) -> dict:
        # SPLIT-001e-1: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.domain_action.resolve_domain_action_payload
        from agent.services.task_scoped.domain_action import resolve_domain_action_payload

        return resolve_domain_action_payload(task=task, command=command)

    def _execute_domain_action(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        command: str | None,
        reason: str,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        # SPLIT-001e-1: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.domain_action.execute_domain_action
        from agent.services.task_scoped.domain_action import execute_domain_action

        return execute_domain_action(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            command=command,
            reason=reason,
            execution_policy=execution_policy,
        )

    def _propose_single_task_step(
        self,
        *,
        tid: str,
        task: dict,
        request_data,
        base_prompt: str,
        research_context: dict | None,
        cli_runner: Callable,
        cfg: dict,
        tool_definitions_resolver: Callable,
        allow_legacy_path: bool = False,
    ) -> "TaskScopedRouteResponse":
        from agent.services.task_scoped.propose_single import propose_single_task_step

        return propose_single_task_step(
            tid=tid,
            task=task,
            request_data=request_data,
            base_prompt=base_prompt,
            research_context=research_context,
            cli_runner=cli_runner,
            cfg=cfg,
            tool_definitions_resolver=tool_definitions_resolver,
            allow_legacy_path=allow_legacy_path,
            resolve_requested_model=self._resolve_requested_model,
            invoke_cli_runner=self._invoke_cli_runner,
            coalesce_cli_output=self._coalesce_cli_output,
        )

    # --- cluster: forwarding_hub (remote forward, persist proposal/execution) ---
    # SPLIT-001f: all four methods delegate to agent.services.task_scoped.forwarding.
    def _forward_task_request_if_remote(
        self,
        *,
        tid: str,
        task: dict,
        endpoint: str,
        payload: dict,
        forwarder: Callable,
        on_success: Callable[[dict, dict], None],
    ) -> TaskScopedRouteResponse | None:
        from agent.services.task_scoped.forwarding import forward_task_request_if_remote

        return forward_task_request_if_remote(
            tid=tid,
            task=task,
            endpoint=endpoint,
            payload=payload,
            forwarder=forwarder,
            on_success=on_success,
            dependencies=self.forwarding_dependencies,
        )

    def _persist_forwarded_proposal(self, response: dict, task: dict, request_payload: dict | None = None) -> None:
        from agent.services.task_scoped.forwarding import persist_forwarded_proposal

        return persist_forwarded_proposal(
            response,
            task,
            request_payload,
            allow_synthetic_llm_profile_fallback=self._allow_synthetic_llm_profile_fallback,
            hub_state=self.forwarding_dependencies.hub_state,
        )

    def _persist_forwarded_execution(
        self,
        *,
        tid: str,
        response: dict,
        task: dict,
        request_data,
        transport_deadline=None,
    ) -> None:
        from agent.services.task_scoped.forwarding import persist_forwarded_execution

        return persist_forwarded_execution(
            tid=tid,
            response=response,
            task=task,
            request_data=request_data,
            transport_deadline=transport_deadline,
            dependencies=self.forwarding_dependencies,
        )

    # ── HF-T019: Hermes propose path ─────────────────────────────────────────

    # --- cluster: adapters (hermes, handler) ---
    # SPLIT-001g: all five methods delegate to agent.services.task_scoped.adapters.
    def _try_hermes_propose(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        research_context: object,
        cfg: dict,
    ) -> TaskScopedRouteResponse | None:
        from agent.services.task_scoped.adapters import try_hermes_propose

        return try_hermes_propose(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            research_context=research_context,
            cfg=cfg,
        )

    def _try_handler_propose(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        base_prompt: str,
        cli_runner: Callable,
        forwarder: Callable,
        tool_definitions_resolver: Callable,
    ) -> TaskScopedRouteResponse | None:
        from agent.services.task_scoped.adapters import try_handler_propose

        return try_handler_propose(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            base_prompt=base_prompt,
            cli_runner=cli_runner,
            forwarder=forwarder,
            tool_definitions_resolver=tool_definitions_resolver,
            service=self,
            build_review_state=self._build_review_state,
        )

    def _require_task(self, tid: str) -> dict:
        # SPLIT-001s: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.task_lookup.require_task.
        from agent.services.task_scoped.task_lookup import require_task

        return require_task(tid)

    def _maybe_sync_task_from_hub(self, tid: str) -> dict | None:
        # SPLIT-001s: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.task_lookup.maybe_sync_task_from_hub.
        from agent.services.task_scoped.task_lookup import maybe_sync_task_from_hub

        return maybe_sync_task_from_hub(tid)

    # --- cluster: runtime (cli backend, opencode session, interactive terminal, research) ---
    def _resolve_cli_backend(
        self,
        task_kind: str,
        requested_backend: str = "auto",
        agent_cfg: dict | None = None,
        required_capabilities: list[str] | None = None,
    ) -> tuple[str, str]:
        # SPLIT-001t: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.runtime.resolve_task_cli_backend.
        from agent.services.task_scoped.runtime import resolve_task_cli_backend

        return resolve_task_cli_backend(
            task_kind=task_kind,
            requested_backend=requested_backend,
            agent_cfg=agent_cfg,
            required_capabilities=required_capabilities,
        )

    @staticmethod
    def _coalesce_cli_output(stdout: str | None, stderr: str | None) -> tuple[str, str]:
        # SPLIT-001u: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.cli_invocation.coalesce_cli_output.
        from agent.services.task_scoped.cli_invocation import coalesce_cli_output

        return coalesce_cli_output(stdout, stderr)

    @staticmethod
    def _normalize_tool_calls(tool_calls: object) -> list[dict] | None:
        # SPLIT-001u: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.cli_invocation.normalize_tool_calls.
        from agent.services.task_scoped.cli_invocation import normalize_tool_calls

        return normalize_tool_calls(tool_calls)

    @classmethod
    def _locally_repair_structured_action_output(cls, raw_text: str) -> str | None:
        return locally_repair_structured_action_output(raw_text)

    @classmethod
    def _extract_structured_action_fields(cls, raw_text: str) -> tuple[str | None, list[dict] | None]:
        return extract_structured_action_fields(raw_text)

    def _repair_task_proposal(
        self,
        *,
        cli_runner: Callable,
        prompt: str,
        bad_output: str,
        validation_error: str,
        timeout: int,
        task_kind: str,
        policy_version: str,
        cfg: dict,
        primary_backend: str,
        primary_model: str | None,
        primary_temperature: float | None = None,
        research_context: dict | None = None,
        session: dict | None = None,
        workdir: str | None = None,
    ) -> dict | None:
        from agent.services.task_scoped.repair import repair_task_proposal

        return repair_task_proposal(
            cli_runner=cli_runner,
            prompt=prompt,
            bad_output=bad_output,
            validation_error=validation_error,
            timeout=timeout,
            task_kind=task_kind,
            policy_version=policy_version,
            cfg=cfg,
            primary_backend=primary_backend,
            primary_model=primary_model,
            primary_temperature=primary_temperature,
            research_context=research_context,
            session=session,
            workdir=workdir,
            invoke_cli_runner=self._invoke_cli_runner,
            coalesce_cli_output=self._coalesce_cli_output,
            normalize_temperature=self._normalize_temperature,
        )

    @staticmethod
    def _estimate_tokens(value: str | None) -> int:
        from agent.services.task_scoped.repair import estimate_tokens

        return estimate_tokens(value)

    def _attempt_repaired_execute_after_meta_block(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        command: str | None,
        execution_output: str | None,
        execution_policy,
        agent_cfg: dict,
        cli_runner: Callable,
        tool_definitions_resolver: Callable | None,
        pipeline: dict,
        workspace_dir: str,
        exec_started_at: float | None,
    ) -> dict | None:
        from agent.services.task_scoped.repair import attempt_repaired_execute_after_meta_block

        return attempt_repaired_execute_after_meta_block(
            tid=tid,
            task=task,
            task_kind=task_kind,
            command=command,
            execution_output=execution_output,
            execution_policy=execution_policy,
            agent_cfg=agent_cfg,
            cli_runner=cli_runner,
            tool_definitions_resolver=tool_definitions_resolver,
            pipeline=pipeline,
            workspace_dir=workspace_dir,
            exec_started_at=exec_started_at,
            build_task_propose_prompt=self._build_task_propose_prompt,
            resolve_task_propose_timeout=self._resolve_task_propose_timeout,
            resolve_requested_model=self._resolve_requested_model,
            normalize_temperature=self._normalize_temperature,
            prepare_task_cli_session=self._prepare_task_cli_session,
            invoke_cli_runner=self._invoke_cli_runner,
            coalesce_cli_output=self._coalesce_cli_output,
        )

    def _build_review_state(
        self,
        agent_cfg: dict,
        backend: str,
        task_kind: str,
        *,
        command: str | None,
        tool_calls: list[dict] | None,
    ) -> dict:
        from agent.services.task_scoped.runtime import build_review_state

        return build_review_state(agent_cfg, backend, task_kind, command=command, tool_calls=tool_calls)

    @staticmethod
    def _cli_session_policy(agent_cfg: dict | None) -> dict:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.resolve_cli_session_policy
        from agent.services.task_scoped.config_policy import resolve_cli_session_policy

        return resolve_cli_session_policy(agent_cfg)

    @staticmethod
    def _resolve_opencode_execution_mode(agent_cfg: dict | None) -> str:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.resolve_opencode_execution_mode
        from agent.services.task_scoped.config_policy import (
            resolve_opencode_execution_mode,
        )

        return resolve_opencode_execution_mode(agent_cfg)

    @staticmethod
    def _resolve_opencode_interactive_launch_mode(agent_cfg: dict | None) -> str:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.resolve_opencode_interactive_launch_mode
        from agent.services.task_scoped.config_policy import (
            resolve_opencode_interactive_launch_mode,
        )

        return resolve_opencode_interactive_launch_mode(agent_cfg)

    @staticmethod
    def _native_worker_runtime_enabled(agent_cfg: dict | None) -> bool:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.native_worker_runtime_enabled
        from agent.services.task_scoped.config_policy import (
            native_worker_runtime_enabled,
        )

        return native_worker_runtime_enabled(agent_cfg)

    def _should_use_native_worker_runtime(
        self, *, proposal_meta: dict | None, agent_cfg: dict | None, command: str | None
    ) -> bool:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.should_use_native_worker_runtime
        from agent.services.task_scoped.config_policy import (
            should_use_native_worker_runtime,
        )

        return should_use_native_worker_runtime(
            proposal_meta=proposal_meta,
            agent_cfg=agent_cfg,
            command=command,
        )

    def _resolve_task_session_scope(self, *, tid: str, task: dict, policy: dict) -> tuple[str, str, str | None]:
        from agent.services.task_scoped.runtime import resolve_task_session_scope

        return resolve_task_session_scope(tid=tid, task=task, policy=policy)

    @staticmethod
    def _has_native_opencode_runtime(session_payload: dict | None) -> bool:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services.task_scoped.config_policy.has_native_opencode_runtime
        from agent.services.task_scoped.config_policy import (
            has_native_opencode_runtime,
        )

        return has_native_opencode_runtime(session_payload)

    def _prepare_task_cli_session(
        self,
        *,
        tid: str,
        task: dict,
        backend: str,
        model: str | None,
        agent_cfg: dict | None,
    ) -> dict | None:
        from agent.services.task_scoped.runtime import prepare_task_cli_session

        return prepare_task_cli_session(tid=tid, task=task, backend=backend, model=model, agent_cfg=agent_cfg)

    def _build_task_propose_prompt(
        self,
        *,
        tid: str,
        task: dict,
        base_prompt: str,
        tool_definitions_resolver: Callable,
        research_context: dict | None = None,
        interactive_terminal: bool = False,
        context_profile: dict | None = None,
    ) -> tuple[str, dict]:
        from agent.services.task_scoped.runtime import build_task_propose_prompt

        return build_task_propose_prompt(
            tid=tid,
            task=task,
            base_prompt=base_prompt,
            tool_definitions_resolver=tool_definitions_resolver,
            research_context=research_context,
            interactive_terminal=interactive_terminal,
            context_profile=context_profile,
        )

    def _get_system_prompt_for_task(self, tid: str) -> str | None:
        from agent.services.task_scoped.runtime import get_system_prompt_for_task

        return get_system_prompt_for_task(tid)

    @staticmethod
    def _routing_dimensions(
        *,
        backend_used: str,
        model: str | None,
        temperature: float | None = None,
        requested_backend: str = "auto",
        agent_cfg: dict | None = None,
        worker_profile: str | None = None,
        profile_source: str | None = None,
    ) -> dict:
        from agent.services.task_scoped.runtime import routing_dimensions

        return routing_dimensions(
            backend_used=backend_used,
            model=model,
            temperature=temperature,
            requested_backend=requested_backend,
            agent_cfg=agent_cfg,
            worker_profile=worker_profile,
            profile_source=profile_source,
        )

    @staticmethod
    def _terminal_parent_goal_guard(*, tid: str, task: dict, phase: str) -> TaskScopedRouteResponse | None:
        from agent.services.task_scoped.runtime import terminal_parent_goal_guard

        return terminal_parent_goal_guard(tid=tid, task=task, phase=phase)


task_scoped_execution_service = TaskScopedExecutionService()


def get_task_scoped_execution_service() -> TaskScopedExecutionService:
    return task_scoped_execution_service
