from __future__ import annotations

import concurrent.futures
import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from flask import current_app, g, has_app_context, has_request_context

from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import TaskConflictError, TaskNotFoundError, WorkerForwardingError
from agent.common.sgpt import SUPPORTED_CLI_BACKENDS, resolve_codex_runtime_config
from agent.common.utils.structured_action_utils import (
    extract_structured_action_fields,
    locally_repair_structured_action_output,
    normalize_structured_action_payload,
    parse_structured_action_payload,
    sanitize_structured_output_text,
)
from agent.config import settings
from agent.model_selection import normalize_legacy_model_name
from agent.models import TaskStepExecuteRequest
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_backend import is_research_backend, normalize_research_artifact
from agent.routes.tasks.orchestration_policy import derive_required_capabilities, derive_research_specialization
from agent.runtime_policy import (
    build_trace_record,
    normalize_task_kind,
    resolve_cli_backend,
    review_policy,
    runtime_routing_config,
)
from agent.security_risk import (
    classify_command_risk,
    classify_tool_calls_risk,
    has_file_access_signal,
    has_terminal_signal,
    max_risk_level,
)
from agent.services.cli_session_service import get_cli_session_service
from agent.services.context_manager_service import get_context_manager_service
from agent.services.live_terminal_session_service import get_live_terminal_session_service
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.bridge_adapter_registry import BridgeAdapterRegistry
from agent.services.capability_registry import CapabilityRegistry
from agent.services.domain_action_router import DomainActionRouter
from agent.services.domain_policy_loader import DomainPolicyLoader
from agent.services.domain_policy_service import DomainPolicyService
from agent.services.domain_registry import DomainRegistry
from agent.services.native_worker_runtime_service import get_native_worker_runtime_service
from agent.services.repository_registry import get_repository_registry
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.research_context_bridge_service import get_research_context_bridge_service
from agent.services.service_registry import get_core_services
from agent.services.task_execution_service import LocalExecutionResult
from agent.services.task_execution_policy_service import normalize_allowed_tools, resolve_execution_policy
from agent.services.task_handler_registry import get_task_handler_registry
from agent.services.execution_improvement_loop_service import get_execution_improvement_loop_service
from agent.services.planning_context_compactor_service import get_planning_context_compactor_service
from agent.services.product_event_service import record_product_event
from agent.services.worker_execution_profile_service import (
    normalize_worker_execution_profile,
    resolve_worker_execution_profile,
)
from agent.services.task_runtime_service import (
    apply_artifact_first_completion,
    get_local_task_status,
    update_local_task_status,
)
from agent.services.task_template_resolution import resolve_task_role_template
from agent.services.verification_service import get_verification_service
from agent.llm_integration import build_llm_call_profile_entry, normalize_llm_call_profile_entry
from agent.services.worker_workspace_service import get_worker_workspace_service
from agent.services.propose_policy_service import get_propose_policy_service
from agent.utils import _extract_reason, _log_terminal_entry

_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


def _build_workspace_state_sync_record(
    *,
    task: dict,
    materialization_manifest: object,
    workspace_artifact_refs: list,
    git_pushed: bool,
) -> dict:
    try:
        from agent.services.workspace_state_sync_policy import WorkspaceStateSyncPolicy
        policy = WorkspaceStateSyncPolicy.resolve(task)
        input_artifacts = [
            {"artifact_id": r.get("artifact_id"), "path": r.get("workspace_relative_path")}
            for r in (materialization_manifest or [])
            if isinstance(r, dict)
        ]
        output_artifacts = [
            {"artifact_id": r.get("artifact_id"), "path": r.get("workspace_relative_path")}
            for r in (workspace_artifact_refs or [])
            if isinstance(r, dict) and r.get("kind") == "workspace_file"
        ]
        return {
            "sync_mode": policy.sync_mode,
            "source_of_truth": policy.source_of_truth,
            "input_artifacts": input_artifacts,
            "output_artifacts": output_artifacts,
            "git_pushed": git_pushed,
        }
    except Exception:
        return {"sync_mode": "none", "source_of_truth": "task_local", "input_artifacts": [], "output_artifacts": [], "git_pushed": git_pushed}


def build_hermes_context_blocks(
    *,
    task: dict,
    request_data: object,
    research_context: object,
) -> list:
    """Build ContextBlock list from task + research context for HermesAdapter. HF-T020."""
    from worker.core.context_resolver import ContextBlock, ContextSensitivity

    blocks: list[ContextBlock] = []

    # Task description / prompt (P0 — never dropped)
    task_description = str(
        getattr(request_data, "prompt", None)
        or (task or {}).get("description")
        or ""
    ).strip()
    if task_description:
        blocks.append(ContextBlock(
            source_type="task_description",
            origin_id=str((task or {}).get("id") or "task"),
            provenance="task_scoped_execution_service:task_description",
            sensitivity=ContextSensitivity.internal,
            content=task_description,
            priority=0,
        ))

    # Research context prompt section
    rc = research_context if isinstance(research_context, dict) else {}
    prompt_section = str(rc.get("prompt_section") or "").strip()
    if prompt_section:
        blocks.append(ContextBlock(
            source_type="research_context",
            origin_id="research_context:prompt_section",
            provenance="task_scoped_execution_service:research_context",
            sensitivity=ContextSensitivity.internal,
            content=prompt_section,
            priority=10,
        ))

    # Additional context from request_data.context_blocks if present
    raw_blocks = getattr(request_data, "context_blocks", None) or []
    for idx, raw in enumerate(raw_blocks if isinstance(raw_blocks, list) else []):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        sensitivity_raw = str(raw.get("sensitivity") or ContextSensitivity.internal.value)
        try:
            sensitivity = ContextSensitivity(sensitivity_raw)
        except ValueError:
            sensitivity = ContextSensitivity.internal
        blocks.append(ContextBlock(
            source_type=str(raw.get("source_type") or "external_context"),
            origin_id=str(raw.get("origin_id") or f"context_block_{idx}"),
            provenance="task_scoped_execution_service:request_context_blocks",
            sensitivity=sensitivity,
            content=content,
            priority=int(raw.get("priority") or 50),
        ))

    return blocks


@dataclass(frozen=True)
class TaskScopedRouteResponse:
    data: dict
    status: str = "success"
    message: str | None = None
    code: int = 200


class TaskScopedExecutionService:
    """Owns task-scoped proposal/execution orchestration so routes stay thin."""

    @staticmethod
    def _allow_synthetic_llm_profile_fallback() -> bool:
        if not has_app_context():
            return False
        cfg = (current_app.config.get("AGENT_CONFIG", {}) or {})
        policy = dict(cfg.get("llm_profile_policy") or {})
        return bool(policy.get("allow_synthetic_fallback", False))

    @staticmethod
    def _is_interactive_terminal_session(session_payload: dict | None) -> bool:
        metadata = (session_payload or {}).get("metadata") if isinstance((session_payload or {}).get("metadata"), dict) else {}
        return str(metadata.get("opencode_execution_mode") or "").strip().lower() == "interactive_terminal"

    @staticmethod
    def _normalize_temperature(value: float | int | str | None) -> float | None:
        if value is None:
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        if normalized < 0.0:
            normalized = 0.0
        if normalized > 2.0:
            normalized = 2.0
        return normalized

    @staticmethod
    def _default_model(agent_cfg: dict) -> str | None:
        provider = str(agent_cfg.get("default_provider") or agent_cfg.get("provider") or "").strip().lower() or None
        return normalize_legacy_model_name(
            str(agent_cfg.get("default_model") or agent_cfg.get("model") or "").strip() or None,
            provider=provider,
        )

    @classmethod
    def _resolve_requested_model(cls, *, agent_cfg: dict, requested_model: str | None) -> str | None:
        provider = str(agent_cfg.get("default_provider") or agent_cfg.get("provider") or "").strip().lower() or None
        resolved = str(requested_model or "").strip() or cls._default_model(agent_cfg)
        return normalize_legacy_model_name(resolved, provider=provider)

    @staticmethod
    def _resolve_task_propose_timeout(agent_cfg: dict, task_kind: str) -> int:
        task_kind_policies = agent_cfg.get("task_kind_execution_policies") if isinstance(agent_cfg.get("task_kind_execution_policies"), dict) else {}
        task_kind_cfg = task_kind_policies.get(task_kind) if isinstance(task_kind_policies.get(task_kind), dict) else {}
        general_timeout = int(agent_cfg.get("command_timeout", 60) or 60)
        kind_timeout = int(task_kind_cfg.get("command_timeout") or 0)
        proposal_timeout = int(agent_cfg.get("task_propose_timeout_seconds") or 0)
        return max(60, general_timeout, kind_timeout, proposal_timeout)

    @staticmethod
    def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value) if value is not None else default
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value) if value is not None else default
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _rewrite_runtime_command_for_workspace_tools(*, command: str | None, workspace_dir: str | None) -> tuple[str | None, dict | None]:
        command_text = str(command or "").strip()
        workspace = str(workspace_dir or "").strip()
        if not command_text or not workspace:
            return command, None
        if "uvicorn" not in command_text:
            return command, None

        venv_uvicorn = Path(workspace) / ".venv" / "bin" / "uvicorn"
        if venv_uvicorn.exists():
            # Replace bare uvicorn token only, keep shell operators/arguments unchanged.
            rewritten = re.sub(r"(?<![\\w./-])uvicorn(?![\\w./-])", str(venv_uvicorn), command_text)
            if rewritten != command_text:
                return rewritten, {
                    "strategy": "workspace_venv_uvicorn_binary",
                    "from": "uvicorn",
                    "to": str(venv_uvicorn),
                }

        venv_activate = Path(workspace) / ".venv" / "bin" / "activate"
        if venv_activate.exists() and ".venv/bin/activate" not in command_text:
            rewritten = f"source .venv/bin/activate && {command_text}"
            return rewritten, {
                "strategy": "workspace_venv_activate_prefix",
                "activate_script": ".venv/bin/activate",
            }
        return command, None

    @classmethod
    def _resolve_worker_semantic_output_correction_policy(cls, agent_cfg: dict | None) -> dict:
        cfg = dict(agent_cfg or {})
        runtime_cfg = cfg.get("worker_runtime") if isinstance(cfg.get("worker_runtime"), dict) else {}
        raw_policy = runtime_cfg.get("semantic_output_correction")
        raw_policy = dict(raw_policy) if isinstance(raw_policy, dict) else {}
        if not raw_policy:
            return {}
        provider_cfg = raw_policy.get("embedding_provider")
        provider_cfg = dict(provider_cfg) if isinstance(provider_cfg, dict) else {}
        fields_cfg = raw_policy.get("fields")
        fields_cfg = dict(fields_cfg) if isinstance(fields_cfg, dict) else {}
        risk_cfg = fields_cfg.get("risk_classification")
        risk_cfg = dict(risk_cfg) if isinstance(risk_cfg, dict) else {}
        risk_candidates = [
            str(item).strip().lower()
            for item in list(risk_cfg.get("candidates") or ["low", "medium", "high", "critical"])
            if str(item).strip()
        ]
        deduped_risk_candidates: list[str] = []
        seen_candidates: set[str] = set()
        for item in risk_candidates:
            if item not in seen_candidates:
                seen_candidates.add(item)
                deduped_risk_candidates.append(item)
        provider = str(provider_cfg.get("provider") or "local").strip().lower() or "local"
        policy = {
            "enabled": bool(raw_policy.get("enabled", False)),
            "similarity_threshold": cls._bounded_float(
                raw_policy.get("similarity_threshold"),
                default=0.9,
                minimum=0.5,
                maximum=1.0,
            ),
            "min_margin": cls._bounded_float(raw_policy.get("min_margin"), default=0.03, minimum=0.0, maximum=1.0),
            "lexical_weight": cls._bounded_float(raw_policy.get("lexical_weight"), default=0.35, minimum=0.0, maximum=1.0),
            "embedding_provider": {
                "provider": provider,
                "dimensions": cls._bounded_int(provider_cfg.get("dimensions"), default=12, minimum=4, maximum=4096),
                "model_version": str(provider_cfg.get("model_version") or "").strip() or None,
                "base_url": str(provider_cfg.get("base_url") or "").strip() or None,
                "api_key": str(provider_cfg.get("api_key") or "").strip() or None,
                "model": str(provider_cfg.get("model") or "").strip() or None,
                "timeout_seconds": cls._bounded_int(provider_cfg.get("timeout_seconds"), default=20, minimum=1, maximum=120),
            },
            "fields": {
                "risk_classification": {
                    "enabled": bool(risk_cfg.get("enabled", True)),
                    "candidates": deduped_risk_candidates or ["low", "medium", "high", "critical"],
                }
            },
        }
        return policy

    @classmethod
    def _resolve_interactive_context_profile(cls, agent_cfg: dict | None, *, retry: bool = False) -> dict:
        cfg = dict(agent_cfg or {})
        runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
        profile_cfg = runtime_cfg.get("interactive_context_profile") if isinstance(runtime_cfg.get("interactive_context_profile"), dict) else {}

        task_brief_chars = cls._bounded_int(
            profile_cfg.get("task_brief_chars_retry" if retry else "task_brief_chars"),
            default=520 if retry else 900,
            minimum=180,
            maximum=4000,
        )
        hub_context_chars = cls._bounded_int(
            profile_cfg.get("hub_context_chars_retry" if retry else "hub_context_chars"),
            default=1200 if retry else 2600,
            minimum=256,
            maximum=12000,
        )
        research_prompt_chars = cls._bounded_int(
            profile_cfg.get("research_prompt_chars_retry" if retry else "research_prompt_chars"),
            default=700 if retry else 1800,
            minimum=200,
            maximum=8000,
        )
        artifact_ids_limit = cls._bounded_int(
            profile_cfg.get("artifact_ids_limit_retry" if retry else "artifact_ids_limit"),
            default=3 if retry else 6,
            minimum=1,
            maximum=20,
        )
        knowledge_ids_limit = cls._bounded_int(
            profile_cfg.get("knowledge_ids_limit_retry" if retry else "knowledge_ids_limit"),
            default=2 if retry else 4,
            minimum=1,
            maximum=20,
        )
        repo_refs_limit = cls._bounded_int(
            profile_cfg.get("repo_refs_limit_retry" if retry else "repo_refs_limit"),
            default=3 if retry else 6,
            minimum=1,
            maximum=30,
        )
        return {
            "compact": True,
            "retry": bool(retry),
            "task_brief_char_limit": task_brief_chars,
            "hub_context_char_limit": hub_context_chars,
            "research_prompt_char_limit": research_prompt_chars,
            "artifact_ids_limit": artifact_ids_limit,
            "knowledge_collection_ids_limit": knowledge_ids_limit,
            "repo_scope_refs_limit": repo_refs_limit,
        }

    @classmethod
    def _compact_research_context(
        cls,
        research_context: dict | None,
        *,
        profile: dict | None,
    ) -> dict | None:
        if not isinstance(research_context, dict):
            return research_context
        cfg = dict(profile or {})
        artifact_limit = cls._bounded_int(cfg.get("artifact_ids_limit"), default=6, minimum=1, maximum=20)
        knowledge_limit = cls._bounded_int(cfg.get("knowledge_collection_ids_limit"), default=4, minimum=1, maximum=20)
        repo_ref_limit = cls._bounded_int(cfg.get("repo_scope_refs_limit"), default=6, minimum=1, maximum=30)
        prompt_limit = cls._bounded_int(cfg.get("research_prompt_char_limit"), default=1800, minimum=200, maximum=12000)
        prompt_section = str((research_context or {}).get("prompt_section") or "").strip()
        if len(prompt_section) > prompt_limit:
            prompt_section = prompt_section[: max(1, prompt_limit - 14)].rstrip() + "\n\n[gekürzt]"
        return {
            **dict(research_context or {}),
            "artifact_ids": list((research_context or {}).get("artifact_ids") or [])[:artifact_limit],
            "knowledge_collection_ids": list((research_context or {}).get("knowledge_collection_ids") or [])[:knowledge_limit],
            "repo_scope_refs": list((research_context or {}).get("repo_scope_refs") or [])[:repo_ref_limit],
            "prompt_section": prompt_section or None,
            "context_char_count": min(
                int((research_context or {}).get("context_char_count") or len(prompt_section)),
                len(prompt_section) if prompt_section else int((research_context or {}).get("context_char_count") or 0),
            ),
        }

    @staticmethod
    def _interactive_timeout_like_failure(*, rc: int, output: str, stderr: str) -> bool:
        text = f"{output or ''}\n{stderr or ''}".strip()
        if rc != 0 and not text:
            return True
        marker = text.lower()
        return "timeout" in marker or "timed out" in marker or "read timed out" in marker

    @classmethod
    def _resolve_interactive_propose_timeout(cls, agent_cfg: dict | None, *, fallback: int) -> int:
        cfg = dict(agent_cfg or {})
        runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
        configured = cls._bounded_int(
            runtime_cfg.get("interactive_propose_timeout_seconds"),
            default=420,
            minimum=120,
            maximum=1800,
        )
        return max(int(fallback or 60), configured)

    @classmethod
    def _resolve_interactive_retry_timeout(cls, agent_cfg: dict | None, *, fallback: int) -> int:
        cfg = dict(agent_cfg or {})
        runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
        configured = cls._bounded_int(
            runtime_cfg.get("interactive_retry_timeout_seconds"),
            default=max(int(fallback or 60), 480),
            minimum=120,
            maximum=1800,
        )
        return max(int(fallback or 60), configured)

    @staticmethod
    def _build_flow_metrics_payload(
        *,
        run_id: str | None,
        phase: str,
        propose_ok: bool | None,
        execute_ok: bool | None,
        artifact_created: bool | None,
        worker_profile: str | None = None,
        profile_source: str | None = None,
        policy_classification: str | None = None,
        retrieval_cache_hit: bool | None = None,
        retrieval_latency_ms: int | None = None,
        retrieval_quality_score: float | None = None,
    ) -> dict:
        return {
            "run_id": str(run_id or "").strip() or None,
            "phase": str(phase or "").strip() or None,
            "propose_ok": None if propose_ok is None else bool(propose_ok),
            "execute_ok": None if execute_ok is None else bool(execute_ok),
            "artifact_created": None if artifact_created is None else bool(artifact_created),
            "worker_profile": normalize_worker_execution_profile(worker_profile),
            "profile_source": str(profile_source or "agent_default").strip().lower() or "agent_default",
            "policy_classification": str(policy_classification or "").strip().lower() or None,
            "retrieval_cache_hit": None if retrieval_cache_hit is None else bool(retrieval_cache_hit),
            "retrieval_latency_ms": None if retrieval_latency_ms is None else int(retrieval_latency_ms),
            "retrieval_quality_score": None if retrieval_quality_score is None else float(retrieval_quality_score),
        }

    @staticmethod
    def _build_planner_observability_payload(
        *,
        trigger: str | None,
        policy_decision_ref: str | None,
        plan_diff: dict | None,
    ) -> dict:
        return {
            "trigger": str(trigger or "").strip().lower() or "unknown",
            "policy_decision_ref": str(policy_decision_ref or "").strip() or None,
            "plan_diff": dict(plan_diff or {}),
        }

    @staticmethod
    def _extract_retrieval_trace_link(context_payload: dict | None) -> dict[str, str | None]:
        payload = dict(context_payload or {})
        metadata = dict(payload.get("bundle_metadata") or {})
        retrieval_trace = dict(metadata.get("retrieval_trace") or {})
        selection_trace = dict(metadata.get("selection_trace") or {})
        trace_id = str(
            retrieval_trace.get("trace_id")
            or selection_trace.get("retrieval_trace_id")
            or selection_trace.get("trace_id")
            or ""
        ).strip() or None
        context_hash = str(
            retrieval_trace.get("context_hash")
            or metadata.get("context_hash")
            or ""
        ).strip() or None
        manifest_hash = str(
            retrieval_trace.get("manifest_hash")
            or metadata.get("manifest_hash")
            or ""
        ).strip() or None
        return {
            "retrieval_trace_id": trace_id,
            "retrieval_context_hash": context_hash,
            "retrieval_manifest_hash": manifest_hash,
        }

    def _build_source_catalog_from_execution_context(
        self,
        *,
        tid: str,
        task: dict,
        llm_scope: str = "local_only",
    ) -> dict | None:
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        context_payload = dict(execution_context.get("context") or {})
        chunks = [dict(item) for item in list(context_payload.get("chunks") or []) if isinstance(item, dict)]
        if not chunks:
            return None
        selected: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []
        for chunk in chunks:
            metadata = dict(chunk.get("metadata") or {})
            selected.append(
                {
                    "path": str(chunk.get("source") or metadata.get("file") or ""),
                    "record_id": str(metadata.get("record_id") or chunk.get("record_id") or ""),
                    "content_hash": str(metadata.get("record_id") or chunk.get("record_id") or chunk.get("source") or ""),
                    "channel": str(metadata.get("channel") or metadata.get("engine") or ""),
                    "metadata": metadata,
                }
            )
            provenance.append(
                {
                    "engine": str(metadata.get("engine") or metadata.get("channel") or ""),
                    "record_id": str(metadata.get("record_id") or chunk.get("record_id") or chunk.get("source") or ""),
                    "file": str(metadata.get("file") or chunk.get("source") or ""),
                    "kind": str(metadata.get("record_kind") or metadata.get("kind") or ""),
                    "score": float(chunk.get("score") or 0.0),
                    "manifest_hash": str(metadata.get("source_manifest_hash") or ""),
                    "line_start": metadata.get("line_start"),
                    "line_end": metadata.get("line_end"),
                    "sensitivity": str(metadata.get("sensitivity") or "internal"),
                }
            )
        retrieval_trace = dict((context_payload.get("bundle_metadata") or {}).get("retrieval_trace") or {})
        if not retrieval_trace:
            retrieval_trace = dict(context_payload.get("retrieval_trace") or {})
        from agent.services.source_catalog_service import get_source_catalog_service

        return get_source_catalog_service().build_catalog(
            task_id=str(tid),
            retrieval_payload={
                "selected": selected,
                "provenance": provenance,
                "retrieval_trace": retrieval_trace,
            },
            llm_scope=llm_scope,
        )

    @staticmethod
    def _render_citation_contract_prompt(source_catalog: dict | None) -> str:
        if not isinstance(source_catalog, dict):
            return ""
        sources = [dict(item) for item in list(source_catalog.get("sources") or []) if isinstance(item, dict)]
        if not sources:
            return ""
        preview = []
        for item in sources[:12]:
            preview.append(
                {
                    "source_id": item.get("source_id"),
                    "source_type": item.get("source_type"),
                    "path": item.get("path"),
                    "record_id": item.get("record_id"),
                    "allowed_for_llm_scope": bool(item.get("allowed_for_llm_scope", True)),
                }
            )
        return (
            "Citation Contract (grounded_answer.v1):\n"
            "- Use only provided source IDs (SRC_* or RUN_*).\n"
            "- Do not invent source IDs, paths, line ranges, or tool result IDs.\n"
            "- Every factual claim must include citation_refs.\n"
            "- Tool execution claims must cite RUN_* evidence.\n"
            "- Uncertain statements must be marked with confidence=unverified and empty citation_refs.\n"
            f"- source_catalog_id: {source_catalog.get('catalog_id')}\n"
            f"- source_catalog_hash: {source_catalog.get('catalog_hash')}\n"
            "Allowed sources excerpt:\n"
            + json.dumps(preview, ensure_ascii=False)
        )

    @staticmethod
    def _extract_grounded_answer_payload(output: str | None) -> dict | None:
        raw = str(output or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, dict) and str(parsed.get("schema") or "").strip() == "grounded_answer.v1":
            return parsed
        return None

    @staticmethod
    def _update_task_flow_metrics(
        *,
        tid: str,
        task: dict,
        flow_metrics: dict,
    ) -> None:
        current_task = get_local_task_status(tid) or dict(task or {})
        verification_status = dict(current_task.get("verification_status") or {})
        merged = dict(verification_status.get("task_flow_metrics") or {})
        merged.update({**dict(flow_metrics or {}), "updated_at": time.time()})
        verification_status["task_flow_metrics"] = merged
        update_local_task_status(
            tid,
            str(current_task.get("status") or task.get("status") or "assigned"),
            verification_status=verification_status,
        )

    @staticmethod
    def _invoke_cli_runner(cli_runner: Callable, **cli_kwargs):
        try:
            return cli_runner(**cli_kwargs)
        except TypeError as exc:
            message = str(exc)
            if "unexpected keyword argument" not in message:
                raise
            signature_target = cli_runner
            side_effect = getattr(cli_runner, "side_effect", None)
            if callable(side_effect):
                signature_target = side_effect
            signature = inspect.signature(signature_target)
            if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
                raise
            filtered_kwargs = {key: value for key, value in cli_kwargs.items() if key in signature.parameters}
            return cli_runner(**filtered_kwargs)

    def propose_task_step(
        self,
        tid: str,
        request_data,
        *,
        cli_runner: Callable,
        forwarder: Callable,
        tool_definitions_resolver: Callable,
    ) -> TaskScopedRouteResponse:
        task = self._require_task(tid)
        terminal_guard = self._terminal_parent_goal_guard(tid=tid, task=task, phase="propose")
        if terminal_guard is not None:
            return terminal_guard
        forwarded = self._forward_task_request_if_remote(
            tid=tid,
            task=task,
            endpoint=f"/tasks/{tid}/step/propose",
            payload=request_data.model_dump(),
            forwarder=forwarder,
            on_success=lambda response, loaded_task: self._persist_forwarded_proposal(
                response,
                loaded_task,
                request_payload=request_data.model_dump(),
            ),
        )
        if forwarded is not None:
            return forwarded

        scoped_resolution = get_goal_config_runtime_service().get_effective_config(
            goal_id=str(task.get("goal_id") or "").strip() or None,
            task_id=tid,
        )
        cfg = dict(scoped_resolution.config or {})
        base_prompt = request_data.prompt or task.get("description") or task.get("prompt") or f"Bearbeite Task {tid}"
        source_catalog = self._build_source_catalog_from_execution_context(
            tid=tid,
            task=task,
            llm_scope="local_only",
        )
        if not isinstance(source_catalog, dict):
            existing_source_catalog = dict((task.get("verification_status") or {}).get("source_catalog") or {})
            if existing_source_catalog:
                source_catalog = {
                    "catalog_id": existing_source_catalog.get("source_catalog_id"),
                    "catalog_hash": existing_source_catalog.get("source_catalog_hash"),
                    "sources": list(existing_source_catalog.get("sources") or []),
                }
        citation_contract = self._render_citation_contract_prompt(source_catalog)
        explicit_task_kind = str(task.get("task_kind") or "").strip().lower()
        task_kind = explicit_task_kind or normalize_task_kind(None, base_prompt)
        rc_input = getattr(request_data, "research_context", None)
        if rc_input is None:
            stored = dict((task or {}).get("worker_execution_context") or {}).get("research_context_input")
            if stored:
                rc_input = stored
        research_context_summary = get_research_context_bridge_service().build_context(
            task=task,
            research_context=rc_input,
            query=base_prompt,
        )
        from worker.core.propose_orchestrator import ProposeStrategyOrchestrator, ProposeContext
        from worker.core.propose import ExecutableProposal, validate_executable_proposal
        from agent.services.propose_strategy_registry import build_strategy_registry

        propose_policy_override = task.get("propose_policy_override", {})
        task_override = {}
        if getattr(request_data, "strategy_mode", None):
            task_override["strategy_mode"] = str(getattr(request_data, "strategy_mode")).strip().lower()
        policy = get_propose_policy_service().get_effective_policy(
            task_kind=task_kind,
            task_override=task_override or None,
            project_config=cfg,
            admin_overrides=propose_policy_override
        )
        compaction_payload = None
        compaction_meta = None
        if bool(getattr(policy, "context_compaction_enabled", True)):
            mode_data = {}
            if isinstance(task.get("mode_data"), dict):
                mode_data = {**mode_data, **dict(task.get("mode_data") or {})}
            if isinstance((task.get("worker_execution_context") or {}).get("mode_data"), dict):
                mode_data = {**mode_data, **dict((task.get("worker_execution_context") or {}).get("mode_data") or {})}
            llm_cfg = dict(cfg.get("llm_config") or {})
            planning_policy = dict(cfg.get("planning_policy") or {})
            compacted = get_planning_context_compactor_service().compact(
                goal_text=str(base_prompt or ""),
                context_text=str((research_context_summary or {}).get("prompt_section") or ""),
                mode=str(task_kind or "generic"),
                mode_data=mode_data,
                planning_policy=planning_policy,
                llm_config=llm_cfg,
                policy=policy,
            )
            compaction_payload = dict(compacted.payload or {})
            compaction_meta = dict(compacted.meta or {})
            _c_status = str(compaction_meta.get("status") or "").strip().lower()
            if _c_status == "success":
                record_product_event(
                    "planning_context_compaction_succeeded",
                    actor="task_scoped_execution_service",
                    details={
                        "task_id": tid,
                        "status": _c_status,
                        "input_chars": compaction_meta.get("input_chars"),
                        "output_chars": compaction_meta.get("output_chars"),
                        "reduction_ratio": compaction_meta.get("reduction_ratio"),
                    },
                    goal_id=str(task.get("goal_id") or "") or None,
                )
            elif _c_status in {"fallback", "bypassed"}:
                record_product_event(
                    "planning_context_compaction_fallback_used",
                    actor="task_scoped_execution_service",
                    details={
                        "task_id": tid,
                        "status": _c_status,
                        "error_classification": compaction_meta.get("error_classification"),
                    },
                    goal_id=str(task.get("goal_id") or "") or None,
                )
            elif _c_status == "failed":
                record_product_event(
                    "planning_context_compaction_failed",
                    actor="task_scoped_execution_service",
                    details={
                        "task_id": tid,
                        "status": _c_status,
                        "error_classification": compaction_meta.get("error_classification"),
                    },
                    goal_id=str(task.get("goal_id") or "") or None,
                )
            if (
                str(compaction_meta.get("status") or "").strip().lower() == "failed"
                and bool(getattr(policy, "context_compaction_required", False))
                and not bool(getattr(policy, "context_compactor_fail_open", False))
            ):
                return TaskScopedRouteResponse(
                    status="error",
                    message="planning_context_compaction_failed",
                    data={
                        "task_id": tid,
                        "status": "failed",
                        "context_compaction": compaction_meta,
                    },
                    code=422,
                )
        system_prompt = self._get_system_prompt_for_task(tid)
        assembled_instruction = get_instruction_layer_service().assemble_for_task(
            task=task,
            base_prompt=base_prompt,
            system_prompt=system_prompt,
            emit_audit=False,
        )
        instruction_stack_payload = dict(assembled_instruction.get("instruction_stack") or {})
        instruction_diagnostics = dict(assembled_instruction.get("diagnostics") or {})
        rendered_system_prompt = str(assembled_instruction.get("rendered_system_prompt") or "").strip() or None

        strategies = build_strategy_registry()

        orch = ProposeStrategyOrchestrator(policy, strategies)
        context = ProposeContext(
            goal_id=task.get("goal_id", "unknown"),
            task_id=tid,
            task=task,
            base_prompt=base_prompt,
            research_context=research_context_summary,
            cli_runner=cli_runner,
            tool_definitions_resolver=tool_definitions_resolver,
            policy=policy,
            effective_config=cfg or None,
            instruction_stack=instruction_stack_payload or None,
            rendered_system_prompt=rendered_system_prompt,
            instruction_diagnostics=instruction_diagnostics or None,
            planning_context_compaction=compaction_payload,
            planning_context_compaction_meta=compaction_meta,
        )
        if citation_contract:
            context.base_prompt = f"{context.base_prompt}\n\n{citation_contract}"
        had_llm_goal_id = False
        had_llm_task_id = False
        previous_llm_goal_id = None
        previous_llm_task_id = None
        if has_request_context():
            had_llm_goal_id = hasattr(g, "llm_goal_id")
            had_llm_task_id = hasattr(g, "llm_task_id")
            previous_llm_goal_id = getattr(g, "llm_goal_id", None)
            previous_llm_task_id = getattr(g, "llm_task_id", None)
            g.llm_goal_id = str(task.get("goal_id") or "").strip() or None
            g.llm_task_id = str(tid or "").strip() or None
        try:
            result = orch.run(context)
        finally:
            if has_request_context():
                if had_llm_goal_id:
                    g.llm_goal_id = previous_llm_goal_id
                else:
                    try:
                        delattr(g, "llm_goal_id")
                    except Exception:
                        pass
                if had_llm_task_id:
                    g.llm_task_id = previous_llm_task_id
                else:
                    try:
                        delattr(g, "llm_task_id")
                    except Exception:
                        pass
        result_dict = result.to_dict()
        if not result.is_executable:
            try:
                rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(
                    cli_runner,
                    prompt=str(base_prompt or ""),
                    options=["--no-interaction"],
                    timeout=self._resolve_task_propose_timeout(cfg, task_kind),
                    backend="aider",
                    model=getattr(request_data, "model", None),
                    routing_policy={"mode": "adaptive", "task_kind": task_kind, "policy_version": "v1"},
                    session=None,
                    workdir=None,
                )
                raw_res, _output_source = self._coalesce_cli_output(cli_out, cli_err)
                parsed = json.loads(str(raw_res or "{}"))
                fallback_command = str(parsed.get("command") or "").strip() or None
                fallback_tool_calls = parsed.get("tool_calls") if isinstance(parsed.get("tool_calls"), list) else []
                fallback_reason = str(parsed.get("reason") or result.reason or "fallback_cli_proposal").strip()
                if rc == 0 and (fallback_command or fallback_tool_calls):
                    result_dict["command"] = fallback_command
                    result_dict["tool_calls"] = fallback_tool_calls
                    result_dict["reason"] = fallback_reason
                    result_dict["backend"] = backend_used
                    result_dict["status"] = "executable"
            except Exception:
                pass

        # Persist to last_proposal so execute step and API can read it.
        _sgpt_routing = cfg.get("sgpt_routing") if isinstance(cfg.get("sgpt_routing"), dict) else {}
        _backend_map = _sgpt_routing.get("task_kind_backend") if isinstance(_sgpt_routing.get("task_kind_backend"), dict) else {}
        _runtime_backend = str(_backend_map.get(task_kind) or _backend_map.get("*") or "").strip() or None
        propose_strategy_meta = {
            "attempted_strategies": result.metadata.get("attempted_strategies", []),
            "selected_strategy": result.metadata.get("selected_strategy"),
            "proposal_status": result.status,
            "proposal_reason": result.reason,
            "normalization_format": result.metadata.get("source_format"),
            "effective_strategy_mode": getattr(policy, "effective_strategy_mode", None) or task_override.get("strategy_mode"),
            "goal_config_source": scoped_resolution.source,
            "runtime_selection": {
                "provider": cfg.get("default_provider"),
                "model": cfg.get("default_model"),
                "backend": _runtime_backend,
                "source": scoped_resolution.source,
            },
            "instruction_stack": {
                "present": bool(instruction_stack_payload),
                "checksum": str(instruction_stack_payload.get("checksum") or "").strip() or None,
                "applied_layers_count": len(list(instruction_diagnostics.get("applied_layers") or [])),
                "suppressed_layers_count": len(list(instruction_diagnostics.get("suppressed_layers") or [])),
            },
            "planning_context_compaction": {
                "used": bool(compaction_meta is not None),
                "status": (compaction_meta or {}).get("status"),
                "reduction_ratio": (compaction_meta or {}).get("reduction_ratio"),
                "error_classification": (compaction_meta or {}).get("error_classification"),
                "input_chars": (compaction_meta or {}).get("input_chars"),
                "output_chars": (compaction_meta or {}).get("output_chars"),
            },
            "source_catalog_id": (source_catalog or {}).get("catalog_id") if isinstance(source_catalog, dict) else None,
            "source_catalog_hash": (source_catalog or {}).get("catalog_hash") if isinstance(source_catalog, dict) else None,
            "answer_schema": "grounded_answer.v1",
        }
        proposal_meta = dict(getattr(result.proposal, "metadata", None) or {}) if result.proposal is not None else {}
        proposal_provider = str(proposal_meta.get("provider") or "").strip() or None
        proposal_model = str(proposal_meta.get("model") or "").strip() or None
        strategy_id = str(getattr(result, "strategy_id", "") or "").strip() or None
        real_llm_call_profile = list((result.metadata or {}).get("llm_call_profile") or [])
        if not real_llm_call_profile:
            real_llm_call_profile = list(proposal_meta.get("llm_call_profile") or [])
        llm_call_profile = [normalize_llm_call_profile_entry(entry) for entry in real_llm_call_profile if isinstance(entry, dict)]
        if not llm_call_profile and self._allow_synthetic_llm_profile_fallback():
            # Bridge fallback: preserves correlation for diagnostics when strategy does not expose real call metrics yet.
            llm_call_profile = [
                {
                    "name": f"propose_{strategy_id or 'orchestrator'}",
                    "backend": "orchestrator",
                    "provider": proposal_provider,
                    "model": proposal_model,
                    "success": True,
                    "latency_ms": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "source": "orchestrator_synthetic",
                    "estimated": True,
                    "error_type": None,
                    "error_message": None,
                    "started_at": None,
                    "ended_at": None,
                }
            ]
        cli_result = {
            "returncode": 0,
            "latency_ms": None,
            "stderr_preview": None,
            "output_source": "orchestrator",
            **({"llm_call_profile": llm_call_profile} if llm_call_profile else {}),
        }
        resolved_reason = str(result_dict.get("reason") or result.reason or "").strip() or result.reason
        resolved_command = result_dict.get("command") if isinstance(result_dict.get("command"), str) else (
            result.proposal.command if result.is_executable and result.proposal is not None else None
        )
        resolved_tool_calls = (
            list(result_dict.get("tool_calls") or [])
            if isinstance(result_dict.get("tool_calls"), list)
            else ((result.proposal.tool_calls or []) if result.is_executable and result.proposal is not None else [])
        )
        resolved_backend = str(result_dict.get("backend") or "orchestrator").strip() or "orchestrator"
        get_core_services().task_execution_service.persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=resolved_reason,
            raw=None,
            backend=resolved_backend,
            model=None,
            routing={
                "task_kind": task_kind,
                "propose_strategy_meta": propose_strategy_meta,
                "goal_config_source": scoped_resolution.source,
            },
            cli_result=cli_result,
            worker_context={"strategy": result.metadata.get("selected_strategy")},
            trace={"policy_version": "v1"},
            review=None,
            command=resolved_command,
            tool_calls=resolved_tool_calls,
            research_context=research_context_summary,
            history_event={
                "event_type": "proposal_result",
                "reason": resolved_reason,
                "backend": resolved_backend,
                "propose_strategy_meta": propose_strategy_meta,
            },
        )
        if isinstance(source_catalog, dict):
            verification_status = dict(task.get("verification_status") or {})
            verification_status["source_catalog"] = {
                "source_catalog_id": source_catalog.get("catalog_id"),
                "source_catalog_hash": source_catalog.get("catalog_hash"),
                "source_count": len(list(source_catalog.get("sources") or [])),
                "retrieval_trace_id": source_catalog.get("retrieval_trace_id"),
                "sources": list(source_catalog.get("sources") or []),
            }
            verification_status["answer_verification"] = {
                **dict(verification_status.get("answer_verification") or {}),
                "answer_schema": "grounded_answer.v1",
                "citation_verification_status": "pending",
            }
            update_local_task_status(
                tid,
                str(task.get("status") or "proposing"),
                verification_status=verification_status,
            )

        return TaskScopedRouteResponse(data={**result_dict, "propose_strategy_meta": propose_strategy_meta})

    def execute_task_step(
        self,
        tid: str,
        request_data,
        *,
        forwarder: Callable,
        cli_runner: Callable | None = None,
        tool_definitions_resolver: Callable | None = None,
    ) -> TaskScopedRouteResponse:
        task = self._require_task(tid)
        terminal_guard = self._terminal_parent_goal_guard(tid=tid, task=task, phase="execute")
        if terminal_guard is not None:
            return terminal_guard
        forwarded = self._forward_task_request_if_remote(
            tid=tid,
            task=task,
            endpoint=f"/tasks/{tid}/step/execute",
            payload=request_data.model_dump(),
            forwarder=forwarder,
            on_success=lambda response, loaded_task: self._persist_forwarded_execution(
                tid=tid,
                response=response,
                task=loaded_task,
                request_data=request_data,
            ),
        )
        if forwarded is not None:
            return forwarded

        explicit_task_kind = str(
            getattr(request_data, "task_kind", None)
            or ((task.get("last_proposal", {}) or {}).get("routing") or {}).get("task_kind")
            or task.get("task_kind")
            or ""
        ).strip().lower()
        task_kind = explicit_task_kind or normalize_task_kind(
            None,
            request_data.command or task.get("description") or task.get("prompt") or "",
        )
        handler_response = self._try_handler_execute(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            forwarder=forwarder,
        )
        if handler_response is not None:
            return handler_response

        # HF-T023: Hermes is proposal/review-only in phase 1 — block mutation paths
        requested_backend = str(getattr(request_data, "requested_backend", None) or "").strip().lower()
        if requested_backend == "hermes":
            return TaskScopedRouteResponse(
                data={
                    "status": "denied",
                    "reason": "hermes_phase1_no_execute_mutation",
                    "task_id": tid,
                    "task_kind": task_kind,
                    "backend": "hermes",
                },
                status="denied",
                message="Hermes cannot execute mutation tasks in phase 1",
                code=403,
            )

        scoped_resolution = get_goal_config_runtime_service().get_effective_config(
            goal_id=str(task.get("goal_id") or "").strip() or None,
            task_id=tid,
        )
        agent_cfg = dict(scoped_resolution.config or {})
        execution_policy = get_core_services().task_execution_service.resolve_policy(
            request_data,
            agent_cfg=agent_cfg,
            source="task_execute",
        )

        command = request_data.command
        tool_calls = request_data.tool_calls
        reason = "Direkte Ausführung"
        used_last_proposal = False
        proposal_meta = dict(task.get("last_proposal") or {})
        proposal_routing = dict(proposal_meta.get("routing") or {})
        proposal_worker_context = dict(proposal_meta.get("worker_context") or {})
        worker_profile = normalize_worker_execution_profile(
            proposal_worker_context.get("worker_profile") or proposal_routing.get("worker_profile")
        )
        profile_source = str(
            proposal_worker_context.get("profile_source") or proposal_routing.get("profile_source") or "agent_default"
        ).strip().lower() or "agent_default"
        policy_classification_summary = str(
            proposal_routing.get("policy_classification_summary") or proposal_routing.get("reason") or ""
        ).strip().lower() or None

        if not command and not tool_calls:
            proposal = task.get("last_proposal")
            if not proposal:
                raise TaskConflictError("no_proposal")
            research_artifact = proposal.get("research_artifact") if isinstance(proposal, dict) else None
            if isinstance(research_artifact, dict):
                return self._execute_research_artifact(
                    tid=tid,
                    task=task,
                    proposal=proposal,
                    research_artifact=research_artifact,
                    execution_policy=execution_policy,
                )
            try:
                from worker.core.propose import validate_executable_proposal
                command, tool_calls, _reason = validate_executable_proposal(proposal)
                reason = _reason or proposal.get("reason", "ExecutableProposal executed")
            except (ValueError, TypeError) as ve:
                return TaskScopedRouteResponse(
                    data={
                        "status": "denied",
                        "reason": "invalid_executable_proposal_format",
                        "task_id": tid,
                        "proposal_preview": str(proposal)[:200],
                        "validation_errors": [str(ve)],
                    },
                    status="denied",
                    message="ExecutableProposal validation failed",
                    code=400,
                )
            used_last_proposal = True

        if task_kind == "domain_action":
            return self._execute_domain_action(
                tid=tid,
                task=task,
                task_kind=task_kind,
                request_data=request_data,
                command=command,
                reason=reason,
                execution_policy=execution_policy,
            )

        if command == _INTERACTIVE_TERMINAL_FINALIZE_COMMAND:
            return self._finalize_interactive_terminal_execution(
                tid=tid,
                task=task,
                reason=reason,
                execution_policy=execution_policy,
            )

        exec_started_at = time.time()
        workspace_ctx = get_worker_workspace_service().resolve_workspace_context(task=task)
        lock_ok, lock_reason = get_worker_workspace_service().acquire_output_dir_lock(task=task, workspace_dir=workspace_ctx.workspace_dir)
        if not lock_ok:
            return TaskScopedRouteResponse(
                data={"status": "blocked", "reason_code": lock_reason or "workspace_write_conflict", "task_id": tid},
                status="blocked",
                message="Shared output directory is currently locked",
                code=409,
            )
        context_delivery_result = None
        if workspace_ctx.context_policy is not None and getattr(workspace_ctx.context_policy, "scope_mode", "full") != "full":
            try:
                from agent.services.context_delivery_service import get_context_delivery_service
                context_delivery_result = get_context_delivery_service().deliver(task=task, workspace_ctx=workspace_ctx)
            except Exception as _csd_err:
                return TaskScopedRouteResponse(
                    data={"status": "failed", "error": "context_delivery_failed", "detail": str(_csd_err), "task_id": tid},
                    status="failed",
                    message="Context delivery failed",
                    code=500,
                )
        try:
            before_workspace_snapshot = get_worker_workspace_service().snapshot_directory(workspace_ctx.workspace_dir)
            command, runtime_command_rewrite = self._rewrite_runtime_command_for_workspace_tools(
                command=command,
                workspace_dir=str(workspace_ctx.workspace_dir),
            )
            pipeline = new_pipeline_trace(
                pipeline="task_execute",
                task_kind=((task.get("last_proposal", {}) or {}).get("routing") or {}).get("task_kind"),
                policy_version=((task.get("last_proposal", {}) or {}).get("trace") or {}).get("policy_version"),
                metadata={"task_id": tid},
            )
            native_artifact_refs: list[dict] = []
            execution_repair_meta: dict | None = None
            if self._should_use_native_worker_runtime(proposal_meta=proposal_meta, agent_cfg=agent_cfg, command=command):
                append_stage(
                    pipeline,
                    name="native_worker_execute",
                    status="ok",
                    metadata={"runtime_path": "native_worker_pipeline"},
                )
                native_execution = get_native_worker_runtime_service().execute_and_verify_command(
                    tid=tid,
                    task=task,
                    command=str(command or ""),
                    trace_id=str(((proposal_meta.get("trace") or {}).get("trace_id") or f"native-exec-{tid}")),
                    worker_profile=worker_profile,
                    profile_source=profile_source,
                    timeout_seconds=int(execution_policy.timeout_seconds),
                    workspace_dir=workspace_ctx.workspace_dir,
                    native_runtime_payload=(proposal_worker_context.get("native_runtime") if isinstance(proposal_worker_context.get("native_runtime"), dict) else {}),
                    agent_cfg=agent_cfg,
                )
                execution_run = LocalExecutionResult(
                    output=str(native_execution.get("output") or ""),
                    exit_code=int(native_execution.get("exit_code") or 1),
                    retries_used=0,
                    failure_type=str(native_execution.get("failure_type") or "native_worker_runtime"),
                    retry_history=[],
                    status=str(native_execution.get("status") or "failed"),
                    loop_signals=[],
                    loop_detection=None,
                    approval_decision=dict(native_execution.get("approval_decision") or {}),
                )
                native_artifact_refs = [ref for ref in list(native_execution.get("artifact_refs") or []) if isinstance(ref, dict)]
                execution_repair_meta = {
                    "native_worker_runtime": dict(native_execution.get("native_runtime") or {}),
                    "runtime_path": "native_worker_pipeline",
                }
                native_policy_summary = str(native_execution.get("policy_classification_summary") or "").strip().lower() or None
                if native_policy_summary:
                    policy_classification_summary = native_policy_summary
            else:
                execution_run = get_core_services().task_execution_service.execute_local_step(
                    tid=tid,
                    task=task,
                    command=command,
                    tool_calls=tool_calls,
                    execution_policy=execution_policy,
                    guard_cfg=agent_cfg,
                    pipeline=pipeline,
                    exec_started_at=exec_started_at,
                    working_directory=str(workspace_ctx.workspace_dir),
                )
                if used_last_proposal and cli_runner and (
                    self._is_shell_meta_blocked_failure(execution_run.output, execution_run.failure_type)
                    or self._is_command_not_found_failure(execution_run.output, execution_run.failure_type)
                ):
                    repaired_execution = self._attempt_repaired_execute_after_meta_block(
                        tid=tid,
                        task=task,
                        task_kind=task_kind,
                        command=command,
                        execution_output=execution_run.output,
                        execution_policy=execution_policy,
                        agent_cfg=agent_cfg,
                        cli_runner=cli_runner,
                        tool_definitions_resolver=tool_definitions_resolver,
                        pipeline=pipeline,
                        workspace_dir=str(workspace_ctx.workspace_dir),
                        exec_started_at=exec_started_at,
                    )
                    if repaired_execution:
                        command = repaired_execution["command"]
                        tool_calls = repaired_execution["tool_calls"]
                        reason = repaired_execution["reason"]
                        execution_run = repaired_execution["execution_run"]
                        execution_repair_meta = repaired_execution["repair_meta"]
            after_workspace_snapshot = get_worker_workspace_service().snapshot_directory(workspace_ctx.workspace_dir)
            changed_files = get_worker_workspace_service().detect_changed_files(before_workspace_snapshot, after_workspace_snapshot)
            meaningful_changed_files = get_worker_workspace_service().filter_meaningful_changed_files(changed_files)
            # FA-T014: Explicit FileChangeSet collection
            from worker.core.file_change_set import diff_snapshots
            from pathlib import Path
            before_id = hashlib.sha256(str(sorted(before_workspace_snapshot.items())).encode()).hexdigest()[:16]
            after_id = hashlib.sha256(str(sorted(after_workspace_snapshot.items())).encode()).hexdigest()[:16]
            exec_id = f"exec-{tid}-{int(time.time()*1000)}"
            fcs = diff_snapshots(
                task_id=tid,
                execution_id=exec_id,
                workspace_root=Path(workspace_ctx.workspace_dir),
                before_snapshot_id=before_id,
                before_snapshot=before_workspace_snapshot,
                after_snapshot_id=after_id,
                after_snapshot=after_workspace_snapshot,
            )
            git_pushed: bool = False
            git_ctx = getattr(workspace_ctx, "git_context", None)
            if git_ctx is not None and getattr(git_ctx, "is_clone", False) and meaningful_changed_files:
                try:
                    from agent.services.workspace_git_service import get_workspace_git_service
                    git_pushed = bool(get_workspace_git_service().commit_and_push(
                        git_ctx.workspace_dir,
                        branch=git_ctx.branch,
                        message=f"task {str(tid)[:12]}: {str(task.get('title') or tid)[:60]}",
                    ))
                except Exception as _git_push_err:
                    logging.warning("git commit+push failed for task %s: %s", tid, _git_push_err)
            workspace_artifact_refs = get_worker_workspace_service().sync_changed_files_to_artifacts(
                task_id=tid,
                task=task,
                workspace_dir=workspace_ctx.workspace_dir,
                changed_rel_paths=changed_files,
                sync_cfg=workspace_ctx.artifact_sync,
            )
            combined_artifact_refs = [*list(workspace_artifact_refs or []), *list(native_artifact_refs or [])]
            execution_duration_ms = int((time.time() - exec_started_at) * 1000)
            tool_run_refs: list[dict] = []
            try:
                from agent.services.tool_run_catalog_service import get_tool_run_catalog_service

                run_entry = get_tool_run_catalog_service().build_run_entry(
                    task_id=str(tid),
                    index=1,
                    tool_name="shell",
                    command=str(command or ""),
                    exit_code=int(execution_run.exit_code),
                    stdout=str(execution_run.output or ""),
                    stderr="",
                    artifact_paths=[
                        str(item.get("path") or item.get("artifact_path") or "")
                        for item in list(combined_artifact_refs or [])
                        if isinstance(item, dict)
                    ],
                    started_at=exec_started_at,
                    ended_at=time.time(),
                )
                tool_run_refs = [run_entry]
            except Exception:
                tool_run_refs = []
            proposal_meta = task.get("last_proposal", {}) or {}
            trace = build_trace_record(
                task_id=tid,
                event_type="execution_result",
                task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
                backend=proposal_meta.get("backend"),
                requested_backend=proposal_meta.get("backend"),
                routing_reason=((proposal_meta.get("routing") or {}).get("reason")),
                policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
                metadata={
                    "retries_used": execution_run.retries_used,
                    "duration_ms": execution_duration_ms,
                    "failure_type": execution_run.failure_type,
                },
            )
            if execution_run.status == "completed":
                from agent.metrics import TASK_COMPLETED

                TASK_COMPLETED.inc()
            else:
                from agent.metrics import TASK_FAILED

                TASK_FAILED.inc()

            response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
                tid=tid,
                task=task,
                status=execution_run.status,
                reason=reason,
                command=command,
                tool_calls=tool_calls,
                output=execution_run.output,
                exit_code=execution_run.exit_code,
                retries_used=execution_run.retries_used,
                retry_history=execution_run.retry_history,
                failure_type=execution_run.failure_type,
                execution_duration_ms=execution_duration_ms,
                trace=trace,
                pipeline={**pipeline, "trace_id": trace["trace_id"]},
                execution_policy=execution_policy,
                artifact_refs=combined_artifact_refs or None,
                extra_history={
                    "workspace_changed_files": changed_files,
                    "workspace_meaningful_changed_files": meaningful_changed_files,
                    "file_change_set": fcs.to_dict(),
                    "workspace_dir": str(workspace_ctx.workspace_dir),
                    "workspace_artifact_count": len(workspace_artifact_refs),
                    "native_artifact_count": len(native_artifact_refs),
                    "workspace_state_sync": _build_workspace_state_sync_record(
                        task=task,
                        materialization_manifest=workspace_ctx.materialization_manifest,
                        workspace_artifact_refs=workspace_artifact_refs,
                        git_pushed=git_pushed,
                    ),
                    "loop_signals": execution_run.loop_signals,
                    "loop_detection": execution_run.loop_detection,
                    "approval_decision": execution_run.approval_decision,
                    "execution_repair": execution_repair_meta,
                    "tool_run_refs": tool_run_refs,
                    "runtime_command_rewrite": runtime_command_rewrite,
                    "flow_metrics": self._build_flow_metrics_payload(
                        run_id=str((((task.get("last_proposal") or {}).get("trace") or {}).get("trace_id") or "")),
                        phase="execute",
                        propose_ok=True,
                        execute_ok=execution_run.status == "completed",
                        artifact_created=bool(meaningful_changed_files),
                        worker_profile=worker_profile,
                        profile_source=profile_source,
                        policy_classification=policy_classification_summary,
                    ),
                },
            )
            if execution_run.status == "completed":
                worker_execution_contract = dict(task.get("worker_execution_contract") or {})
                expected_paths = [
                    str(item.get("relative_path") or "").strip()
                    for item in list(worker_execution_contract.get("expected_artifacts") or [])
                    if isinstance(item, dict) and bool(item.get("required", True)) and str(item.get("relative_path") or "").strip()
                ]
                artifact_ids = [str(ref.get("artifact_id") or "").strip() for ref in list(combined_artifact_refs or []) if str(ref.get("artifact_id") or "").strip()]
                produced_paths = {
                    str(ref.get("workspace_relative_path") or "").strip()
                    for ref in list(combined_artifact_refs or [])
                    if isinstance(ref, dict) and str(ref.get("workspace_relative_path") or "").strip()
                }
                missing = [path for path in expected_paths if path not in produced_paths]
                collection_result = {
                    "manifest_valid": not missing,
                    "artifact_ids": artifact_ids,
                    "manifest_id": f"manifest-{tid}",
                    "missing_expected_paths": missing,
                }
                final_status = apply_artifact_first_completion(
                    tid,
                    collection_result=collection_result,
                    advisory_parse_result=None,
                    exit_code=execution_run.exit_code,
                    retry_count=int(execution_run.retries_used or 0),
                    expected_paths=expected_paths,
                    verification_required=bool(expected_paths),
                    allow_synthesized_manifest=False,
                )
                response_payload["status"] = final_status
                response_payload["artifact_completion"] = {
                    "expected_paths": expected_paths,
                    "produced_paths": sorted(produced_paths),
                    "missing_expected_paths": missing,
                    "final_status": final_status,
                }
                goal_output_artifacts = self._register_goal_artifact_outputs(
                    task=task,
                    tid=tid,
                    artifact_refs=list(combined_artifact_refs or []),
                )
                if goal_output_artifacts:
                    response_payload["goal_output_artifacts"] = goal_output_artifacts

            verification_status = dict((task.get("verification_status") or {}))
            source_catalog_status = dict(verification_status.get("source_catalog") or {})
            source_catalog_sources = list(source_catalog_status.get("sources") or [])
            answer_payload = self._extract_grounded_answer_payload(response_payload.get("output"))
            answer_verification = dict(verification_status.get("answer_verification") or {})
            answer_verification.setdefault("answer_schema", "grounded_answer.v1")
            if answer_payload and source_catalog_sources:
                from agent.services.citation_verification_service import get_citation_verification_service

                verification_result = get_citation_verification_service().verify(
                    task_id=str(tid),
                    answer_payload=answer_payload,
                    source_catalog={
                        "schema": "source_catalog.v1",
                        "catalog_id": source_catalog_status.get("source_catalog_id"),
                        "task_id": str(tid),
                        "retrieval_trace_id": source_catalog_status.get("retrieval_trace_id"),
                        "retrieval_context_hash": "",
                        "retrieval_manifest_hash": "",
                        "catalog_hash": source_catalog_status.get("source_catalog_hash") or "0" * 16,
                        "sources": source_catalog_sources,
                    },
                    tool_run_catalog=tool_run_refs,
                )
                answer_verification.update(
                    {
                        "citation_verification_status": verification_result.get("status"),
                        "verified_claim_count": int(verification_result.get("verified_claim_count") or 0),
                        "unverified_claim_count": int(verification_result.get("unverified_claim_count") or 0),
                        "failed_claims": list(verification_result.get("failed_claims") or []),
                        "tool_run_refs": tool_run_refs,
                    }
                )
                if verification_result.get("status") != "verified" and str(response_payload.get("status") or "") == "completed":
                    response_payload["status"] = "failed"
            else:
                answer_verification.setdefault("citation_verification_status", "not_evaluated")
                answer_verification.setdefault("verified_claim_count", 0)
                answer_verification.setdefault("unverified_claim_count", 0)
                answer_verification.setdefault("failed_claims", [])
            verification_status["answer_verification"] = answer_verification
            update_local_task_status(
                tid,
                str(response_payload.get("status") or execution_run.status),
                verification_status=verification_status,
            )

            history_len = len(task.get("history", []) or [])
            _log_terminal_entry(current_app.config["AGENT_NAME"], history_len, "out", command=command, task_id=tid)
            _log_terminal_entry(
                current_app.config["AGENT_NAME"],
                history_len,
                "in",
                output=execution_run.output,
                exit_code=execution_run.exit_code,
                task_id=tid,
            )
            return TaskScopedRouteResponse(data=response_payload)
        finally:
            get_worker_workspace_service().release_output_dir_lock(task=task, workspace_dir=workspace_ctx.workspace_dir)

    @staticmethod
    def _build_domain_action_router() -> DomainActionRouter:
        domain_registry = DomainRegistry()
        descriptors = domain_registry.load()
        capability_registry = CapabilityRegistry()
        capability_registry.load_from_descriptors(descriptors)
        policy_loader = DomainPolicyLoader(capability_registry=capability_registry)
        policy_service = DomainPolicyService(capability_registry=capability_registry)
        bridge_adapter_registry = BridgeAdapterRegistry()
        bridge_adapter_registry.load_from_descriptors(descriptors)
        return DomainActionRouter(
            domain_registry=domain_registry,
            capability_registry=capability_registry,
            policy_loader=policy_loader,
            policy_service=policy_service,
            bridge_adapter_registry=bridge_adapter_registry,
        )

    def _register_goal_artifact_outputs(self, *, task: dict, tid: str, artifact_refs: list[dict]) -> list[dict]:
        goal_id = str((task or {}).get("goal_id") or "").strip()
        if not goal_id:
            return []
        if not list(artifact_refs or []):
            return []
        from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
        from agent.services.config_snapshot_service import ConfigSnapshotService
        from agent.services.prompt_snapshot_service import PromptSnapshotService

        execution_context = dict((task or {}).get("worker_execution_context") or {})
        context_envelope = execution_context.get("context_envelope_ref")
        context_envelope = dict(context_envelope or {}) if isinstance(context_envelope, dict) else {}
        source_usage_refs = [str(item) for item in list(context_envelope.get("source_usage_refs") or []) if str(item).strip()]
        context_artifact_refs = [
            str(item.get("artifact_ref") or item.get("ref") or "").strip()
            for item in list(context_envelope.get("retrieval_refs") or [])
            if isinstance(item, dict)
        ]
        service = GoalArtifactService()
        config_snapshot_service = ConfigSnapshotService()
        prompt_snapshot_service = PromptSnapshotService()
        if context_artifact_refs and not source_usage_refs:
            context_tracking = service.validate_and_record_context_usages(
                goal_id=goal_id,
                artifact_refs=[item for item in context_artifact_refs if item],
                task_id=tid,
                worker_id=str((task or {}).get("assigned_worker_id") or "").strip() or None,
                context_hash=str(context_envelope.get("context_hash") or "").strip() or None,
            )
            source_usage_refs = list(context_tracking.get("source_usage_refs") or [])
        worker_id = str((task or {}).get("assigned_worker_id") or "").strip() or None
        worker_profile = str(execution_context.get("worker_profile") or "default")
        runtime_path = str(((task or {}).get("verification_status") or {}).get("routing", {}).get("runtime_path") or "unknown")
        backend = str(((task or {}).get("verification_status") or {}).get("routing", {}).get("backend") or "unknown")
        model_name = str(((task or {}).get("verification_status") or {}).get("routing", {}).get("inference_model") or "unknown")
        execution_seed = f"{goal_id}:{tid}:{worker_id or 'worker'}"
        execution_id = f"exec-{hashlib.sha1(execution_seed.encode('utf-8')).hexdigest()[:14]}"
        worker_config = config_snapshot_service.build_snapshot(
            config_kind="worker_config",
            source_path_or_ref=f"task:{tid}:worker",
            scope=f"goal:{goal_id}",
            config_payload={"worker_profile": worker_profile, "worker_id": worker_id or "unknown"},
        )
        runtime_config = config_snapshot_service.build_snapshot(
            config_kind="runtime_config",
            source_path_or_ref=f"task:{tid}:runtime",
            scope=f"goal:{goal_id}",
            config_payload={"runtime_path": runtime_path, "backend": backend},
        )
        model_config = config_snapshot_service.build_snapshot(
            config_kind="model_config",
            source_path_or_ref=f"task:{tid}:model",
            scope=f"goal:{goal_id}",
            config_payload={"model": model_name},
        )
        policy_config = config_snapshot_service.build_snapshot(
            config_kind="policy_config",
            source_path_or_ref=f"task:{tid}:policy",
            scope=f"goal:{goal_id}",
            config_payload={"data_boundary": "project_private", "sensitivity": "internal"},
        )
        system_prompt = self._get_system_prompt_for_task(str(tid)) or ""
        prompt_refs: dict[str, Any] = {"no_prompt_reason": "no_prompt_used"} if not system_prompt else {}
        if system_prompt:
            template = prompt_snapshot_service.build_template_snapshot(
                prompt_template_ref=f"prompt-template:{tid}",
                template_path=f"task:{tid}:resolved-template",
                template_version="v1",
                template_text=system_prompt,
                renderer="replace",
                expected_output_schema_ref="worker_response.v1",
            )
            final_prompt = prompt_snapshot_service.build_final_prompt_record(
                prompt_template_ref=template["prompt_template_ref"],
                variables_payload={"task_id": tid, "goal_id": goal_id},
                final_prompt_text=system_prompt,
                context_hash=str(context_envelope.get("context_hash") or "context-hash-missing"),
                input_usage_refs=list(source_usage_refs or []),
                output_schema_ref="worker_response.v1",
                store_raw_prompt=False,
            )
            prompt_refs = {
                "prompt_template_ref": template.get("prompt_template_ref"),
                "prompt_template_version": template.get("template_version"),
                "prompt_template_hash": template.get("template_hash"),
                "prompt_variables_hash": final_prompt.get("variables_hash"),
                "final_prompt_hash": final_prompt.get("final_prompt_hash"),
                "redacted_prompt_ref": final_prompt.get("storage_ref"),
                "raw_prompt_stored": final_prompt.get("raw_prompt_stored"),
            }
        provenance = {
            "schema": "execution_provenance.v1",
            "provenance_id": f"prov-{hashlib.sha1(f'{goal_id}:{tid}:{execution_id}'.encode('utf-8')).hexdigest()[:16]}",
            "goal_id": goal_id,
            "task_id": str(tid),
            "execution_id": execution_id,
            "worker_id": str(worker_id or "worker-unknown"),
            "worker_kind": "native",
            "runtime_target_ref": {"runtime_type": backend, "location": "local", "snapshot_id": runtime_config.get("config_snapshot_id")},
            "model_ref": {"provider_id": backend, "model_id": model_name},
            "config_refs": {
                "worker_config_ref": worker_config.get("config_snapshot_id"),
                "runtime_config_ref": runtime_config.get("config_snapshot_id"),
                "model_config_ref": model_config.get("config_snapshot_id"),
                "policy_config_ref": policy_config.get("config_snapshot_id"),
            },
            "prompt_refs": prompt_refs,
            "input_usage_refs": list(source_usage_refs or []),
            "output_artifact_refs": [
                str(item.get("artifact_id") or item.get("trace_bundle_ref") or item.get("workspace_relative_path") or "")
                for item in list(artifact_refs or [])
                if isinstance(item, dict)
            ],
            "created_at": _now_iso(),
        }
        try:
            return service.register_output_artifacts_from_refs(
                goal_id=goal_id,
                task_id=str(tid),
                worker_id=worker_id,
                artifact_refs=list(artifact_refs or []),
                input_usage_refs=source_usage_refs,
                execution_provenance=provenance,
            )
        except GoalArtifactServiceError as exc:
            return [
                {
                    "status": "failed",
                    "reason_code": exc.reason_code,
                    "detail": exc.detail,
                }
            ]

    @staticmethod
    def _resolve_domain_action_payload(*, task: dict, command: str | None) -> dict:
        command_text = str(command or "").strip()
        inline_payload = None
        if command_text:
            try:
                parsed = json.loads(command_text)
            except json.JSONDecodeError as exc:
                raise TaskConflictError(
                    "domain_action_payload_invalid",
                    details={"reason": "command_must_be_valid_json_object", "error": str(exc)},
                )
            if not isinstance(parsed, dict):
                raise TaskConflictError(
                    "domain_action_payload_invalid",
                    details={"reason": "command_must_be_json_object"},
                )
            inline_payload = dict(parsed)
        payload = inline_payload or dict(task.get("domain_action_request") or {})
        if not payload:
            raise TaskConflictError(
                "domain_action_payload_missing",
                details={"reason": "provide_json_command_or_domain_action_request"},
            )
        required = ("domain_id", "capability_id", "action_id")
        missing = [key for key in required if not str(payload.get(key) or "").strip()]
        if missing:
            raise TaskConflictError(
                "domain_action_payload_invalid",
                details={"reason": "missing_required_fields", "fields": missing},
            )
        return payload

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
        payload = self._resolve_domain_action_payload(task=task, command=command)
        route_result = self._build_domain_action_router().route(
            domain_id=str(payload.get("domain_id") or "").strip(),
            capability_id=str(payload.get("capability_id") or "").strip(),
            action_id=str(payload.get("action_id") or "").strip(),
            execution_mode=str(payload.get("execution_mode") or "execute").strip() or "execute",
            context_summary=dict(payload.get("context_summary") or {}),
            actor_metadata=dict(payload.get("actor_metadata") or {}),
            approval=dict(payload.get("approval") or {}) if isinstance(payload.get("approval"), dict) else None,
        )
        route = route_result.as_dict()

        state = str(route.get("state") or "").strip().lower()
        if state in {"plan", "execution_started"}:
            status = "completed"
            exit_code = 0
            failure_type = "success"
        elif state == "approval_required":
            status = "blocked"
            exit_code = 1
            failure_type = "approval_required"
        elif state == "denied":
            status = "failed"
            exit_code = 1
            failure_type = "policy_denied"
        else:
            status = "failed"
            exit_code = 1
            failure_type = "degraded"

        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=task_kind,
            policy_version="domain_action_router_v1",
            metadata={
                "task_id": tid,
                "domain_id": route.get("domain_id"),
                "capability_id": route.get("capability_id"),
                "action_id": route.get("action_id"),
            },
        )
        append_stage(
            pipeline,
            name="domain_action_route",
            status="ok" if status == "completed" else "failed",
            metadata={"route_state": state, "route_reason": route.get("reason")},
        )
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=task_kind,
            backend="domain_action_router",
            requested_backend="domain_action_router",
            routing_reason="domain_action_router",
            policy_version="domain_action_router_v1",
            metadata={
                "source": "domain_action_execute",
                "domain_action_route": route,
            },
        )
        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status=status,
            reason=reason or "Domain action routed",
            command=command,
            tool_calls=request_data.tool_calls if isinstance(getattr(request_data, "tool_calls", None), list) else None,
            output=json.dumps(route, ensure_ascii=False),
            exit_code=exit_code,
            retries_used=0,
            retry_history=[],
            failure_type=failure_type,
            execution_duration_ms=0,
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            extra_history={
                "domain_action_route": route,
                "domain_action_payload": payload,
            },
        )
        return TaskScopedRouteResponse(data=response_payload)

    def _propose_task_with_comparisons(
        self,
        *,
        tid: str,
        task: dict,
        request_data,
        prompt: str,
        base_prompt: str,
        worker_context_meta: dict,
        research_context: dict | None,
        cli_runner: Callable,
        cfg: dict,
    ) -> TaskScopedRouteResponse:
        task_kind = normalize_task_kind(None, base_prompt)
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        requested_temperature = self._normalize_temperature(getattr(request_data, "temperature", None))
        timeout = self._resolve_task_propose_timeout(cfg, task_kind)
        compare_policy = resolve_execution_policy(
            TaskStepExecuteRequest(timeout=timeout),
            agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
            source="task_propose_compare",
        )
        routing_policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]

        def _run_single_provider(provider_entry: str) -> tuple[str, dict]:
            entry = str(provider_entry or "").strip()
            if not entry:
                return provider_entry, {"error": "invalid_provider_entry"}

            parts = entry.split(":", 1)
            requested_backend = str(parts[0] or "").strip().lower()
            selected_model = self._resolve_requested_model(
                agent_cfg=cfg,
                requested_model=(parts[1].strip() if len(parts) > 1 else "") or request_data.model,
            )
            if requested_backend not in SUPPORTED_CLI_BACKENDS:
                return entry, {"error": f"unsupported_backend:{requested_backend}", "backend": requested_backend}

            effective_backend, routing_reason = self._resolve_cli_backend(
                task_kind,
                requested_backend=requested_backend,
                agent_cfg=cfg,
                required_capabilities=derive_required_capabilities(task, task_kind),
            )
            started_at = time.time()
            cli_kwargs = {
                "prompt": prompt,
                "options": ["--no-interaction"],
                "timeout": compare_policy.timeout_seconds,
                "backend": effective_backend,
                "model": selected_model,
                "routing_policy": {"mode": "adaptive", "task_kind": task_kind, "policy_version": routing_policy_version},
                "workdir": str(workspace_context.workspace_dir),
            }
            if requested_temperature is not None:
                cli_kwargs["temperature"] = requested_temperature
            if research_context:
                cli_kwargs["research_context"] = research_context
            rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(cli_runner, **cli_kwargs)
            latency_ms = int((time.time() - started_at) * 1000)
            raw_res, output_source = self._coalesce_cli_output(cli_out, cli_err)
            required_capabilities = derive_required_capabilities(task, task_kind)
            routing_dimensions = self._routing_dimensions(
                backend_used=backend_used,
                model=selected_model,
                temperature=requested_temperature,
                requested_backend=requested_backend,
                agent_cfg=cfg,
                worker_profile=worker_context_meta.get("worker_profile"),
                profile_source=worker_context_meta.get("profile_source"),
            )
            routing = {
                "task_kind": task_kind,
                "effective_backend": effective_backend,
                "reason": routing_reason,
                "policy_classification_summary": str(routing_reason or "").strip().lower() or None,
                "required_capabilities": required_capabilities,
                "research_specialization": derive_research_specialization(task, task_kind, required_capabilities),
                **routing_dimensions,
            }
            cli_result = {
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "llm_call_profile": self._build_llm_call_profile_entries(
                    backend_used=backend_used,
                    model=selected_model,
                    prompt=prompt_for_cli,
                    raw_output=raw_res,
                    latency_ms=latency_ms,
                    rc=rc,
                    repair_attempted=False,
                    repair_backend=None,
                    repair_model=None,
                ),
            }
            if rc != 0 and not raw_res.strip():
                return entry, {"error": cli_err or f"backend '{backend_used}' failed with exit code {rc}", "backend": backend_used, "routing": routing, "cli_result": cli_result}
            if not raw_res:
                return entry, {"error": "empty_response", "backend": backend_used, "routing": routing, "cli_result": cli_result}
            if is_research_backend(backend_used):
                research_res = self._build_research_result(
                    raw_res,
                    backend_used,
                    tid,
                    rc,
                    cli_err,
                    latency_ms,
                    output_source=output_source,
                    research_context=research_context,
                )
                research_res["model"] = selected_model
                research_res["routing"] = routing
                return entry, research_res
            command, tool_calls = self._extract_structured_action_fields(raw_res)
            return entry, {
                "reason": _extract_reason(raw_res),
                "command": command,
                "tool_calls": tool_calls,
                "raw": raw_res,
                "backend": backend_used,
                "model": selected_model,
                "routing": routing,
                "cli_result": cli_result,
            }

        results: dict[str, dict] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(request_data.providers))) as executor:
            futures = {executor.submit(_run_single_provider, provider_name): provider_name for provider_name in request_data.providers}
            for future in concurrent.futures.as_completed(futures):
                requested = futures[future]
                try:
                    provider_key, provider_result = future.result()
                    results[provider_key or requested] = provider_result
                except Exception as exc:
                    current_app.logger.error("Multi-Provider CLI Call for %s failed: %s", requested, exc)
                    results[requested] = {"error": str(exc)}

        successful_results = [
            results.get(provider_name)
            for provider_name in request_data.providers
            if isinstance(results.get(provider_name), dict) and not results.get(provider_name).get("error")
        ]
        if not successful_results:
            return TaskScopedRouteResponse(
                status="error",
                message="all_llm_failed",
                data={"comparisons": results},
                code=502,
            )

        main_res = results.get(request_data.providers[0])
        if not isinstance(main_res, dict) or main_res.get("error"):
            main_res = successful_results[0]

        trace = build_trace_record(
            task_id=tid,
            event_type="proposal_result",
            task_kind=(main_res.get("routing") or {}).get("task_kind"),
            backend=main_res.get("backend"),
            requested_backend=request_data.providers[0] if request_data.providers else "auto",
            routing_reason=((main_res.get("routing") or {}).get("reason")),
            policy_version=routing_policy_version,
            metadata={**worker_context_meta, "source": "task_propose_multi", "comparison_count": len(results)},
        )
        review = self._build_review_state(
            current_app.config.get("AGENT_CONFIG", {}) or {},
            backend=str(main_res.get("backend") or ""),
            task_kind=str(((main_res.get("routing") or {}).get("task_kind") or "")),
            command=main_res.get("command"),
            tool_calls=main_res.get("tool_calls"),
        )
        response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=main_res.get("reason"),
            raw=main_res.get("raw"),
            backend=main_res.get("backend"),
            model=main_res.get("model"),
            routing=main_res.get("routing"),
            cli_result=main_res.get("cli_result"),
            worker_context=worker_context_meta,
            trace=trace,
            review=review,
            comparisons=results,
            command=main_res.get("command"),
            tool_calls=main_res.get("tool_calls"),
            research_artifact=main_res.get("research_artifact"),
            research_context=main_res.get("research_context"),
            history_event={
                "event_type": "proposal_result",
                "reason": main_res.get("reason"),
                "backend": main_res.get("backend"),
                "routing_reason": ((main_res.get("routing") or {}).get("reason")),
                "latency_ms": int((main_res.get("cli_result") or {}).get("latency_ms") or 0),
                "returncode": int((main_res.get("cli_result") or {}).get("returncode") or 0),
                "comparison_count": len(results),
                "pipeline": None,
                "trace": trace,
                "flow_metrics": self._build_flow_metrics_payload(
                    run_id=str(trace.get("trace_id") or ""),
                    phase="propose",
                    propose_ok=True,
                    execute_ok=None,
                    artifact_created=None,
                    worker_profile=worker_context_meta.get("worker_profile"),
                    profile_source=worker_context_meta.get("profile_source"),
                    policy_classification=str(((main_res.get("routing") or {}).get("reason")) or ""),
                ),
            },
        )
        return TaskScopedRouteResponse(data=response_payload)

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
    ) -> TaskScopedRouteResponse:
        # FA-T003: Inventory: This legacy path maps to "flexible_llm_normalization" strategy.
        # Block until ProposeStrategyOrchestrator delegates.
        raise NotImplementedError(
            "FA-T003: Ungoverned legacy propose path blocked. "
            "Delegate to ProposeStrategyOrchestrator(policy).run() with strategy_id='flexible_llm_normalization'."
        )
        timeout = self._resolve_task_propose_timeout(cfg, task_kind)
        proposal_model = self._resolve_requested_model(agent_cfg=cfg, requested_model=request_data.model)
        policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]
        session_payload = self._prepare_task_cli_session(
            tid=tid,
            task=task,
            backend=effective_backend,
            model=proposal_model,
            agent_cfg=cfg,
        )
        interactive_terminal_session = effective_backend == "opencode" and self._is_interactive_terminal_session(session_payload)
        interactive_context_profile = (
            self._resolve_interactive_context_profile(cfg, retry=False) if interactive_terminal_session else None
        )
        effective_research_context = (
            self._compact_research_context(research_context, profile=interactive_context_profile)
            if interactive_terminal_session
            else research_context
        )
        if interactive_terminal_session:
            timeout = self._resolve_interactive_propose_timeout(cfg, fallback=timeout)
        prompt_for_cli, worker_context_meta = self._build_task_propose_prompt(
            tid=tid,
            task=task,
            base_prompt=base_prompt,
            tool_definitions_resolver=(lambda *_args, **_kwargs: [])
            if interactive_terminal_session
            else tool_definitions_resolver,
            research_context=effective_research_context,
            interactive_terminal=interactive_terminal_session,
            context_profile=interactive_context_profile,
        )
        pipeline = new_pipeline_trace(
            pipeline="task_propose",
            task_kind=task_kind,
            policy_version=policy_version,
            metadata={"task_id": tid, "requested_backend": "auto", **worker_context_meta},
        )
        append_stage(
            pipeline,
            name="route",
            status="ok",
            metadata={"effective_backend": effective_backend, "reason": routing_reason},
        )
        requested_temperature = self._normalize_temperature(getattr(request_data, "temperature", None))
        if requested_temperature is not None:
            prompt_for_cli = (
                f"{prompt_for_cli}\n\n"
                f"[Sampling-Hinweis]\n"
                f"Ziel-Temperatur fuer diese Antwort: {requested_temperature:.2f}\n"
                + (
                    "Arbeite im sichtbaren OpenCode-Terminal direkt im Workspace."
                    if interactive_terminal_session
                    else "Behalte strikt das JSON-Output-Schema ein."
                )
            )
        if session_payload and not self._has_native_opencode_runtime(session_payload) and not interactive_terminal_session:
            prompt_for_cli = (
                get_cli_session_service().build_prompt_with_history(
                    session_id=session_payload["id"],
                    prompt=prompt_for_cli,
                    max_turns=int(session_payload.get("max_turns_per_session") or 40),
                )
                or prompt_for_cli
            )
        started_at = time.time()
        cli_kwargs = {
            "prompt": prompt_for_cli,
            "options": ["--no-interaction"],
            "timeout": timeout,
            "backend": effective_backend,
            "model": proposal_model,
            "routing_policy": {"mode": "adaptive", "task_kind": task_kind, "policy_version": policy_version},
            "session": session_payload,
            "workdir": str(workspace_context.workspace_dir),
        }
        if requested_temperature is not None:
            cli_kwargs["temperature"] = requested_temperature
        if effective_research_context:
            cli_kwargs["research_context"] = effective_research_context
        rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(cli_runner, **cli_kwargs)
        latency_ms = int((time.time() - started_at) * 1000)
        raw_res, output_source = self._coalesce_cli_output(cli_out, cli_err)
        repair_meta = {"attempted": False, "backend": None, "model": None}
        interactive_retry_meta = {"attempted": False, "timeout": None, "latency_ms": None}
        append_stage(
            pipeline,
            name="execute",
            status=(
                "ok"
                if (rc == 0 if interactive_terminal_session else (rc == 0 or bool(raw_res)))
                else "error"
            ),
            metadata={
                "backend_used": backend_used,
                "returncode": rc,
                "latency_ms": latency_ms,
                "output_source": output_source,
            },
            started_at=started_at,
        )
        if interactive_terminal_session and backend_used == "opencode":
            timeout_like_failure = self._interactive_timeout_like_failure(rc=rc, output=raw_res, stderr=cli_err)
            if timeout_like_failure:
                retry_profile = self._resolve_interactive_context_profile(cfg, retry=True)
                retry_research_context = self._compact_research_context(research_context, profile=retry_profile)
                retry_prompt, retry_worker_meta = self._build_task_propose_prompt(
                    tid=tid,
                    task=task,
                    base_prompt=base_prompt,
                    tool_definitions_resolver=(lambda *_args, **_kwargs: []),
                    research_context=retry_research_context,
                    interactive_terminal=True,
                    context_profile=retry_profile,
                )
                if requested_temperature is not None:
                    retry_prompt = (
                        f"{retry_prompt}\n\n"
                        f"[Sampling-Hinweis]\n"
                        f"Ziel-Temperatur fuer diese Antwort: {requested_temperature:.2f}\n"
                        "Arbeite im sichtbaren OpenCode-Terminal direkt im Workspace."
                    )
                retry_timeout = self._resolve_interactive_retry_timeout(cfg, fallback=timeout)
                retry_kwargs = {
                    **cli_kwargs,
                    "prompt": retry_prompt,
                    "timeout": retry_timeout,
                }
                if retry_research_context:
                    retry_kwargs["research_context"] = retry_research_context
                started_retry = time.time()
                retry_rc, retry_out, retry_err, retry_backend = self._invoke_cli_runner(cli_runner, **retry_kwargs)
                retry_latency_ms = int((time.time() - started_retry) * 1000)
                retry_raw, retry_source = self._coalesce_cli_output(retry_out, retry_err)
                interactive_retry_meta = {
                    "attempted": True,
                    "timeout": retry_timeout,
                    "latency_ms": retry_latency_ms,
                }
                append_stage(
                    pipeline,
                    name="interactive_retry",
                    status="ok" if retry_rc == 0 else "error",
                    metadata={
                        "backend_used": retry_backend,
                        "returncode": retry_rc,
                        "latency_ms": retry_latency_ms,
                        "output_source": retry_source,
                        "timeout": retry_timeout,
                    },
                    started_at=started_retry,
                )
                rc = retry_rc
                cli_err = retry_err
                cli_out = retry_out
                raw_res = retry_raw
                output_source = retry_source
                backend_used = retry_backend
                latency_ms += retry_latency_ms
                prompt_for_cli = retry_prompt
                worker_context_meta = retry_worker_meta
                effective_research_context = retry_research_context
        if not interactive_terminal_session and rc != 0 and not raw_res.strip():
            repaired = self._repair_task_proposal(
                cli_runner=cli_runner,
                prompt=prompt_for_cli,
                bad_output=(cli_err or ""),
                validation_error="empty_or_failed_cli_response",
                timeout=timeout,
                task_kind=task_kind,
                policy_version=policy_version,
                cfg=cfg,
                primary_backend=backend_used,
                primary_model=proposal_model,
                primary_temperature=requested_temperature,
                research_context=effective_research_context,
                session=session_payload,
                workdir=str(workspace_context.workspace_dir),
            )
            if repaired:
                raw_res = repaired["raw"]
                output_source = repaired["output_source"]
                backend_used = repaired["backend_used"]
                rc = int(repaired["rc"])
                cli_err = str(repaired.get("stderr") or "")
                repair_meta = {
                    "attempted": True,
                    "backend": repaired["backend_used"],
                    "model": repaired.get("model"),
                }
            else:
                return TaskScopedRouteResponse(
                    status="error",
                    message="llm_cli_failed",
                    data={"details": cli_err or f"backend '{backend_used}' failed with exit code {rc}", "backend": backend_used},
                    code=502,
                )
        if not interactive_terminal_session and not raw_res:
            repaired = self._repair_task_proposal(
                cli_runner=cli_runner,
                prompt=prompt_for_cli,
                bad_output=(cli_err or ""),
                validation_error="empty_cli_response",
                timeout=timeout,
                task_kind=task_kind,
                policy_version=policy_version,
                cfg=cfg,
                primary_backend=backend_used,
                primary_model=proposal_model,
                primary_temperature=requested_temperature,
                research_context=effective_research_context,
                session=session_payload,
                workdir=str(workspace_context.workspace_dir),
            )
            if repaired:
                raw_res = repaired["raw"]
                output_source = repaired["output_source"]
                backend_used = repaired["backend_used"]
                rc = int(repaired["rc"])
                cli_err = str(repaired.get("stderr") or "")
                repair_meta = {
                    "attempted": True,
                    "backend": repaired["backend_used"],
                    "model": repaired.get("model"),
                }
            else:
                return TaskScopedRouteResponse(status="error", message="llm_failed", data={}, code=502)

        routing = {
            "task_kind": task_kind,
            "effective_backend": effective_backend,
            "reason": routing_reason,
            "policy_classification_summary": str(routing_reason or "").strip().lower() or None,
            "required_capabilities": required_capabilities,
            "research_specialization": research_specialization,
            **self._routing_dimensions(
                backend_used=backend_used,
                model=proposal_model,
                temperature=requested_temperature,
                requested_backend="auto",
                agent_cfg=cfg,
                worker_profile=worker_context_meta.get("worker_profile"),
                profile_source=worker_context_meta.get("profile_source"),
            ),
        }
        if session_payload:
            routing["session_mode"] = "stateful"
            routing["session_id"] = session_payload["id"]
            routing["session_reused"] = bool(session_payload.get("session_reused"))
            session_metadata = session_payload.get("metadata") if isinstance(session_payload.get("metadata"), dict) else {}
            live_terminal_meta = (
                dict(session_metadata.get("opencode_live_terminal") or {})
                if isinstance(session_metadata.get("opencode_live_terminal"), dict)
                else {}
            )
            config_execution_mode = self._resolve_opencode_execution_mode(cfg)
            if (
                str(session_metadata.get("opencode_execution_mode") or "").strip().lower() in {"live_terminal", "interactive_terminal"}
                or (effective_backend == "opencode" and config_execution_mode in {"live_terminal", "interactive_terminal"})
            ):
                routing["execution_mode"] = str(session_metadata.get("opencode_execution_mode") or config_execution_mode).strip().lower()
                if not live_terminal_meta:
                    live_terminal_meta = (
                        dict((task.get("verification_status") or {}).get("opencode_live_terminal") or {})
                        if isinstance((task.get("verification_status") or {}).get("opencode_live_terminal"), dict)
                        else {}
                    )
                routing["live_terminal"] = live_terminal_meta
        if interactive_terminal_session and backend_used == "opencode":
            timeout_like_failure = self._interactive_timeout_like_failure(rc=rc, output=raw_res, stderr=cli_err)
            if rc != 0 or timeout_like_failure:
                flow_metrics = self._build_flow_metrics_payload(
                    run_id=str(session_payload.get("id") or "") if isinstance(session_payload, dict) else None,
                    phase="propose",
                    propose_ok=False,
                    execute_ok=None,
                    artifact_created=None,
                    worker_profile=worker_context_meta.get("worker_profile"),
                    profile_source=worker_context_meta.get("profile_source"),
                    policy_classification=str(routing_reason or "").strip().lower() or None,
                )
                self._update_task_flow_metrics(tid=tid, task=task, flow_metrics=flow_metrics)
                return TaskScopedRouteResponse(
                    status="error",
                    message="llm_cli_failed",
                    data={
                        "details": cli_err or raw_res or f"backend '{backend_used}' failed with exit code {rc}",
                        "backend": backend_used,
                        "flow_metrics": flow_metrics,
                        "retry": interactive_retry_meta,
                    },
                    code=502,
                )
        if is_research_backend(backend_used):
            research_res = self._build_research_result(
                raw_res,
                backend_used,
                tid,
                rc,
                cli_err,
                latency_ms,
                output_source=output_source,
                research_context=effective_research_context,
            )
            trace = build_trace_record(
                task_id=tid,
                event_type="proposal_result",
                task_kind=task_kind,
                backend=backend_used,
                requested_backend="auto",
                routing_reason=routing_reason,
                policy_version=policy_version,
                metadata={**worker_context_meta, "source": "task_propose", "artifact_kind": "research_report"},
            )
            pipeline_payload = {**pipeline, "trace_id": trace["trace_id"]}
            response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
                tid=tid,
                task=task,
                reason=research_res.get("reason"),
                raw=raw_res,
                backend=backend_used,
                model=proposal_model,
                routing=routing,
                cli_result=research_res.get("cli_result"),
                worker_context=worker_context_meta,
                trace=trace,
                review=self._build_review_state(
                    current_app.config.get("AGENT_CONFIG", {}) or {},
                    backend_used,
                    task_kind,
                    command=None,
                    tool_calls=None,
                ),
                pipeline=pipeline_payload,
                research_artifact=research_res.get("research_artifact"),
                research_context=effective_research_context,
                history_event={
                    "event_type": "proposal_result",
                    "reason": research_res.get("reason"),
                    "backend": backend_used,
                    "routing_reason": routing_reason,
                    "latency_ms": latency_ms,
                    "returncode": rc,
                    "artifact_kind": "research_report",
                    "source_count": len((research_res.get("research_artifact") or {}).get("sources") or []),
                    "pipeline": pipeline_payload,
                    "trace": trace,
                    "flow_metrics": self._build_flow_metrics_payload(
                        run_id=str(trace.get("trace_id") or ""),
                        phase="propose",
                        propose_ok=True,
                        execute_ok=None,
                        artifact_created=None,
                        worker_profile=worker_context_meta.get("worker_profile"),
                        profile_source=worker_context_meta.get("profile_source"),
                        policy_classification=str(routing_reason or "").strip().lower() or None,
                    ),
                },
            )
            if session_payload:
                turn = get_cli_session_service().append_turn(
                    session_id=session_payload["id"],
                    prompt=prompt_for_cli,
                    output=raw_res,
                    model=proposal_model,
                    trace_id=str(trace.get("trace_id") or ""),
                    metadata={"backend_used": backend_used, "task_id": tid, "proposal_mode": "research"},
                )
                if isinstance(turn, dict):
                    response_payload.setdefault("routing", {})
                    response_payload["routing"]["session_turn_id"] = turn.get("id")
            return TaskScopedRouteResponse(data=response_payload)

        if interactive_terminal_session and backend_used == "opencode":
            reason = "Interactive OpenCode session finished; finalize workspace changes"
            append_stage(
                pipeline,
                name="parse",
                status="ok",
                metadata={"interactive_terminal_finalize": True, "has_command": True, "tool_call_count": 0},
            )
            trace = build_trace_record(
                task_id=tid,
                event_type="proposal_result",
                task_kind=task_kind,
                backend=backend_used,
                requested_backend="auto",
                routing_reason=routing_reason,
                policy_version=policy_version,
                metadata={**worker_context_meta, "source": "task_propose", "interactive_terminal": True},
            )
            pipeline_payload = {**pipeline, "trace_id": trace["trace_id"]}
            cli_result = {
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "repair_attempted": bool(interactive_retry_meta.get("attempted")),
                "repair_backend": backend_used if interactive_retry_meta.get("attempted") else None,
                "repair_model": proposal_model if interactive_retry_meta.get("attempted") else None,
            }
            response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
                tid=tid,
                task=task,
                reason=reason,
                raw=raw_res,
                backend=backend_used,
                model=proposal_model,
                routing=routing,
                cli_result=cli_result,
                worker_context=worker_context_meta,
                trace=trace,
                review=self._build_review_state(
                    current_app.config.get("AGENT_CONFIG", {}) or {},
                    backend_used,
                    task_kind,
                    command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
                    tool_calls=None,
                ),
                pipeline=pipeline_payload,
                command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
                tool_calls=None,
                history_event={
                    "event_type": "proposal_result",
                    "reason": reason,
                    "backend": backend_used,
                    "routing_reason": routing_reason,
                    "latency_ms": latency_ms,
                    "returncode": rc,
                    "interactive_terminal": True,
                    "pipeline": pipeline_payload,
                    "trace": trace,
                    "flow_metrics": self._build_flow_metrics_payload(
                        run_id=str(trace.get("trace_id") or ""),
                        phase="propose",
                        propose_ok=True,
                        execute_ok=None,
                        artifact_created=None,
                        worker_profile=worker_context_meta.get("worker_profile"),
                        profile_source=worker_context_meta.get("profile_source"),
                        policy_classification=str(routing_reason or "").strip().lower() or None,
                    ),
                },
            )
            if session_payload:
                turn = get_cli_session_service().append_turn(
                    session_id=session_payload["id"],
                    prompt=prompt_for_cli,
                    output=raw_res,
                    model=proposal_model,
                    trace_id=str(trace.get("trace_id") or ""),
                    metadata={"backend_used": backend_used, "task_id": tid, "proposal_mode": "interactive_terminal"},
                )
                if isinstance(turn, dict):
                    response_payload.setdefault("routing", {})
                    response_payload["routing"]["session_turn_id"] = turn.get("id")
            _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt_for_cli, task_id=tid)
            _log_terminal_entry(
                current_app.config["AGENT_NAME"],
                0,
                "out",
                reason=reason,
                command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
                tool_calls=None,
                task_id=tid,
            )
            return TaskScopedRouteResponse(data=response_payload)

        reason = _extract_reason(raw_res)
        command, tool_calls = self._extract_structured_action_fields(raw_res)
        if not command and not tool_calls:
            repaired = self._repair_task_proposal(
                cli_runner=cli_runner,
                prompt=prompt_for_cli,
                bad_output=raw_res,
                validation_error="missing_required_fields: command_or_tool_calls",
                timeout=timeout,
                task_kind=task_kind,
                policy_version=policy_version,
                cfg=cfg,
                primary_backend=backend_used,
                primary_model=proposal_model,
                primary_temperature=requested_temperature,
                research_context=effective_research_context,
                session=session_payload,
                workdir=str(workspace_context.workspace_dir),
            )
            if repaired:
                raw_res = repaired["raw"]
                output_source = repaired["output_source"]
                backend_used = repaired["backend_used"]
                rc = int(repaired["rc"])
                cli_err = str(repaired.get("stderr") or "")
                reason = _extract_reason(raw_res)
                repair_meta = {
                    "attempted": True,
                    "backend": repaired["backend_used"],
                    "model": repaired.get("model"),
                }
                command, tool_calls = self._extract_structured_action_fields(raw_res)
        policy_classification_summary = str(routing_reason or "").strip().lower() or None
        if backend_used == "ananta-worker" and self._native_worker_runtime_enabled(cfg):
            native_plan = get_native_worker_runtime_service().prepare_native_command_plan(
                tid=tid,
                task=task,
                command=command,
                reason=reason,
                worker_profile=worker_context_meta.get("worker_profile"),
                profile_source=worker_context_meta.get("profile_source"),
                trace_id=str((session_payload or {}).get("id") or ""),
                context_bundle_id=worker_context_meta.get("context_bundle_id"),
                agent_cfg=cfg,
            )
            worker_context_meta.update(dict(native_plan.get("worker_context_updates") or {}))
            runtime_path = str(native_plan.get("runtime_path") or "").strip().lower()
            if runtime_path:
                routing["worker_runtime_path"] = runtime_path
            policy_classification_summary = str(native_plan.get("policy_classification_summary") or policy_classification_summary or "").strip().lower() or None
            if policy_classification_summary:
                routing["policy_classification_summary"] = policy_classification_summary
        append_stage(
            pipeline,
            name="parse",
            status="ok",
            metadata={"has_command": bool(command), "tool_call_count": len(tool_calls or [])},
        )
        trace = build_trace_record(
            task_id=tid,
            event_type="proposal_result",
            task_kind=task_kind,
            backend=backend_used,
            requested_backend="auto",
            routing_reason=routing_reason,
            policy_version=policy_version,
            metadata={**worker_context_meta, "source": "task_propose"},
        )
        pipeline_payload = {**pipeline, "trace_id": trace["trace_id"]}
        response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=reason,
            raw=raw_res,
            backend=backend_used,
            model=proposal_model,
            routing=routing,
            cli_result={
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "repair_attempted": bool(repair_meta["attempted"]),
                "repair_backend": repair_meta["backend"],
                "repair_model": repair_meta["model"],
                "llm_call_profile": self._build_llm_call_profile_entries(
                    backend_used=backend_used,
                    model=proposal_model,
                    prompt=prompt_for_cli,
                    raw_output=raw_res,
                    latency_ms=latency_ms,
                    rc=rc,
                    repair_attempted=bool(repair_meta["attempted"]),
                    repair_backend=repair_meta["backend"],
                    repair_model=repair_meta["model"],
                ),
            },
            worker_context=worker_context_meta,
            trace=trace,
            review=self._build_review_state(
                current_app.config.get("AGENT_CONFIG", {}) or {},
                backend_used,
                task_kind,
                command=command,
                tool_calls=tool_calls,
            ),
            pipeline=pipeline_payload,
            command=command,
            tool_calls=tool_calls,
            history_event={
                "event_type": "proposal_result",
                "reason": reason,
                "backend": backend_used,
                "routing_reason": routing_reason,
                "latency_ms": latency_ms,
                "returncode": rc,
                "pipeline": pipeline_payload,
                "trace": trace,
                "flow_metrics": self._build_flow_metrics_payload(
                    run_id=str(trace.get("trace_id") or ""),
                    phase="propose",
                    propose_ok=True,
                    execute_ok=None,
                    artifact_created=None,
                    worker_profile=worker_context_meta.get("worker_profile"),
                    profile_source=worker_context_meta.get("profile_source"),
                    policy_classification=policy_classification_summary,
                ),
            },
        )
        if session_payload:
            turn = get_cli_session_service().append_turn(
                session_id=session_payload["id"],
                prompt=prompt_for_cli,
                output=raw_res,
                model=proposal_model,
                trace_id=str(trace.get("trace_id") or ""),
                metadata={"backend_used": backend_used, "task_id": tid, "proposal_mode": "command"},
            )
            if isinstance(turn, dict):
                response_payload.setdefault("routing", {})
                response_payload["routing"]["session_turn_id"] = turn.get("id")
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt_for_cli, task_id=tid)
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid)
        return TaskScopedRouteResponse(data=response_payload)

    def _finalize_interactive_terminal_execution(
        self,
        *,
        tid: str,
        task: dict,
        reason: str,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        workspace_ctx = get_worker_workspace_service().resolve_workspace_context(task=task)
        changed_files = get_worker_workspace_service().detect_changed_files_against_interactive_baseline(
            workspace_dir=workspace_ctx.workspace_dir
        )
        meaningful_changed_files = get_worker_workspace_service().filter_meaningful_changed_files(changed_files)
        workspace_artifact_refs = get_worker_workspace_service().sync_changed_files_to_artifacts(
            task_id=tid,
            task=task,
            workspace_dir=workspace_ctx.workspace_dir,
            changed_rel_paths=changed_files,
            sync_cfg=workspace_ctx.artifact_sync,
        )
        diff_artifact_ref = get_worker_workspace_service().create_workspace_diff_artifact(
            task_id=tid,
            task=task,
            workspace_dir=workspace_ctx.workspace_dir,
            changed_rel_paths=changed_files,
            sync_cfg=workspace_ctx.artifact_sync,
        )
        artifact_refs = list(workspace_artifact_refs or [])
        if diff_artifact_ref:
            artifact_refs.append(diff_artifact_ref)
        proposal_meta = dict(task.get("last_proposal") or {})
        cli_result = proposal_meta.get("cli_result") if isinstance(proposal_meta.get("cli_result"), dict) else {}
        exit_code = int(cli_result.get("returncode") or 0)
        status = "completed" if exit_code == 0 else "failed"
        output_lines = [
            "Interactive OpenCode session finalized.",
            f"Workspace: {workspace_ctx.workspace_dir}",
            f"Changed files: {len(changed_files)}",
        ]
        if changed_files:
            output_lines.extend(f"- {rel}" for rel in changed_files[:50])
        else:
            output_lines.append("No tracked workspace changes detected.")
        if diff_artifact_ref:
            output_lines.append(f"Diff artifact: {diff_artifact_ref.get('artifact_id')}")
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
            backend=proposal_meta.get("backend"),
            requested_backend=proposal_meta.get("backend"),
            routing_reason=((proposal_meta.get("routing") or {}).get("reason")),
            policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
            metadata={
                "interactive_terminal_finalize": True,
                "changed_file_count": len(changed_files),
                "meaningful_changed_file_count": len(meaningful_changed_files),
                "workspace_artifact_count": len(artifact_refs),
            },
        )
        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
            policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
            metadata={"task_id": tid, "interactive_terminal_finalize": True},
        )
        append_stage(
            pipeline,
            name="interactive_terminal_finalize",
            status="ok" if exit_code == 0 else "error",
            metadata={
                "changed_file_count": len(changed_files),
                "meaningful_changed_file_count": len(meaningful_changed_files),
                "workspace_artifact_count": len(artifact_refs),
                "exit_code": exit_code,
            },
        )
        output = "\n".join(output_lines)
        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status=status,
            reason=reason or "Interactive OpenCode session finalized",
            command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
            tool_calls=None,
            output=output,
            exit_code=exit_code,
            retries_used=0,
            retry_history=[],
            failure_type="success" if exit_code == 0 else "command_failure",
            execution_duration_ms=int(cli_result.get("latency_ms") or 0),
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            artifact_refs=artifact_refs or None,
            extra_history={
                "workspace_changed_files": changed_files,
                "workspace_meaningful_changed_files": meaningful_changed_files,
                "workspace_dir": str(workspace_ctx.workspace_dir),
                "workspace_artifact_count": len(artifact_refs),
                "interactive_terminal_finalize": True,
                "flow_metrics": self._build_flow_metrics_payload(
                    run_id=str(((proposal_meta.get("trace") or {}).get("trace_id") or "")),
                    phase="execute",
                    propose_ok=True,
                    execute_ok=status == "completed",
                    artifact_created=bool(meaningful_changed_files),
                    worker_profile=((proposal_meta.get("worker_context") or {}).get("worker_profile") or (proposal_meta.get("routing") or {}).get("worker_profile")),
                    profile_source=((proposal_meta.get("worker_context") or {}).get("profile_source") or (proposal_meta.get("routing") or {}).get("profile_source")),
                    policy_classification=str(((proposal_meta.get("routing") or {}).get("policy_classification_summary") or (proposal_meta.get("routing") or {}).get("reason") or "")),
                ),
            },
        )
        get_worker_workspace_service().refresh_interactive_terminal_baseline(workspace_dir=workspace_ctx.workspace_dir)
        return TaskScopedRouteResponse(data=response_payload)

    def _execute_research_artifact(
        self,
        *,
        tid: str,
        task: dict,
        proposal: dict,
        research_artifact: dict,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        review = (proposal.get("review") or {}) if isinstance(proposal, dict) else {}
        if review.get("required") and review.get("status") != "approved":
            raise TaskConflictError("research_review_required", details={"review": review, "task_id": tid})
        verification = self._verify_research_artifact(research_artifact)
        research_artifact["verification"] = verification
        if not verification.get("passed"):
            critique = get_execution_improvement_loop_service().build_verification_critique(
                expected_artifacts=[],
                verification=verification,
                observed_artifacts=[],
                logs=str(research_artifact.get("report_markdown") or ""),
            )
            raise TaskConflictError(
                "research_artifact_verification_failed",
                details={"verification": verification, "task_id": tid, "verification_critique": critique},
            )
        output = str(research_artifact.get("report_markdown") or "")
        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=((proposal.get("routing") or {}).get("task_kind")),
            policy_version=((proposal.get("trace") or {}).get("policy_version")),
            metadata={"task_id": tid, "artifact_execute": True},
        )
        append_stage(pipeline, name="artifact_finalize", status="ok", metadata={"artifact_kind": research_artifact.get("kind")})
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=((proposal.get("routing") or {}).get("task_kind")),
            backend=proposal.get("backend"),
            requested_backend=proposal.get("backend"),
            routing_reason=((proposal.get("routing") or {}).get("reason")),
            policy_version=((proposal.get("trace") or {}).get("policy_version")),
            metadata={"source": "research_artifact_execute", "artifact_kind": research_artifact.get("kind")},
        )
        trace["metadata"]["research_verification"] = verification
        artifact_ref = get_core_services().task_execution_tracking_service.persist_research_artifact(
            tid=tid,
            task=task,
            research_artifact=research_artifact,
        )
        verification_record = get_verification_service().create_or_update_record(
            tid,
            trace_id=trace.get("trace_id"),
            output=output,
            exit_code=0,
            gate_results=verification,
        )
        if isinstance(artifact_ref, dict):
            artifact_ref["verification_record_id"] = getattr(verification_record, "id", None)
            research_artifact.setdefault("trace", {})
            research_artifact["trace"]["persisted_artifact"] = artifact_ref
        from agent.metrics import TASK_COMPLETED

        TASK_COMPLETED.inc()
        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status="completed",
            reason=proposal.get("reason", "Research report persisted"),
            command=None,
            tool_calls=None,
            output=output,
            exit_code=0,
            retries_used=0,
            retry_history=[],
            failure_type="success",
            execution_duration_ms=0,
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            review=review,
            artifact_refs=[artifact_ref] if artifact_ref else None,
            extra_history={
                "artifact_kind": research_artifact.get("kind"),
                "artifact_ref": artifact_ref,
                "source_count": len(research_artifact.get("sources") or []),
                "verification": verification,
                "verification_record_id": getattr(verification_record, "id", None),
                "flow_metrics": self._build_flow_metrics_payload(
                    run_id=str(((proposal.get("trace") or {}).get("trace_id") or "")),
                    phase="execute",
                    propose_ok=True,
                    execute_ok=True,
                    artifact_created=bool(artifact_ref),
                    worker_profile=((proposal.get("worker_context") or {}).get("worker_profile") or (proposal.get("routing") or {}).get("worker_profile")),
                    profile_source=((proposal.get("worker_context") or {}).get("profile_source") or (proposal.get("routing") or {}).get("profile_source")),
                    policy_classification=str(((proposal.get("routing") or {}).get("policy_classification_summary") or (proposal.get("routing") or {}).get("reason") or "")),
                ),
            },
        )
        return TaskScopedRouteResponse(data=response_payload)

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
        # Hub owns cross-container routing. Worker containers must execute locally
        # and never re-forward step endpoints to avoid forwarding loops.
        if str(getattr(settings, "role", "") or "").strip().lower() != "hub":
            return None
        worker_url = task.get("assigned_agent_url")
        if not worker_url:
            return None
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        if worker_url.rstrip("/") == my_url.rstrip("/"):
            return None
        try:
            parsed_worker = urlparse(str(worker_url))
            parsed_self = urlparse(str(my_url))
            worker_host = str(parsed_worker.hostname or "").strip().lower()
            self_host = str(parsed_self.hostname or "").strip().lower()
            worker_port = int(parsed_worker.port or settings.port)
            self_port = int(parsed_self.port or settings.port)
            if worker_port == self_port and (
                worker_host in {"localhost", "127.0.0.1", "0.0.0.0"} or worker_host == self_host
            ):
                return None
        except Exception:
            pass
        assigned_token = task.get("assigned_agent_token")
        resolved_token = assigned_token
        try:
            agent = get_repository_registry().agent_repo.get_by_url(worker_url)
            current_token = str(getattr(agent, "token", "") or "").strip()
            if current_token:
                resolved_token = current_token
        except Exception:
            pass
        try:
            response = forwarder(worker_url, endpoint, payload, token=resolved_token)
            if response is None and resolved_token:
                response = forwarder(worker_url, endpoint, payload, token=None)
            if (
                resolved_token
                and isinstance(response, dict)
                and str(response.get("status") or "").strip().lower() == "error"
                and ("401" in str(response.get("message") or "").lower() or "unauthorized" in str(response.get("message") or "").lower())
            ):
                response = forwarder(worker_url, endpoint, payload, token=None)
            # Worker returned 404: task not in worker DB (split-DB dev setup).
            # Configurable via execution_fallback_policy.worker_404_hub_fallback_enabled.
            if (
                isinstance(response, dict)
                and str(response.get("status") or "").strip().lower() == "error"
                and int(response.get("http_status") or 0) == 404
            ):
                _fallback_policy = {}
                try:
                    _fallback_policy = dict(current_app.config.get("AGENT_CONFIG", {}).get("execution_fallback_policy") or {})
                except Exception:
                    pass
                if bool(_fallback_policy.get("worker_404_hub_fallback_enabled", True)):
                    current_app.logger.warning(
                        "Worker %s returned 404 for %s — falling back to local hub execution",
                        worker_url,
                        endpoint,
                    )
                    return None
            response = unwrap_api_envelope(response)
            if not isinstance(response, dict) or not response:
                raise RuntimeError(f"worker_empty_payload:{worker_url}:{endpoint}")
            if isinstance(response, dict):
                on_success(response, task)
            return TaskScopedRouteResponse(data=response)
        except Exception as exc:
            err_text = str(exc or "")
            err_lc = err_text.lower()
            if assigned_token and ("401" in err_lc or "unauthorized" in err_lc):
                try:
                    response = forwarder(worker_url, endpoint, payload, token=None)
                    response = unwrap_api_envelope(response)
                    if isinstance(response, dict):
                        on_success(response, task)
                    return TaskScopedRouteResponse(data=response)
                except Exception:
                    pass
            current_app.logger.error("Forwarding an %s fehlgeschlagen: %s", worker_url, exc)
            raise WorkerForwardingError(details={"details": str(exc), "worker_url": worker_url})

    def _persist_forwarded_proposal(self, response: dict, task: dict, request_payload: dict | None = None) -> None:
        if not isinstance(response, dict):
            return
        request_payload = dict(request_payload or {})
        prompt_text = str(request_payload.get("prompt") or "").strip()
        forwarded_request = {
            "prompt_preview": prompt_text[:240],
            "prompt_hash_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text else None,
            "provider": str(request_payload.get("provider") or "").strip() or None,
            "providers": list(request_payload.get("providers") or []) if isinstance(request_payload.get("providers"), list) else None,
            "model": str(request_payload.get("model") or "").strip() or None,
            "temperature": request_payload.get("temperature"),
            "strategy_mode": str(request_payload.get("strategy_mode") or "").strip() or None,
            "request_task_id": str(request_payload.get("task_id") or "").strip() or None,
            "captured_at": time.time(),
        }
        has_proposal_payload = any(
            key in response
            for key in (
                "command",
                "tool_calls",
                "reason",
                "raw",
                "routing",
                "cli_result",
                "trace",
                "review",
                "pipeline",
                "research_artifact",
                "research_context",
                "worker_context",
            )
        )
        if not has_proposal_payload:
            return
        response_trace = response.get("trace") if isinstance(response.get("trace"), dict) else None
        if not response_trace:
            metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
            wrapped = response.get("proposal") if isinstance(response.get("proposal"), dict) else {}
            wrapped_meta = wrapped.get("metadata") if isinstance(wrapped.get("metadata"), dict) else {}
            prompt_trace_id = (
                str(metadata.get("prompt_trace_id") or "").strip()
                or str(wrapped_meta.get("prompt_trace_id") or "").strip()
            )
            if prompt_trace_id:
                response_trace = {
                    "trace_id": prompt_trace_id,
                    "source": "model_invocation_service",
                    "request_kind": "propose",
                }
            else:
                response_trace = {
                    "source": "external_worker_uninspectable",
                    "request_kind": "propose",
                    "external_worker_uninspectable": True,
                }
        cli_result = response.get("cli_result") if isinstance(response.get("cli_result"), dict) else None
        if not isinstance(cli_result, dict):
            response_meta = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
            meta_profile = [
                normalize_llm_call_profile_entry(entry)
                for entry in list(response_meta.get("llm_call_profile") or [])
                if isinstance(entry, dict)
            ]
            if meta_profile:
                cli_result = {
                    "returncode": 0,
                    "latency_ms": None,
                    "output_source": str(response.get("backend") or "orchestrator").strip() or "orchestrator",
                    "llm_call_profile": meta_profile,
                }
        if cli_result is None:
            snapshot = get_core_services().autopilot_decision_service.build_proposal_snapshot(response)
            snapshot_cli = snapshot.get("cli_result") if isinstance(snapshot.get("cli_result"), dict) else None
            if isinstance(snapshot_cli, dict):
                cli_result = dict(snapshot_cli)
        if not isinstance(cli_result, dict) and self._allow_synthetic_llm_profile_fallback():
            backend = str(response.get("backend") or "orchestrator").strip() or "orchestrator"
            model = str(response.get("model") or "").strip() or None
            provider = None
            ms = response.get("model_selection")
            if isinstance(ms, dict):
                provider = str(ms.get("runtime_provider") or "").strip() or None
                model = model or (str(ms.get("selected_model") or "").strip() or None)
            cli_result = {
                "returncode": 0,
                "latency_ms": None,
                "output_source": backend,
                "llm_call_profile": [
                    {
                        "name": "propose_forwarded",
                        "backend": backend,
                        "provider": provider,
                        "model": model,
                        "success": True,
                        "latency_ms": None,
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "total_tokens": None,
                        "source": "orchestrator_synthetic",
                        "estimated": True,
                        "error_type": None,
                        "error_message": None,
                        "started_at": None,
                        "ended_at": None,
                    }
                ],
            }
        if not isinstance(cli_result, dict):
            cli_result = {
                "returncode": 0,
                "latency_ms": None,
                "output_source": str(response.get("backend") or "orchestrator").strip() or "orchestrator",
            }
        get_core_services().task_execution_service.persist_task_proposal_result(
            tid=task["id"],
            task=task,
            reason=str(response.get("reason") or ""),
            raw=str(response.get("raw") or ""),
            backend=(str(response.get("backend") or "").strip() or None),
            model=(str(response.get("model") or "").strip() or None),
            routing=response.get("routing") if isinstance(response.get("routing"), dict) else None,
            cli_result=cli_result,
            worker_context=response.get("worker_context") if isinstance(response.get("worker_context"), dict) else None,
            trace=response_trace,
            review=response.get("review") if isinstance(response.get("review"), dict) else None,
            pipeline=response.get("pipeline") if isinstance(response.get("pipeline"), dict) else None,
            command=(str(response.get("command") or "").strip() or None),
            tool_calls=response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else None,
            comparisons=response.get("comparisons") if isinstance(response.get("comparisons"), dict) else None,
            research_artifact=response.get("research_artifact") if isinstance(response.get("research_artifact"), dict) else None,
            research_context=response.get("research_context") if isinstance(response.get("research_context"), dict) else None,
            forwarded_request=forwarded_request,
            history_event={
                "event_type": "proposal_result",
                "reason": str(response.get("reason") or ""),
                "backend": response.get("backend"),
                "routing_reason": ((response.get("routing") or {}).get("reason")) if isinstance(response.get("routing"), dict) else None,
                "forwarded_request": forwarded_request,
                "forwarded": True,
                "timestamp": time.time(),
            },
        )

    def _persist_forwarded_execution(self, *, tid: str, response: dict, task: dict, request_data) -> None:
        if "status" not in response:
            return
        history = task.get("history", [])
        proposal_meta = task.get("last_proposal", {}) or {}
        verification_status = dict(task.get("verification_status") or {})
        execution_scope = response.get("execution_scope") if isinstance(response.get("execution_scope"), dict) else None
        execution_provenance = (
            response.get("execution_provenance") if isinstance(response.get("execution_provenance"), dict) else None
        )
        artifacts = self._normalize_forwarded_artifacts(
            task_id=tid,
            artifacts=list(response.get("artifacts") or []) if isinstance(response.get("artifacts"), list) else None,
        )
        review = response.get("review") if isinstance(response.get("review"), dict) else None
        if execution_scope:
            verification_status["execution_scope"] = dict(execution_scope)
        if execution_provenance:
            verification_status["execution_provenance"] = dict(execution_provenance)
        if artifacts is not None:
            verification_status["execution_artifacts"] = artifacts
        if review:
            verification_status["execution_review"] = dict(review)
        history.append(
            {
                "event_type": "execution_result",
                "prompt": task.get("description"),
                "reason": "Forwarded to " + str(task.get("assigned_agent_url")),
                "command": request_data.command or task.get("last_proposal", {}).get("command"),
                "output": response.get("output"),
                "exit_code": response.get("exit_code"),
                "backend": proposal_meta.get("backend"),
                "routing_reason": ((proposal_meta.get("routing") or {}).get("reason")),
                "artifacts": artifacts,
                "execution_scope": execution_scope,
                "execution_provenance": execution_provenance,
                "review": review,
                "forwarded": True,
                "timestamp": time.time(),
            }
        )
        update_local_task_status(
            tid,
            response["status"],
            history=history,
            last_output=response.get("output"),
            last_exit_code=response.get("exit_code"),
            verification_status=verification_status,
        )

    @staticmethod
    def _normalize_forwarded_artifacts(*, task_id: str, artifacts: list[dict] | None) -> list[dict] | None:
        if artifacts is None:
            return None
        normalized: list[dict] = []
        for idx, item in enumerate(artifacts, start=1):
            if not isinstance(item, dict):
                continue
            row = dict(item)
            artifact_id = str(row.get("artifact_id") or row.get("id") or "").strip()
            kind = str(row.get("kind") or "").strip()
            path = str(row.get("path") or row.get("name") or row.get("filename") or row.get("title") or "").strip()
            if not artifact_id:
                artifact_id = f"{task_id}-artifact-{idx:03d}"
            if not kind:
                kind = "task_output"
            row["artifact_id"] = artifact_id
            row.setdefault("id", artifact_id)
            row["kind"] = kind
            if path:
                row["path"] = path
            row.setdefault("task_id", task_id)
            normalized.append(row)
        return normalized

    # ── HF-T019: Hermes propose path ─────────────────────────────────────────

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
        # FA-T003: Maps to "worker_strategy" (Hermes/OpenCode/native).
        """Invoke HermesAdapter when ToolRouter selects Hermes for safe proposal modes. HF-T019."""
        hermes_cfg_raw = dict((cfg or {}).get("hermes_worker_adapter") or {})
        if not bool(hermes_cfg_raw.get("enabled", False)):
            return None
        feature_flags = dict((cfg or {}).get("feature_flags") or {})
        if not bool(feature_flags.get("enable_hermes_worker_adapter", False)):
            return None

        # Only safe proposal modes — never mutation
        safe_modes = {"plan_only", "review", "summarize", "patch_propose", "research_limited"}
        if task_kind not in safe_modes:
            return None

        try:
            result = self._invoke_hermes_adapter(
                tid=tid,
                task=task,
                task_kind=task_kind,
                request_data=request_data,
                research_context=research_context,
                hermes_cfg_raw=hermes_cfg_raw,
            )
        except Exception as exc:
            # HF-T022: Hermes unavailable → return degraded, not crash
            return TaskScopedRouteResponse(
                data={
                    "status": "degraded",
                    "reason": "hermes_unavailable",
                    "fallback_from_hermes": True,
                    "error": str(exc)[:200],
                    "task_id": tid,
                },
                status="degraded",
                message="Hermes unavailable; no policy-approved fallback",
                code=503,
            )

        if result is None:
            return None

        status = str(result.get("status") or "").lower()
        if status in {"denied", "degraded", "failed"}:
            # HF-T022: record Hermes failure explicitly; don't hide it
            return TaskScopedRouteResponse(
                data={**result, "fallback_from_hermes": False, "task_id": tid},
                status=status,
                message=f"Hermes returned {status}",
                code=400 if status == "denied" else 503,
            )
        return TaskScopedRouteResponse(data={**result, "backend": "hermes", "task_id": tid})

    def _invoke_hermes_adapter(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        research_context: object,
        hermes_cfg_raw: dict,
    ) -> dict | None:
        """Build envelope, context blocks, run HermesAdapter. HF-T019, T020, T021."""
        from worker.core.hermes_adapter import HermesAdapter
        from worker.core.hermes_adapter_config import HermesAdapterConfig
        from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope

        try:
            hermes_config = HermesAdapterConfig(**{
                k: v for k, v in hermes_cfg_raw.items()
                if k in HermesAdapterConfig.model_fields
            })
        except Exception:
            hermes_config = HermesAdapterConfig()

        adapter = HermesAdapter(config=hermes_config)

        # Build minimal envelope for Hermes — planning cap covers review/plan_only
        cap_map = {
            "plan_only": ["planning"],
            "review": ["planning", "review"],
            "summarize": ["planning", "summarize"],
            "patch_propose": ["planning", "patch_propose"],
            "research_limited": ["planning", "research_limited"],
        }
        capabilities = cap_map.get(task_kind, ["planning"])
        envelope = ExecutionEnvelope(
            task_id=tid,
            actor_ref="task_scoped_execution_service:hermes",
            capability_grant=CapabilityGrant(capabilities=capabilities),
            context_envelope_ref=f"task:{tid}",
            audit_correlation_id=f"hermes:{tid}:{task_kind}",
        )

        # HF-T020: build context blocks from task/research context
        context_blocks = build_hermes_context_blocks(
            task=task,
            request_data=request_data,
            research_context=research_context,
        )

        # Run adapter for the requested mode
        mode_method = getattr(adapter, task_kind, adapter.plan_only)
        worker_result = mode_method(envelope, context_blocks=context_blocks)

        # HF-T021: convert WorkerResult artifacts to task response format
        artifacts = []
        for art in (worker_result.artifacts or []):
            artifacts.append({
                "artifact_id": art.artifact_id,
                "kind": art.kind,
                "provenance": art.provenance,
                "summary": art.summary,
                "metadata": dict(art.metadata or {}),
                "source": "hermes",
            })

        return {
            "status": worker_result.status.value,
            "summary": worker_result.summary,
            "artifacts": artifacts,
            "artifact_refs": [a["artifact_id"] for a in artifacts],
            "policy_observations": list(worker_result.policy_observations or []),
            "warnings": list(worker_result.warnings or []),
            "no_side_effects_confirmed": worker_result.no_side_effects_confirmed,
            "backend": "hermes",
            "adapter_mode": task_kind,
        }

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
        # FA-T003: Maps to "deterministic_handler" strategy.
        registry = get_task_handler_registry()
        handler = registry.resolve(task_kind)
        if handler is None or not hasattr(handler, "propose"):
            return None
        handler_descriptor = registry.resolve_descriptor(task_kind) or {}
        response = handler.propose(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            base_prompt=base_prompt,
            service=self,
            cli_runner=cli_runner,
            forwarder=forwarder,
            tool_definitions_resolver=tool_definitions_resolver,
            handler_descriptor=handler_descriptor,
        )
        coerced = self._coerce_handler_response(response)
        if coerced is None:
            return None
        payload = dict(coerced.data or {})
        payload.setdefault("handler_contract", handler_descriptor or None)
        if bool((handler_descriptor.get("safety_flags") or {}).get("requires_review")) and "review" not in payload:
            base_review = self._build_review_state(
                current_app.config.get("AGENT_CONFIG", {}) or {},
                backend="handler",
                task_kind=task_kind,
                command=str(payload.get("command") or "") or None,
                tool_calls=payload.get("tool_calls"),
            )
            payload["review"] = {
                **base_review,
                "required": True,
                "status": "pending",
                "reason": "handler_safety_requires_review",
            }
        return TaskScopedRouteResponse(
            data=payload,
            status=coerced.status,
            message=coerced.message,
            code=coerced.code,
        )

    def _try_handler_execute(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        forwarder: Callable,
    ) -> TaskScopedRouteResponse | None:
        registry = get_task_handler_registry()
        handler = registry.resolve(task_kind)
        if handler is None or not hasattr(handler, "execute"):
            return None
        handler_descriptor = registry.resolve_descriptor(task_kind) or {}
        response = handler.execute(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            service=self,
            forwarder=forwarder,
            handler_descriptor=handler_descriptor,
        )
        coerced = self._coerce_handler_response(response)
        if coerced is None:
            return None
        payload = dict(coerced.data or {})
        payload.setdefault("handler_contract", handler_descriptor or None)
        return TaskScopedRouteResponse(
            data=payload,
            status=coerced.status,
            message=coerced.message,
            code=coerced.code,
        )

    def _coerce_handler_response(self, response: object | None) -> TaskScopedRouteResponse | None:
        if response is None:
            return None
        if isinstance(response, TaskScopedRouteResponse):
            return response
        if isinstance(response, dict):
            return TaskScopedRouteResponse(data=response)
        raise TypeError("task_handler_response_must_be_dict_or_TaskScopedRouteResponse")

    def _require_task(self, tid: str) -> dict:
        task = get_local_task_status(tid)
        if not task:
            task = self._maybe_sync_task_from_hub(tid)
        if not task:
            raise TaskNotFoundError()
        return task

    def _maybe_sync_task_from_hub(self, tid: str) -> dict | None:
        try:
            agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if current_app else {}
        except Exception:
            agent_cfg = {}
        fp = dict((agent_cfg.get("execution_fallback_policy") or {}))
        if not bool(fp.get("worker_task_sync_from_hub_enabled", True)):
            return None
        from agent.services.task_runtime_service import sync_task_from_hub
        return sync_task_from_hub(tid)

    def _resolve_cli_backend(
        self,
        task_kind: str,
        requested_backend: str = "auto",
        agent_cfg: dict | None = None,
        required_capabilities: list[str] | None = None,
    ) -> tuple[str, str]:
        backend, reason, _ = resolve_cli_backend(
            task_kind=task_kind,
            requested_backend=requested_backend,
            supported_backends=SUPPORTED_CLI_BACKENDS,
            agent_cfg=agent_cfg if agent_cfg is not None else (current_app.config.get("AGENT_CONFIG", {}) or {}),
            fallback_backend="sgpt",
            required_capabilities=required_capabilities,
        )
        return backend, reason

    @staticmethod
    def _coalesce_cli_output(stdout: str | None, stderr: str | None) -> tuple[str, str]:
        out = str(stdout or "").strip()
        if out:
            return out, "stdout"
        err = str(stderr or "").strip()
        if err:
            return err, "stderr"
        return "", "none"

    @classmethod
    def _sanitize_structured_output_text(cls, raw_text: str) -> str:
        return sanitize_structured_output_text(raw_text)

    @staticmethod
    def _normalize_tool_calls(tool_calls: object) -> list[dict] | None:
        if isinstance(tool_calls, list) and all(isinstance(item, dict) for item in tool_calls):
            return tool_calls
        if isinstance(tool_calls, dict):
            return [tool_calls]
        return None

    @classmethod
    def _normalize_structured_action_payload(cls, data: object) -> dict | None:
        return normalize_structured_action_payload(data)

    @classmethod
    def _parse_structured_action_payload(cls, raw_text: str) -> dict | None:
        return parse_structured_action_payload(raw_text)

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
        locally_repaired = self._locally_repair_structured_action_output(bad_output)
        if locally_repaired:
            return {
                "raw": locally_repaired,
                "output_source": "local_repair",
                "backend_used": primary_backend,
                "model": primary_model,
                "temperature": self._normalize_temperature(primary_temperature),
                "stderr": "",
                "rc": 0,
            }
        default_model = str(cfg.get("default_model") or cfg.get("model") or "").strip() or None
        first_backend = str(primary_backend or "opencode").strip().lower()
        if first_backend not in SUPPORTED_CLI_BACKENDS:
            first_backend = "opencode"
        first_model = primary_model or default_model

        repair_backend = str(cfg.get("task_propose_repair_backend") or "opencode").strip().lower()
        if repair_backend not in SUPPORTED_CLI_BACKENDS:
            repair_backend = "opencode"
        repair_model = str(cfg.get("task_propose_repair_model") or "").strip() or default_model
        normalized_temperature = self._normalize_temperature(primary_temperature)
        timeout_like_failure = validation_error == "empty_or_failed_cli_response" and "timeout" in str(bad_output or "").lower()
        candidates: list[tuple[str, str | None, float | None]] = []
        if not timeout_like_failure or repair_backend == first_backend:
            candidates.append((first_backend, first_model, normalized_temperature))
        candidates.append((repair_backend, repair_model, normalized_temperature))
        deduped: list[tuple[str, str | None, float | None]] = []
        seen: set[tuple[str, str, str]] = set()
        for backend_name, model_name, temperature in candidates:
            key = (backend_name, str(model_name or ""), str(temperature))
            if key in seen:
                continue
            seen.add(key)
            deduped.append((backend_name, model_name, temperature))

        repair_prompt = self._build_repair_prompt(prompt=prompt, bad_output=bad_output, validation_error=validation_error)
        for backend_name, model_name, temperature in deduped:
            cli_kwargs = {
                "prompt": repair_prompt,
                "options": ["--no-interaction"],
                "timeout": timeout,
                "backend": backend_name,
                "model": model_name,
                "routing_policy": {"mode": "adaptive", "task_kind": task_kind, "policy_version": policy_version},
                "workdir": workdir,
            }
            if temperature is not None:
                cli_kwargs["temperature"] = temperature
            if research_context:
                cli_kwargs["research_context"] = research_context
            if session:
                cli_kwargs["session"] = session
            rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(cli_runner, **cli_kwargs)
            raw_res, output_source = self._coalesce_cli_output(cli_out, cli_err)
            if not raw_res.strip():
                continue
            command, tool_calls = self._extract_structured_action_fields(raw_res)
            if not command and not tool_calls:
                continue
            return {
                "raw": raw_res,
                "output_source": output_source,
                "backend_used": backend_used,
                "model": model_name,
                "temperature": temperature,
                "stderr": cli_err,
                "rc": rc,
            }
        return None

    @staticmethod
    def _is_timeout_like_repair_failure(*, validation_error: str, bad_output: str) -> bool:
        error_marker = str(validation_error or "").strip().lower()
        output_marker = str(bad_output or "").strip().lower()
        if error_marker in {"empty_or_failed_cli_response", "empty_cli_response"}:
            if not output_marker:
                return True
            return "timeout" in output_marker or "timed out" in output_marker
        return "timeout" in output_marker or "timed out" in output_marker

    @staticmethod
    def _is_shell_meta_blocked_failure(output: str | None, failure_type: str | None) -> bool:
        if str(failure_type or "").strip().lower() != "command_runtime_error":
            return False
        text = str(output or "")
        markers = (
            "Befehlskettung (&&/||)",
            "Semikolons (;)",
            "Input/Output-Redirection",
            "Background-Execution (&)",
            "Unsupported shell operators in command",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_command_not_found_failure(output: str | None, failure_type: str | None) -> bool:
        normalized = str(failure_type or "").strip().lower()
        if normalized in {"command_not_found", "command_runtime_error"}:
            text = str(output or "").lower()
            if "command not found" in text or "not recognized as an internal or external command" in text:
                return True
        return False

    @staticmethod
    def _estimate_tokens(value: str | None) -> int:
        text = str(value or "")
        if not text:
            return 0
        return max(1, int(len(text) / 4))

    def _build_llm_call_profile_entries(
        self,
        *,
        backend_used: str,
        model: str | None,
        prompt: str,
        raw_output: str,
        latency_ms: int,
        rc: int,
        repair_attempted: bool,
        repair_backend: str | None,
        repair_model: str | None,
    ) -> list[dict]:
        entries = [
            build_llm_call_profile_entry(
                name="propose_primary",
                backend=str(backend_used or ""),
                provider=None,
                model=str(model or "") or None,
                success=bool(rc == 0),
                started_at=None,
                ended_at=None,
                usage={
                    "prompt_tokens": self._estimate_tokens(prompt),
                    "completion_tokens": self._estimate_tokens(raw_output),
                },
                source="cli_backend",
                estimated=True,
            )
        ]
        # Override latency_ms post-hoc since we have the real value but not started_at/ended_at.
        entries[0]["latency_ms"] = int(latency_ms or 0)
        if repair_attempted:
            entries.append(
                build_llm_call_profile_entry(
                    name="propose_repair",
                    backend=str(repair_backend or ""),
                    provider=None,
                    model=str(repair_model or "") or None,
                    success=bool(rc == 0),
                    started_at=None,
                    ended_at=None,
                    source="cli_backend",
                    estimated=True,
                )
            )
        return entries

    @staticmethod
    def _build_repair_prompt(*, prompt: str, bad_output: str, validation_error: str) -> str:
        preview = str(bad_output or "").strip()
        if len(preview) > 2000:
            preview = preview[:2000]
        return (
            "Der vorherige Modell-Output war leer/ungueltig oder nicht ausfuehrbar.\n"
            "Repariere die Antwort und gib NUR ein valides JSON-Objekt zurueck.\n\n"
            f"Validator/Fehlergrund: {validation_error}\n\n"
            "Anforderungen:\n"
            "- Genau ein JSON-Objekt, kein Markdown.\n"
            "- Felder: reason (string), command (string optional), tool_calls (array optional).\n"
            "- Mindestens eines von command oder tool_calls muss befuellt sein.\n\n"
            f"Original-Prompt:\n{prompt}\n\n"
            f"Fehlerhafter Output (Ausschnitt):\n{preview}\n"
        )

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
        proposal_meta = dict(task.get("last_proposal") or {})
        research_context = proposal_meta.get("research_context") if isinstance(proposal_meta.get("research_context"), dict) else None
        prompt, _ = self._build_task_propose_prompt(
            tid=tid,
            task=task,
            base_prompt=str(task.get("description") or task.get("prompt") or f"Bearbeite Task {tid}"),
            tool_definitions_resolver=tool_definitions_resolver or (lambda *_args, **_kwargs: []),
            research_context=research_context,
        )
        timeout = self._resolve_task_propose_timeout(agent_cfg, task_kind)
        routing_policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]
        primary_backend = str(
            proposal_meta.get("backend")
            or ((proposal_meta.get("routing") or {}).get("execution_backend"))
            or ((proposal_meta.get("routing") or {}).get("effective_backend"))
            or "opencode"
        ).strip().lower()
        primary_model = self._resolve_requested_model(
            agent_cfg=agent_cfg,
            requested_model=str(proposal_meta.get("model") or "").strip() or None,
        )
        bad_output = json.dumps(
            {
                "blocked_command": command,
                "execution_error": execution_output,
                "raw_proposal_preview": str(proposal_meta.get("raw") or "")[:1200],
            },
            ensure_ascii=False,
        )
        repaired = self._repair_task_proposal(
            cli_runner=cli_runner,
            prompt=prompt,
            bad_output=bad_output,
            validation_error="shell_meta_character_blocked",
            timeout=timeout,
            task_kind=task_kind,
            policy_version=routing_policy_version,
            cfg=agent_cfg,
            primary_backend=primary_backend,
            primary_model=primary_model,
            primary_temperature=self._normalize_temperature(((proposal_meta.get("routing") or {}).get("inference_temperature"))),
            research_context=research_context,
            session=self._prepare_task_cli_session(
                tid=tid,
                task=task,
                backend=primary_backend,
                model=primary_model,
                agent_cfg=agent_cfg,
            ),
            workdir=workspace_dir,
        )
        if not repaired:
            return None
        repaired_command, repaired_tool_calls = self._extract_structured_action_fields(str(repaired.get("raw") or ""))
        if not repaired_command and not repaired_tool_calls:
            return None
        if repaired_command and repaired_command.strip() == str(command or "").strip() and not repaired_tool_calls:
            return None
        append_stage(
            pipeline,
            name="proposal_repair",
            status="ok",
            metadata={
                "reason": "shell_meta_character_blocked",
                "repair_backend": repaired.get("backend_used"),
                "repair_model": repaired.get("model"),
            },
        )
        repaired_run = get_core_services().task_execution_service.execute_local_step(
            tid=tid,
            task=task,
            command=repaired_command,
            tool_calls=repaired_tool_calls,
            execution_policy=execution_policy,
            guard_cfg=agent_cfg,
            working_directory=workspace_dir,
            pipeline=pipeline,
            exec_started_at=exec_started_at,
        )
        return {
            "reason": _extract_reason(str(repaired.get("raw") or "")) or "Repaired proposal after shell policy block.",
            "command": repaired_command,
            "tool_calls": repaired_tool_calls,
            "execution_run": repaired_run,
            "repair_meta": {
                "attempted": True,
                "trigger": "shell_meta_character_blocked",
                "repair_backend": repaired.get("backend_used"),
                "repair_model": repaired.get("model"),
                "output_source": repaired.get("output_source"),
            },
        }

    def _build_research_result(
        self,
        raw_res: str,
        backend_used: str,
        tid: str | None,
        rc: int,
        cli_err: str,
        latency_ms: int,
        output_source: str = "stdout",
        research_context: dict | None = None,
    ) -> dict:
        artifact = normalize_research_artifact(
            raw_res,
            backend=backend_used,
            task_id=tid,
            cli_result={
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
            },
            research_context=research_context,
        )
        return {
            "reason": artifact.get("summary") or "Research report generated",
            "raw": raw_res,
            "research_artifact": artifact,
            "research_context": research_context,
            "backend": backend_used,
            "command": None,
            "tool_calls": None,
            "cli_result": {
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "llm_call_profile": self._build_llm_call_profile_entries(
                    backend_used=backend_used,
                    model=artifact.get("model"),
                    prompt=(research_context or {}).get("prompt_section"),
                    raw_output=raw_res,
                    latency_ms=latency_ms,
                    rc=rc,
                    repair_attempted=False,
                    repair_backend=None,
                    repair_model=None,
                ),
            },
        }

    def _verify_research_artifact(self, research_artifact: dict | None) -> dict:
        artifact = dict(research_artifact or {})
        report_markdown = str(artifact.get("report_markdown") or "").strip()
        sources = list(artifact.get("sources") or [])
        citations = list(artifact.get("citations") or [])
        passed = bool(report_markdown and sources)
        verification = {
            "passed": passed,
            "ready": passed,
            "has_report": bool(report_markdown),
            "has_sources": bool(sources),
            "has_citations": bool(citations),
            "source_count": len(sources),
            "citation_count": len(citations),
            "reason": "verified" if passed else "missing_sources_or_report",
        }
        artifact_verification = dict(artifact.get("verification") or {})
        artifact_verification.update(verification)
        artifact["verification"] = artifact_verification
        return artifact_verification

    def _build_review_state(
        self,
        agent_cfg: dict,
        backend: str,
        task_kind: str,
        *,
        command: str | None,
        tool_calls: list[dict] | None,
    ) -> dict:
        risk_level = max_risk_level(
            classify_command_risk(command),
            classify_tool_calls_risk(tool_calls, guard_cfg=agent_cfg),
        )
        policy = review_policy(
            agent_cfg,
            backend=backend,
            task_kind=task_kind,
            risk_level=risk_level,
            uses_terminal=has_terminal_signal(command),
            uses_file_access=has_file_access_signal(command, tool_calls),
        )
        return {
            "required": bool(policy.get("required")),
            "status": "pending" if policy.get("required") else "not_required",
            "policy_version": policy.get("policy_version"),
            "reason": policy.get("reason"),
            "risk_level": policy.get("risk_level"),
            "uses_terminal": policy.get("uses_terminal"),
            "uses_file_access": policy.get("uses_file_access"),
            "reviewed_by": None,
            "reviewed_at": None,
            "comment": None,
        }

    def _get_worker_execution_context(
        self,
        task: dict | None,
        *,
        tid: str | None = None,
        base_prompt: str | None = None,
    ) -> dict:
        agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
        semantic_policy = self._resolve_worker_semantic_output_correction_policy(agent_cfg)
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        if execution_context:
            execution_context["allowed_tools"] = normalize_allowed_tools(execution_context.get("allowed_tools"))
            if semantic_policy and not isinstance(execution_context.get("semantic_output_correction"), dict):
                execution_context["semantic_output_correction"] = semantic_policy
            profile, profile_source = resolve_worker_execution_profile(
                worker_execution_context=execution_context,
                agent_cfg=agent_cfg,
            )
            execution_context["worker_profile"] = profile
            execution_context["profile_source"] = profile_source
            auto_bundle_cfg = dict(
                (agent_cfg.get("worker_runtime") or {}).get("codecompass_auto_bundle") or {}
            )
            if auto_bundle_cfg.get("enabled") and not list(
                (execution_context.get("context") or {}).get("chunks") or []
            ):
                kind_filter = [
                    str(k).strip().lower()
                    for k in list(auto_bundle_cfg.get("task_kinds") or [])
                    if str(k).strip()
                ]
                routing_kind = str(
                    (execution_context.get("routing_hints") or {}).get("task_kind") or ""
                ).strip().lower()
                if not kind_filter or not routing_kind or routing_kind in kind_filter:
                    try:
                        resolved = get_context_manager_service().ensure_task_context_bundle(
                            task=dict(task or {}),
                            task_id=tid,
                            query=base_prompt,
                        )
                        bundle = resolved.get("context_bundle")
                        if bundle:
                            ctx = dict(execution_context.get("context") or {})
                            ctx.setdefault("chunks", []).extend(list(bundle.chunks or []))
                            ctx["token_estimate"] = (
                                int(ctx.get("token_estimate") or 0)
                                + int(bundle.token_estimate or 0)
                            )
                            if not ctx.get("context_text") and bundle.context_text:
                                ctx["context_text"] = bundle.context_text
                            execution_context["context"] = ctx
                            execution_context.setdefault("context_bundle_id", bundle.id)
                    except Exception:
                        pass
            return execution_context
        bundle_id = str((task or {}).get("context_bundle_id") or "").strip()
        bundle = None
        if bundle_id:
            bundle = get_repository_registry().context_bundle_repo.get_by_id(bundle_id)
        if bundle is None and (tid or (task or {})):
            resolved = get_context_manager_service().ensure_task_context_bundle(
                task=dict(task or {}),
                task_id=tid,
                query=base_prompt,
            )
            resolved_bundle = resolved.get("context_bundle")
            if resolved_bundle is not None:
                bundle = resolved_bundle
        if bundle is None:
            return {}
        profile, profile_source = resolve_worker_execution_profile(
            worker_execution_context={},
            agent_cfg=agent_cfg,
        )
        resolved_context = {
            "context_bundle_id": bundle.id,
            "worker_profile": profile,
            "profile_source": profile_source,
            "context": {
                "context_text": bundle.context_text,
                "chunks": list(bundle.chunks or []),
                "token_estimate": int(bundle.token_estimate or 0),
                "bundle_metadata": dict(bundle.bundle_metadata or {}),
            },
        }
        if semantic_policy:
            resolved_context["semantic_output_correction"] = semantic_policy
        return resolved_context

    def _tool_definitions_for_task(
        self,
        task: dict | None,
        *,
        tool_definitions_resolver: Callable,
        execution_context: dict | None = None,
    ) -> list[dict]:
        execution_context = dict(execution_context or self._get_worker_execution_context(task))
        allowed_tools = normalize_allowed_tools(execution_context.get("allowed_tools"))
        if allowed_tools:
            return tool_definitions_resolver(allowlist=allowed_tools)
        return tool_definitions_resolver()

    @staticmethod
    def _cli_session_policy(agent_cfg: dict | None) -> dict:
        cfg = agent_cfg or {}
        mode = cfg.get("cli_session_mode") if isinstance(cfg.get("cli_session_mode"), dict) else {}
        backends = [str(item or "").strip().lower() for item in list(mode.get("stateful_backends") or ["opencode", "codex"]) if str(item or "").strip()]
        return {
            "enabled": bool(mode.get("enabled", False)),
            "stateful_backends": backends,
            "max_turns_per_session": max(1, min(int(mode.get("max_turns_per_session") or 40), 200)),
            "max_sessions": max(1, min(int(mode.get("max_sessions") or 200), 2000)),
            "allow_task_scoped_auto_session": bool(mode.get("allow_task_scoped_auto_session", True)),
            "reuse_scope": str(mode.get("reuse_scope") or "task").strip().lower() or "task",
            "native_opencode_sessions": bool(mode.get("native_opencode_sessions", False)),
        }

    @staticmethod
    def _resolve_opencode_execution_mode(agent_cfg: dict | None) -> str:
        cfg = agent_cfg or {}
        runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
        mode = str(runtime_cfg.get("execution_mode") or "live_terminal").strip().lower()
        return mode if mode in {"backend", "live_terminal", "interactive_terminal"} else "live_terminal"

    @staticmethod
    def _resolve_opencode_interactive_launch_mode(agent_cfg: dict | None) -> str:
        cfg = agent_cfg or {}
        runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
        mode = str(runtime_cfg.get("interactive_launch_mode") or "run").strip().lower()
        return mode if mode in {"run", "tui"} else "run"

    @staticmethod
    def _native_worker_runtime_enabled(agent_cfg: dict | None) -> bool:
        runtime_cfg = (agent_cfg or {}).get("worker_runtime") if isinstance((agent_cfg or {}).get("worker_runtime"), dict) else {}
        native_cfg = runtime_cfg.get("native_worker_runtime") if isinstance(runtime_cfg.get("native_worker_runtime"), dict) else {}
        return bool(native_cfg.get("enabled", False))

    def _should_use_native_worker_runtime(self, *, proposal_meta: dict | None, agent_cfg: dict | None, command: str | None) -> bool:
        if not str(command or "").strip():
            return False
        if not self._native_worker_runtime_enabled(agent_cfg):
            return False
        proposal = dict(proposal_meta or {})
        backend = str(proposal.get("backend") or "").strip().lower()
        routing = dict(proposal.get("routing") or {})
        runtime_path = str(routing.get("worker_runtime_path") or "").strip().lower()
        return backend == "ananta-worker" and runtime_path == "native_worker_pipeline"

    def _resolve_task_role_identity(self, tid: str, task: dict) -> tuple[str | None, str | None]:
        task_record = get_repository_registry().task_repo.get_by_id(tid)
        if not task_record:
            return None, None
        role_id = getattr(task_record, "assigned_role_id", None)
        if task_record.team_id and task_record.assigned_agent_url:
            members = get_repository_registry().team_member_repo.get_by_team(task_record.team_id)
            for member in members:
                if member.agent_url == task_record.assigned_agent_url and not role_id:
                    role_id = member.role_id
                    break
        role_name = None
        if role_id:
            role = get_repository_registry().role_repo.get_by_id(role_id)
            if role:
                role_name = role.name
        return str(role_id or "").strip() or None, str(role_name or "").strip() or None

    def _resolve_task_session_scope(self, *, tid: str, task: dict, policy: dict) -> tuple[str, str, str | None]:
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        workspace = dict(execution_context.get("workspace") or {})
        explicit_scope_key = str(workspace.get("session_scope_key") or "").strip()
        if explicit_scope_key:
            explicit_scope_kind = str(workspace.get("session_scope_kind") or "workspace").strip().lower() or "workspace"
            return explicit_scope_kind, explicit_scope_key, None

        reuse_scope = str(policy.get("reuse_scope") or "task").strip().lower()
        if reuse_scope == "role":
            role_id, role_name = self._resolve_task_role_identity(tid, task)
            if role_id or role_name:
                role_key = role_id or f"role-name:{role_name}"
                return "role", str(role_key), role_name
        return "task", f"task:{tid}", None

    @staticmethod
    def _has_native_opencode_runtime(session_payload: dict | None) -> bool:
        metadata = (session_payload or {}).get("metadata") if isinstance((session_payload or {}).get("metadata"), dict) else {}
        runtime_meta = metadata.get("opencode_runtime") if isinstance(metadata.get("opencode_runtime"), dict) else {}
        return str(runtime_meta.get("kind") or "").strip().lower() == "native_server"

    def _prepare_task_cli_session(
        self,
        *,
        tid: str,
        task: dict,
        backend: str,
        model: str | None,
        agent_cfg: dict | None,
    ) -> dict | None:
        policy = self._cli_session_policy(agent_cfg)
        backend_name = str(backend or "").strip().lower()
        opencode_execution_mode = self._resolve_opencode_execution_mode(agent_cfg)
        opencode_interactive_launch_mode = self._resolve_opencode_interactive_launch_mode(agent_cfg)
        terminal_execution_mode = (
            opencode_execution_mode if backend_name == "opencode" and opencode_execution_mode in {"live_terminal", "interactive_terminal"} else None
        )
        if not terminal_execution_mode and (not policy["enabled"] or not policy["allow_task_scoped_auto_session"]):
            return None
        if not terminal_execution_mode and backend_name not in set(policy["stateful_backends"]):
            return None
        scope_kind, scope_key, role_name = self._resolve_task_session_scope(tid=tid, task=task, policy=policy)
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        workspace_dir = str(workspace_context.workspace_dir)
        verification = dict(task.get("verification_status") or {})
        session_meta = verification.get("cli_session") if isinstance(verification.get("cli_session"), dict) else {}
        existing_id = str(session_meta.get("session_id") or "").strip()
        session = get_cli_session_service().get_session(existing_id, include_history=False) if existing_id else None
        if (
            not session
            or str(session.get("status") or "").strip().lower() != "active"
            or str(session.get("backend") or "").strip().lower() != backend_name
        ):
            session = get_cli_session_service().find_active_session(
                backend=backend_name,
                scope_key=scope_key,
                scope_kind=scope_kind,
            )
        session_reused = False
        if session and str(session.get("status") or "").strip().lower() == "active" and str(session.get("backend") or "").strip().lower() == backend_name:
            session_payload = dict(session)
            session_reused = True
        else:
            session_payload = get_cli_session_service().create_session(
                backend=backend_name,
                model=model,
                metadata={
                    "source": "task_propose_auto_session",
                    "task_id": tid,
                    "scope_kind": scope_kind,
                    "scope_key": scope_key,
                    "role_name": role_name,
                    "opencode_workdir": workspace_dir,
                    "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                },
                task_id=tid,
                conversation_id=scope_key,
            )
            verification["cli_session"] = {
                "session_id": session_payload.get("id"),
                "backend": backend_name,
                "model": model,
                "status": "active",
                "scope_kind": scope_kind,
                "scope_key": scope_key,
                "updated_at": time.time(),
            }
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        if backend_name == "opencode" and not terminal_execution_mode:
            session_payload = (
                get_cli_session_service().update_session(
                    str(session_payload.get("id") or ""),
                    metadata_updates={
                        "opencode_execution_mode": "backend",
                        "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                        "opencode_live_terminal": {},
                    },
                )
                or session_payload
            )
            verification["cli_session"] = {
                **verification.get("cli_session", {}),
                "execution_mode": "backend",
                "terminal_session_id": None,
                "forward_param": None,
                "terminal_status": None,
                "updated_at": time.time(),
            }
            verification["opencode_live_terminal"] = {}
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        if terminal_execution_mode:
            terminal_meta = (
                get_live_terminal_session_service().ensure_session_for_cli(
                    session_payload,
                    execution_mode=terminal_execution_mode,
                    workdir=workspace_dir,
                )
                or {}
            )
            interactive_terminal_workspace = (
                dict(verification.get("interactive_terminal_workspace") or {})
                if isinstance(verification.get("interactive_terminal_workspace"), dict)
                else {}
            )
            if terminal_execution_mode == "interactive_terminal" and not interactive_terminal_workspace.get("baseline_ready"):
                baseline_meta = get_worker_workspace_service().refresh_interactive_terminal_baseline(workspace_dir=Path(workspace_dir))
                interactive_terminal_workspace = {
                    "baseline_ready": True,
                    **baseline_meta,
                }
            session_payload = (
                get_cli_session_service().update_session(
                    str(session_payload.get("id") or ""),
                    metadata_updates={
                        "opencode_execution_mode": terminal_execution_mode,
                        "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                        "opencode_live_terminal": terminal_meta,
                        "opencode_workdir": workspace_dir,
                    },
                )
                or session_payload
            )
            verification["cli_session"] = {
                **verification.get("cli_session", {}),
                "execution_mode": terminal_execution_mode,
                "terminal_session_id": terminal_meta.get("terminal_session_id"),
                "forward_param": terminal_meta.get("forward_param"),
                "agent_url": terminal_meta.get("agent_url"),
                "agent_name": terminal_meta.get("agent_name"),
                "terminal_status": terminal_meta.get("status"),
                "updated_at": time.time(),
            }
            if interactive_terminal_workspace:
                verification["interactive_terminal_workspace"] = interactive_terminal_workspace
            verification["opencode_live_terminal"] = dict(terminal_meta)
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        elif backend_name == "opencode" and bool(policy.get("native_opencode_sessions")):
            from agent.services.opencode_runtime_service import get_opencode_runtime_service

            runtime_meta = get_opencode_runtime_service().ensure_session_runtime(session_payload, model=model)
            session_payload = (
                get_cli_session_service().get_session(str(session_payload.get("id") or ""), include_history=False) or session_payload
            )
            verification["cli_session"] = {
                **verification.get("cli_session", {}),
                "native_session_id": runtime_meta.get("native_session_id"),
                "server_key": runtime_meta.get("server_key"),
                "server_url": runtime_meta.get("server_url"),
                "agent": runtime_meta.get("agent"),
                "updated_at": time.time(),
            }
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        get_cli_session_service().prune_sessions(max_sessions=policy["max_sessions"])
        session_payload["session_reused"] = bool(session_reused)
        session_payload["max_turns_per_session"] = policy["max_turns_per_session"]
        return session_payload

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
        execution_context = self._get_worker_execution_context(task, tid=tid, base_prompt=base_prompt)
        context_payload = dict(execution_context.get("context") or {})
        retrieval_trace_link = self._extract_retrieval_trace_link(context_payload)
        context_text = str(context_payload.get("context_text") or "").strip()
        context_profile_payload = dict(context_profile or {})
        compact_profile = bool(context_profile_payload.get("compact"))
        task_brief_char_limit = (
            self._bounded_int(context_profile_payload.get("task_brief_char_limit"), default=900, minimum=180, maximum=4000)
            if compact_profile
            else None
        )
        hub_context_char_limit = (
            self._bounded_int(context_profile_payload.get("hub_context_char_limit"), default=2600, minimum=256, maximum=12000)
            if compact_profile
            else None
        )
        research_prompt_char_limit = (
            self._bounded_int(context_profile_payload.get("research_prompt_char_limit"), default=1800, minimum=200, maximum=12000)
            if compact_profile
            else None
        )
        if hub_context_char_limit and len(context_text) > hub_context_char_limit:
            context_text = context_text[: max(1, hub_context_char_limit - 14)].rstrip() + "\n\n[gekürzt]"
        workspace_payload = dict(execution_context.get("workspace") or {})
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        allowed_tools = normalize_allowed_tools(execution_context.get("allowed_tools"))
        expected_output_schema = dict(execution_context.get("expected_output_schema") or {})
        semantic_output_correction = (
            dict(execution_context.get("semantic_output_correction") or {})
            if isinstance(execution_context.get("semantic_output_correction"), dict)
            else {}
        )
        worker_profile = normalize_worker_execution_profile(execution_context.get("worker_profile"))
        profile_source = str(execution_context.get("profile_source") or "agent_default").strip().lower() or "agent_default"
        tool_definitions = self._tool_definitions_for_task(
            task,
            tool_definitions_resolver=tool_definitions_resolver,
            execution_context=execution_context,
        )

        prompt_sections: list[str] = []
        system_prompt = self._get_system_prompt_for_task(tid)
        instruction_stack = get_instruction_layer_service().assemble_for_task(
            task=task,
            base_prompt=base_prompt,
            system_prompt=system_prompt,
            emit_audit=True,
        )
        effective_system_prompt = str(instruction_stack.get("rendered_system_prompt") or "").strip() or None
        stack_diagnostics = dict(instruction_stack.get("diagnostics") or {})
        shell_command_mode = str(execution_context.get("shell_command_mode") or "").strip().lower()
        allow_complex_shell = shell_command_mode == "pipeline"
        opencode_context_files = get_worker_workspace_service().prepare_opencode_context_files(
            task=task,
            workspace_context=workspace_context,
            base_prompt=base_prompt,
            system_prompt=effective_system_prompt,
            context_text=context_text,
            expected_output_schema=expected_output_schema,
            tool_definitions=tool_definitions,
            research_context=research_context,
            include_response_contract=not interactive_terminal,
            allow_complex_shell=allow_complex_shell,
            task_brief_char_limit=task_brief_char_limit,
            context_text_char_limit=hub_context_char_limit,
            research_prompt_char_limit=research_prompt_char_limit,
        )
        prompt_sections.append(f"Aktueller Auftrag: {base_prompt}")
        read_paths = [
            str(opencode_context_files.get("agents_path") or "").strip(),
            str(opencode_context_files.get("context_index_path") or "").strip(),
            str(opencode_context_files.get("task_brief_path") or "").strip(),
        ]
        if context_text:
            read_paths.append(str(opencode_context_files.get("hub_context_path") or ".ananta/hub-context.md"))
        if not interactive_terminal:
            read_paths.append(str(opencode_context_files.get("response_contract_path") or "").strip())
        read_paths = [item for item in read_paths if item]
        if read_paths:
            prompt_sections.append(
                "Lies zuerst die bereitgestellten Workspace-Dateien und verwende diesen Dateikontext "
                "statt lange Inhalte zu wiederholen:\n" + "\n".join(f"- {item}" for item in read_paths)
            )
        if context_text:
            prompt_sections.append(
                "Selektierter Research-Kontext ist im Hub-Kontext enthalten und wird aus derselben Datei geladen."
            )
            context_preview = " ".join(str(context_text).split()).strip().lower()[:240]
            if context_preview and not compact_profile:
                prompt_sections.append(f"Kurzvorschau Hub-Kontext: {context_preview}")
        research_prompt_section = str((research_context or {}).get("prompt_section") or "").strip()
        if research_prompt_section:
            prompt_sections.append(
                "Selektierter Research-Kontext ist ausgelagert in "
                f"{str(opencode_context_files.get('research_context_prompt_path') or 'rag_helper/research-context.md')}."
            )
            if not compact_profile:
                research_preview = " ".join(research_prompt_section.split()).strip().lower()[:320]
                if research_preview:
                    prompt_sections.append(f"Kurzvorschau Research-Kontext: {research_preview}")
        if allowed_tools:
            prompt_sections.append(
                "Tool-Scope fuer diesen Task (nur diese Tools verwenden): "
                + ", ".join(str(item) for item in allowed_tools)
            )
        prompt_sections.append(
            f"Worker-Ausfuehrungsprofil: {worker_profile} (source={profile_source})."
        )
        if expected_output_schema and not compact_profile:
            prompt_sections.append(
                "Erwartetes Output-Schema (Kurzfassung): "
                + json.dumps(expected_output_schema, ensure_ascii=False)[:400]
            )
        if stack_diagnostics and not compact_profile:
            prompt_sections.append(get_instruction_layer_service().render_diagnostics_brief(stack_diagnostics))
        prompt_sections.append(
            "Arbeitsverzeichnis fuer Datei-/Shell-Aktionen:\n"
            f"- workspace: {workspace_context.workspace_dir}\n"
            f"- artifacts: {workspace_context.artifacts_dir}\n"
            f"- rag_helper: {workspace_context.rag_helper_dir}\n"
            "Nutze ausschliesslich diesen Workspace fuer neue oder geaenderte Dateien."
        )
        if interactive_terminal:
            if compact_profile:
                prompt_sections.append(
                    "Kompaktmodus aktiv: nutze die Workspace-Dateien als Quelle der Wahrheit und vermeide Kontext-Wiederholung."
                )
            prompt_sections.append(
                "Arbeite direkt im Workspace mit normalem OpenCode-CLI. "
                "Fuehre die gewuenschten Datei- und Verzeichnis-Aenderungen im Workspace aus. "
                "Nutze bei Bedarf `rag_helper/` fuer Hilfsdateien oder ausgelagerten Kontext. "
                "Es ist keine JSON-Antwort erforderlich; Workspace-Aenderungen und Diffs werden nach dem Lauf automatisch erfasst."
            )
        else:
            prompt_sections.append(
                "Antworte ausschliesslich als genau ein JSON-Objekt. "
                "Beachte dafuer die Regeln in "
                f"{str(opencode_context_files.get('response_contract_path') or '.ananta/response-contract.md')} "
                "und setze mindestens eines von 'command' oder 'tool_calls'."
            )
            if allow_complex_shell:
                prompt_sections.append(
                    "Priorisiere `tool_calls` fuer Datei-/Verzeichnis- und Code-Aenderungen. "
                    "Falls ein Shell-Befehl erforderlich ist, liefere einen `command` — "
                    "Pipes (`|`), Redirects (`>`, `<`, `2>&1`) und Chaining (`&&`, `||`, `;`) sind erlaubt."
                )
            else:
                prompt_sections.append(
                    "Priorisiere `tool_calls` fuer Datei-/Verzeichnis- und Code-Aenderungen. "
                    "Falls ein Shell-Befehl erforderlich ist, liefere genau einen einzelnen `command` "
                    "ohne `&&`, `||`, `;`, `>`, `<` oder `|`."
                )
        return "\n\n".join(section for section in prompt_sections if section), {
            "context_bundle_id": execution_context.get("context_bundle_id") or task.get("context_bundle_id"),
            "allowed_tools": allowed_tools,
            "expected_output_schema": expected_output_schema,
            "semantic_output_correction": semantic_output_correction if semantic_output_correction else None,
            "worker_profile": worker_profile,
            "profile_source": profile_source,
            "workspace": {
                "requested": workspace_payload or None,
                "workspace_dir": str(workspace_context.workspace_dir),
                "artifacts_dir": str(workspace_context.artifacts_dir),
                "rag_helper_dir": str(workspace_context.rag_helper_dir),
                "opencode_context_files": opencode_context_files,
            },
            "context_chunk_count": len(context_payload.get("chunks") or []),
            "has_context_text": bool(context_text),
            "retrieval_trace_id": retrieval_trace_link["retrieval_trace_id"],
            "retrieval_context_hash": retrieval_trace_link["retrieval_context_hash"],
            "retrieval_manifest_hash": retrieval_trace_link["retrieval_manifest_hash"],
            "instruction_layers": stack_diagnostics,
            "research_context": {
                "artifact_ids": list((research_context or {}).get("artifact_ids") or []),
                "knowledge_collection_ids": list((research_context or {}).get("knowledge_collection_ids") or []),
                "repo_scope_refs": list((research_context or {}).get("repo_scope_refs") or []),
                "truncated": bool((research_context or {}).get("truncated")),
                "context_char_count": int((research_context or {}).get("context_char_count") or 0),
            }
            if research_context
            else None,
        }

    def _get_system_prompt_for_task(self, tid: str) -> str | None:
        task = get_repository_registry().task_repo.get_by_id(tid)
        if not task:
            return None

        repos = get_repository_registry()
        resolved = resolve_task_role_template(task, repos=repos)
        role_id = resolved.get("role_id")
        template_id = resolved.get("template_id")
        if not template_id:
            return None
        template = repos.template_repo.get_by_id(template_id)
        if not template:
            return None

        prompt = template.prompt_template
        goal_text = ""
        goal_context = ""
        acceptance_criteria: list[str] = []
        goal_id = str(task.goal_id or "").strip()
        if goal_id:
            goal = repos.goal_repo.get_by_id(goal_id)
            if goal:
                goal_text = str(goal.goal or "").strip()
                goal_context = str(goal.context or "").strip()
                acceptance_criteria = [str(item) for item in (goal.acceptance_criteria or []) if str(item or "").strip()]
        variables = {
            "agent_name": current_app.config.get("AGENT_NAME", "Unbekannter Agent"),
            "task_title": task.title or "Kein Titel",
            "task_description": task.description or "Keine Beschreibung",
            "team_goal": goal_text
            or str(task.title or "").strip()
            or str(task.description or "").strip()
            or str(resolved.get("team_name") or "").strip()
            or "aktuelles Teamziel",
            "goal_context": goal_context,
            "acceptance_criteria": "\n".join(f"- {item}" for item in acceptance_criteria),
        }
        if resolved.get("team_name"):
            variables["team_name"] = resolved["team_name"]
        if resolved.get("role_name"):
            variables["role_name"] = resolved["role_name"]
        for key, value in variables.items():
            prompt = prompt.replace("{{" + key + "}}", str(value))
        return prompt

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
        backend = str(backend_used or "").strip().lower()
        requested = str(requested_backend or "auto").strip().lower()
        normalized_profile = normalize_worker_execution_profile(worker_profile)
        normalized_profile_source = str(profile_source or "agent_default").strip().lower() or "agent_default"
        cfg = agent_cfg if isinstance(agent_cfg, dict) else ((current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {})
        runtime_cfg = cfg.get("worker_runtime") if isinstance(cfg.get("worker_runtime"), dict) else {}
        native_runtime_cfg = runtime_cfg.get("native_worker_runtime") if isinstance(runtime_cfg.get("native_worker_runtime"), dict) else {}
        runtime_path = None
        if backend == "ananta-worker":
            runtime_path = "native_worker_pipeline" if bool(native_runtime_cfg.get("enabled", False)) else "sgpt_fallback_proxy"
        dimensions = {
            "requested_backend": requested or "auto",
            "execution_backend": backend or requested or "sgpt",
            "inference_provider": None,
            "inference_model": str(model or "").strip() or None,
            "inference_temperature": TaskScopedExecutionService._normalize_temperature(temperature),
            "inference_base_url": None,
            "inference_target_kind": None,
            "inference_target_provider_type": None,
            "remote_hub": False,
            "instance_id": None,
            "max_hops": None,
            "worker_profile": normalized_profile,
            "profile_source": normalized_profile_source,
            "worker_runtime_path": runtime_path,
        }
        if backend == "codex":
            runtime_cfg = resolve_codex_runtime_config() if has_app_context() else {}
            dimensions.update(
                {
                    "inference_provider": runtime_cfg.get("target_provider") or str(cfg.get("default_provider") or "").strip().lower() or "openai_compatible",
                    "inference_base_url": runtime_cfg.get("base_url"),
                    "inference_target_kind": runtime_cfg.get("target_kind"),
                    "inference_target_provider_type": runtime_cfg.get("target_provider_type"),
                    "remote_hub": bool(runtime_cfg.get("remote_hub")),
                    "instance_id": runtime_cfg.get("instance_id"),
                    "max_hops": runtime_cfg.get("max_hops"),
                }
            )
            return dimensions
        dimensions["inference_provider"] = str(cfg.get("default_provider") or "").strip().lower() or None
        return dimensions

    @staticmethod
    def _terminal_parent_goal_guard(*, tid: str, task: dict, phase: str) -> TaskScopedRouteResponse | None:
        goal_id = str((task or {}).get("goal_id") or "").strip()
        if not goal_id:
            return None
        goal = get_repository_registry().goal_repo.get_by_id(goal_id)
        goal_status = str(getattr(goal, "status", "") or "").strip().lower() if goal is not None else ""
        if goal_status not in {"completed", "failed", "cancelled", "aborted", "timeout"}:
            return None
        update_local_task_status(
            tid,
            str((task or {}).get("status") or "todo"),
            event_type="parent_goal_cancelled",
            event_actor="task_scoped_execution_service",
            event_details={"goal_id": goal_id, "goal_status": goal_status, "phase": phase},
        )
        return TaskScopedRouteResponse(
            data={
                "status": "skipped",
                "reason": "parent_goal_cancelled",
                "goal_status": goal_status,
                "task_id": tid,
                "goal_id": goal_id,
                "phase": phase,
            },
            status="skipped",
            message="Parent goal is terminal",
            code=409,
        )


task_scoped_execution_service = TaskScopedExecutionService()


def get_task_scoped_execution_service() -> TaskScopedExecutionService:
    return task_scoped_execution_service
