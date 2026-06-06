from __future__ import annotations

import time

from agent.common.sgpt import resolve_codex_runtime_config
from agent.research_backend import get_research_backend_preflight, resolve_research_backend_config
from agent.runtime_profiles import resolve_runtime_profile
from agent.governance_modes import resolve_governance_mode
from agent.runtime_policy import review_policy
from agent.services.cli_session_service import get_cli_session_service
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.governance_profile_service import build_effective_policy_profile
from agent.services.integration_registry_service import get_integration_registry_service
from agent.services.operations_observability_service import get_operations_observability_service
from agent.services.critical_workflow_state_service import get_critical_workflow_state_service
from agent.services.repository_registry import get_repository_registry
from agent.services.routing_decision_service import get_routing_decision_service
from agent.services.task_state_machine_service import build_task_state_machine_contract, build_task_status_contract


class ConfigReadModelService:
    """Read-model builders for assistant and dashboard configuration views."""

    def _build_model_routing_read_model(self, cfg: dict, *, task_kind: str) -> dict:
        try:
            from agent.services.model_invocation_service import ModelInvocationService
            from agent.services.model_profile_resolver import RoutingContext

            resolver = ModelInvocationService._get_resolver()
        except Exception as exc:
            return {"status": "error", "reason": str(exc), "profiles": [], "matrix": [], "effective_winner": None}
        if resolver is None:
            return {
                "status": "not_configured",
                "profiles": [],
                "matrix": [],
                "effective_winner": None,
                "legacy": {
                    "default_provider": (cfg or {}).get("default_provider"),
                    "default_model": (cfg or {}).get("default_model"),
                },
            }

        profiles = list(getattr(resolver, "_all_enabled", []) or [])

        def _profile_row(profile) -> dict:
            return {
                "profile_id": profile.profile_id,
                "provider_id": profile.provider_id,
                "model": profile.model,
                "endpoint": profile.base_url,
                "model_role": profile.model_role,
                "context_tokens": profile.context_tokens,
                "cost_class": profile.cost_class,
                "quality_class": profile.quality_class,
                "cloud_allowed": profile.cloud_allowed,
                "block_secret_context": profile.block_secret_context,
                "api_key_env": profile.api_key_env,
                "api_key_redacted": True if profile.api_key_env else None,
                "capabilities": {
                    "tools": bool(profile.supports_tools),
                    "json": bool(profile.supports_json),
                    "streaming": bool(profile.supports_streaming),
                },
            }

        roles = sorted({str(p.model_role or "any") for p in profiles} | {"planner", "coder", "reviewer", "summarizer"})
        matrix = []
        for role in roles:
            result = resolver.resolve(RoutingContext(model_role=role, task_kind=task_kind))
            selected = result.profile
            matrix.append(
                {
                    "task_kind": task_kind,
                    "model_role": role,
                    "primary": selected.profile_id if selected else None,
                    "fallbacks": list(getattr(resolver.rules, "fallback_chain", []) or []),
                    "cloud_allowed": bool(selected.cloud_allowed) if selected else False,
                    "secret_allowed": bool(selected.is_usable_with_secrets()) if selected else False,
                    "supports_tools": bool(selected.supports_tools) if selected else False,
                    "supports_json": bool(selected.supports_json) if selected else False,
                    "final_source": result.final_source,
                    "policy_decisions": [
                        {
                            "rank": decision.rank,
                            "source": decision.source,
                            "profile_id": decision.profile_id,
                            "accepted": decision.accepted,
                            "reason": decision.reason,
                        }
                        for decision in result.decisions
                    ],
                    "blocked_candidates": [
                        {"profile_id": profile_id, "reason": reason}
                        for profile_id, reason in result.blocked_candidates
                    ],
                }
            )
        winner = resolver.resolve(RoutingContext(model_role="planner", task_kind=task_kind))
        return {
            "status": "loaded",
            "profiles": [_profile_row(profile) for profile in profiles],
            "matrix": matrix,
            "benchmark_ranking": resolver.benchmark_ranking_read_model() if hasattr(resolver, "benchmark_ranking_read_model") else {},
            "effective_winner": {
                "profile_id": winner.profile.profile_id if winner.profile else None,
                "provider_id": winner.profile.provider_id if winner.profile else None,
                "model": winner.profile.model if winner.profile else None,
                "final_source": winner.final_source,
                "final_rank": winner.final_rank,
                "blocked_candidates": [
                    {"profile_id": profile_id, "reason": reason}
                    for profile_id, reason in winner.blocked_candidates
                ],
            },
        }

    def _build_retrieval_bundle_telemetry(self, tasks: list[dict], *, max_tasks: int = 200) -> dict:
        repos = get_repository_registry()
        recent_tasks = sorted(
            [item for item in tasks if str(item.get("context_bundle_id") or "").strip()],
            key=lambda task: float(task.get("updated_at") or task.get("created_at") or 0.0),
            reverse=True,
        )[: max(1, int(max_tasks))]

        def _safe_float(value) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        entries: list[dict] = []
        for task in recent_tasks:
            bundle_id = str(task.get("context_bundle_id") or "").strip()
            bundle = repos.context_bundle_repo.get_by_id(bundle_id) if bundle_id else None
            if bundle is None:
                continue
            metadata = dict(bundle.bundle_metadata or {})
            retrieval_hints = dict(metadata.get("retrieval_hints") or {})
            context_policy = dict(metadata.get("context_policy") or {})
            budget = dict(metadata.get("budget") or {})
            strategy = dict(metadata.get("strategy") or {})
            fusion = dict(strategy.get("fusion") or {})
            dedupe = dict(fusion.get("dedupe") or {})
            candidate_counts = dict(fusion.get("candidate_counts") or {})
            all_candidates = max(1, int(candidate_counts.get("all") or 0))
            final_candidates = max(0, int(candidate_counts.get("final") or 0))
            duplicate_rate = min(
                1.0,
                float((dedupe.get("identity_duplicates") or 0) + (dedupe.get("content_duplicates") or 0)) / float(all_candidates),
            )
            noise_rate = min(1.0, max(0.0, float(all_candidates - final_candidates) / float(all_candidates)))
            entries.append(
                {
                    "task_kind": str(
                        retrieval_hints.get("task_kind")
                        or task.get("task_kind")
                        or "unknown"
                    ).strip()
                    or "unknown",
                    "bundle_mode": str(context_policy.get("mode") or "unknown").strip() or "unknown",
                    "window_profile": str(context_policy.get("window_profile") or "unknown").strip() or "unknown",
                    "budget_utilization": min(1.0, max(0.0, _safe_float(budget.get("retrieval_utilization")))),
                    "duplicate_rate": duplicate_rate,
                    "noise_rate": noise_rate,
                }
            )

        def _aggregate(key: str) -> dict[str, dict]:
            groups: dict[str, dict] = {}
            for entry in entries:
                group_key = str(entry.get(key) or "unknown")
                current = groups.setdefault(
                    group_key,
                    {
                        "count": 0,
                        "budget_utilization_sum": 0.0,
                        "duplicate_rate_sum": 0.0,
                        "noise_rate_sum": 0.0,
                    },
                )
                current["count"] += 1
                current["budget_utilization_sum"] += float(entry.get("budget_utilization") or 0.0)
                current["duplicate_rate_sum"] += float(entry.get("duplicate_rate") or 0.0)
                current["noise_rate_sum"] += float(entry.get("noise_rate") or 0.0)
            result: dict[str, dict] = {}
            for group_key, data in sorted(groups.items(), key=lambda item: item[0]):
                count = max(1, int(data["count"]))
                result[group_key] = {
                    "count": int(data["count"]),
                    "avg_budget_utilization": round(float(data["budget_utilization_sum"]) / float(count), 4),
                    "avg_duplicate_rate": round(float(data["duplicate_rate_sum"]) / float(count), 4),
                    "avg_noise_rate": round(float(data["noise_rate_sum"]) / float(count), 4),
                }
            return result

        return {
            "sample_size": len(entries),
            "by_task_kind": _aggregate("task_kind"),
            "by_bundle_mode": _aggregate("bundle_mode"),
            "by_window_profile": _aggregate("window_profile"),
        }

    def _build_critical_workflow_observability(self, tasks: list[dict], *, max_tasks: int = 120, max_proposals_per_task: int = 8) -> dict:
        repos = get_repository_registry()
        workflow_service = get_critical_workflow_state_service()
        recent_tasks = sorted(
            [item for item in tasks if str(item.get("id") or "").strip()],
            key=lambda task: float(task.get("updated_at") or task.get("created_at") or 0.0),
            reverse=True,
        )[: max(1, int(max_tasks))]

        sample_size = 0
        transition_count_total = 0
        blocked_transition_count = 0
        timeout_transition_count = 0
        invalid_replay_count = 0
        stuck_workflow_count = 0
        recovery_attempt_count = 0
        unstable_pattern_count = 0
        state_counts: dict[str, int] = {}
        fallback_causes: dict[str, int] = {}
        seen_proposals: set[str] = set()

        for task in recent_tasks:
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                continue
            for proposal in repos.evolution_proposal_repo.get_by_task_id(task_id, limit=max(1, int(max_proposals_per_task))):
                proposal_id = str(getattr(proposal, "id", "") or "").strip()
                if not proposal_id or proposal_id in seen_proposals:
                    continue
                seen_proposals.add(proposal_id)
                metadata = dict(getattr(proposal, "proposal_metadata", None) or {})
                workflow_state = workflow_service.materialize_record(
                    metadata.get("workflow_state"),
                    workflow_type="evolution_proposal",
                )
                replay = workflow_service.replay(workflow_state, workflow_type="evolution_proposal")
                timeout = workflow_service.inspect_timeout(workflow_state, workflow_type="evolution_proposal")
                history = [
                    event
                    for event in list(workflow_state.get("history") or [])
                    if str((event or {}).get("event_type") or "") == "workflow_transition"
                ]

                sample_size += 1
                transition_count_total += len(history)
                blocked_events = sum(1 for event in history if str((event or {}).get("to_state") or "").strip().lower() == "blocked")
                timeout_events = sum(1 for event in history if str((event or {}).get("to_state") or "").strip().lower() == "timeout")
                blocked_transition_count += blocked_events
                timeout_transition_count += timeout_events
                if not bool(replay.get("valid")):
                    invalid_replay_count += 1
                if bool(timeout.get("stuck")):
                    stuck_workflow_count += 1
                recovery_attempts = int(workflow_state.get("recovery_attempts") or 0)
                if recovery_attempts > 0:
                    recovery_attempt_count += 1

                if blocked_events > 1 or len(history) >= 8 or recovery_attempts > 0:
                    unstable_pattern_count += 1

                state_key = str(workflow_state.get("state") or "unknown").strip().lower() or "unknown"
                state_counts[state_key] = int(state_counts.get(state_key) or 0) + 1

                fallback = metadata.get("last_fallback")
                if isinstance(fallback, dict):
                    cause = str(fallback.get("cause") or "").strip().lower()
                    if cause:
                        fallback_causes[cause] = int(fallback_causes.get(cause) or 0) + 1

        def _top_counts(source: dict[str, int], *, limit: int = 8) -> list[dict]:
            rows = sorted(source.items(), key=lambda item: (-int(item[1]), item[0]))[: max(1, int(limit))]
            return [{"key": key, "count": int(count)} for key, count in rows]

        denom = float(sample_size) if sample_size > 0 else 1.0
        return {
            "sample_size": sample_size,
            "transitions_total": transition_count_total,
            "blocked_transition_count": blocked_transition_count,
            "timeout_transition_count": timeout_transition_count,
            "invalid_replay_count": invalid_replay_count,
            "stuck_workflow_count": stuck_workflow_count,
            "recovery_attempted_count": recovery_attempt_count,
            "unstable_pattern_count": unstable_pattern_count,
            "rates": {
                "blocked_transition_rate": round(float(blocked_transition_count) / denom, 4),
                "invalid_replay_rate": round(float(invalid_replay_count) / denom, 4),
                "unstable_pattern_rate": round(float(unstable_pattern_count) / denom, 4),
            },
            "state_distribution": _top_counts(state_counts),
            "fallback_causes": _top_counts(fallback_causes),
        }

    def assistant_read_model(
        self,
        *,
        cfg: dict,
        is_admin: bool,
        capability_contract_builder,
        allowed_tools_resolver,
        capabilities_describer,
        settings_inventory_builder,
        settings_summary_builder,
        automation_snapshot_builder,
    ) -> dict:
        repos = get_repository_registry()
        teams = [team.model_dump() for team in repos.team_repo.get_all()]
        roles = [role.model_dump() for role in repos.role_repo.get_all()]
        templates = [template.model_dump() for template in repos.template_repo.get_all()]
        agents = [agent.model_dump() for agent in repos.agent_repo.get_all()]
        for agent in agents:
            if "token" in agent:
                agent["token"] = "***"

        capability_contract = capability_contract_builder(cfg)
        allowed_tools = allowed_tools_resolver(cfg, is_admin=is_admin, contract=capability_contract)
        capability_meta = capabilities_describer(capability_contract, allowed_tools=allowed_tools, is_admin=is_admin)
        settings_inventory = settings_inventory_builder()
        settings_summary = settings_summary_builder(cfg, teams, templates)
        runtime_preflight = (get_integration_registry_service().list_execution_backends(include_preflight=True).get("preflight") or {})
        return {
            "config": {"effective": cfg, "has_sensitive_redactions": True},
            "teams": {"count": len(teams), "items": teams},
            "roles": {"count": len(roles), "items": roles},
            "templates": {"count": len(templates), "items": templates},
            "agents": {"count": len(agents), "items": agents},
            "settings": {
                "summary": settings_summary,
                "runtime_telemetry": {
                    "providers": dict(runtime_preflight.get("providers") or {}),
                    "cli_backends": dict(runtime_preflight.get("cli_backends") or {}),
                },
                "editable_inventory": settings_inventory,
                "editable_count": len(settings_inventory),
            },
            "automation": automation_snapshot_builder(),
            "assistant_capabilities": capability_meta,
            "context_timestamp": int(time.time()),
        }

    def dashboard_read_model(
        self,
        *,
        cfg: dict,
        benchmark_task_kind: str,
        benchmark_task_kinds: set[str] | list[str],
        include_task_snapshot: bool,
        benchmark_rows_builder,
        benchmark_recommendation_builder,
        system_health_builder,
        contract_catalog_builder,
        hub_copilot_summary_builder,
        context_policy_summary_builder,
        artifact_flow_summary_builder,
        planning_learning_summary_builder=None,
    ) -> dict:
        repos = get_repository_registry()
        teams = [team.model_dump() for team in repos.team_repo.get_all()]
        roles = [role.model_dump() for role in repos.role_repo.get_all()]
        templates = [template.model_dump() for template in repos.template_repo.get_all()]
        agents = [agent.model_dump() for agent in repos.agent_repo.get_all()]
        tasks = [task.model_dump() for task in repos.task_repo.get_all()] if include_task_snapshot else []
        for agent in agents:
            if "token" in agent:
                agent["token"] = "***"

        bench_rows, bench = benchmark_rows_builder(task_kind=benchmark_task_kind, top_n=8)
        valid_task_kind = benchmark_task_kind if benchmark_task_kind in benchmark_task_kinds else "analysis"
        benchmark_recommendation = benchmark_recommendation_builder(task_kind=valid_task_kind, cfg=cfg)
        runtime_preflight = (get_integration_registry_service().list_execution_backends(include_preflight=True).get("preflight") or {})
        retrieval_telemetry = self._build_retrieval_bundle_telemetry(tasks if include_task_snapshot else [])
        operations_observability = get_operations_observability_service().build_dashboard_summary(
            tasks=tasks if include_task_snapshot else [],
            config=cfg,
        )
        critical_workflow_observability = self._build_critical_workflow_observability(tasks if include_task_snapshot else [])
        contract_catalog = contract_catalog_builder()
        task_status_contract = build_task_status_contract()
        task_state_machine = build_task_state_machine_contract()
        llm_cfg = (cfg or {}).get("llm_config", {}) if isinstance((cfg or {}).get("llm_config"), dict) else {}
        effective_default_provider = (
            str(llm_cfg.get("provider") or cfg.get("default_provider") or "").strip().lower() or None
        )
        effective_default_model = str(llm_cfg.get("model") or cfg.get("default_model") or "").strip() or None
        explicit_override = {
            "provider": str(llm_cfg.get("provider") or "").strip().lower() or None,
            "model": str(llm_cfg.get("model") or "").strip() or None,
            "active": bool(llm_cfg.get("provider") or llm_cfg.get("model")),
            "source": {
                "provider": "agent_config.llm_config.provider" if llm_cfg.get("provider") else "agent_config.default_provider",
                "model": "agent_config.llm_config.model" if llm_cfg.get("model") else "agent_config.default_model",
            },
        }
        configured_runtime = {
            "provider": explicit_override["provider"] or effective_default_provider,
            "model": explicit_override["model"] or effective_default_model,
            "mode": "explicit_override" if explicit_override["active"] else "default_config",
            "source": explicit_override["source"] if explicit_override["active"] else {
                "provider": "agent_config.llm_config.provider" if llm_cfg.get("provider") else "agent_config.default_provider",
                "model": "agent_config.llm_config.model" if llm_cfg.get("model") else "agent_config.default_model",
            },
        }
        recommended_runtime = benchmark_recommendation.get("recommended") if isinstance(benchmark_recommendation, dict) else None
        codex_runtime = resolve_codex_runtime_config()
        routing_decision_service = get_routing_decision_service()
        routing_fallback_policy = routing_decision_service.resolve_fallback_policy(cfg)
        planning_learning_summary = planning_learning_summary_builder(cfg) if callable(planning_learning_summary_builder) else {}
        effective_runtime = {
            "provider": (recommended_runtime or {}).get("provider") or configured_runtime["provider"],
            "model": (recommended_runtime or {}).get("model") or configured_runtime["model"],
            "mode": "benchmark_recommendation" if recommended_runtime else configured_runtime["mode"],
            "selection_source": (
                (recommended_runtime or {}).get("selection_source")
                or configured_runtime["source"]["provider"]
            ),
            "benchmark_applied": bool(recommended_runtime),
            "replaces_configured": bool(
                recommended_runtime
                and (
                    (recommended_runtime or {}).get("provider") != configured_runtime["provider"]
                    or (recommended_runtime or {}).get("model") != configured_runtime["model"]
                )
            ),
            "configured": configured_runtime,
        }
        execution_default_backend = str(cfg.get("sgpt_execution_backend") or "sgpt").strip().lower()
        routing_decision_chain = routing_decision_service.build_decision_chain(
            cfg=cfg,
            task_kind=valid_task_kind,
            requested={},
            effective={
                "provider": effective_runtime.get("provider"),
                "model": effective_runtime.get("model"),
                "execution_backend": execution_default_backend,
                "codex_target_provider": codex_runtime.get("target_provider"),
                "codex_target_kind": codex_runtime.get("target_kind"),
            },
            sources={
                "provider_source": effective_runtime.get("selection_source"),
                "model_source": effective_runtime.get("selection_source"),
            },
            recommendation=recommended_runtime,
            execution_backend={
                "backend": execution_default_backend,
                "reason": "agent_config.sgpt_execution_backend_or_default",
            },
        )
        research_backend_cfg = resolve_research_backend_config(agent_cfg=cfg)
        research_backend_review = review_policy(cfg, research_backend_cfg.get("provider"), "research")
        exposure_policy = get_exposure_policy_service().normalize_exposure_policy((cfg or {}).get("exposure_policy"))
        cli_session_mode = (cfg or {}).get("cli_session_mode") if isinstance((cfg or {}).get("cli_session_mode"), dict) else {}
        propose_policy_cfg = (cfg or {}).get("propose_policy") if isinstance((cfg or {}).get("propose_policy"), dict) else {}
        propose_policy_summary = {
            "context_compaction_enabled": bool(propose_policy_cfg.get("context_compaction_enabled", True)),
            "context_compaction_required": bool(propose_policy_cfg.get("context_compaction_required", False)),
            "context_compactor_fail_open": bool(propose_policy_cfg.get("context_compactor_fail_open", False)),
            "context_compactor_profile": str(propose_policy_cfg.get("context_compactor_profile") or "default").strip().lower() or "default",
        }
        task_counts = {"total": len(tasks), "completed": 0, "failed": 0, "todo": 0, "in_progress": 0, "blocked": 0}
        recent_timeline = []
        if include_task_snapshot:
            for task in tasks:
                status = str(task.get("status") or "todo").strip().lower()
                if status not in task_counts:
                    task_counts[status] = 0
                task_counts[status] += 1

            recent_tasks = sorted(
                tasks,
                key=lambda task: float(task.get("updated_at") or task.get("created_at") or 0.0),
                reverse=True,
            )[:30]
            recent_timeline = [
                {
                    "task_id": task.get("id"),
                    "title": task.get("title"),
                    "status": task.get("status"),
                    "updated_at": task.get("updated_at") or task.get("created_at"),
                }
                for task in recent_tasks
            ]
        return {
            "config": {"effective": cfg, "has_sensitive_redactions": True},
            "system_health": system_health_builder(),
            "contracts": {
                "version": contract_catalog.get("version") or "v1",
                "schema_count": len(contract_catalog.get("schemas") or {}),
                "task_statuses": task_status_contract.model_dump(),
                "task_state_machine": task_state_machine.model_dump(),
            },
            "teams": {"count": len(teams), "items": teams},
            "roles": {"count": len(roles), "items": roles},
            "templates": {"count": len(templates), "items": templates},
            "agents": {"count": len(agents), "items": agents},
            "tasks": {"included": include_task_snapshot, "counts": task_counts, "recent": recent_timeline},
            "llm_configuration": {
                "governance_mode": resolve_governance_mode(cfg),
                "runtime_profile": resolve_runtime_profile(cfg),
                "defaults": {
                    "provider": effective_default_provider,
                    "model": effective_default_model,
                    "source": {
                        "provider": "agent_config.llm_config.provider" if llm_cfg.get("provider") else "agent_config.default_provider",
                        "model": "agent_config.llm_config.model" if llm_cfg.get("model") else "agent_config.default_model",
                    },
                },
                "explicit_override": explicit_override,
                "effective_runtime": effective_runtime,
                "model_routing": self._build_model_routing_read_model(cfg, task_kind=valid_task_kind),
                "hub_copilot": hub_copilot_summary_builder(cfg),
                "context_bundle_policy": context_policy_summary_builder(cfg),
                "artifact_flow": artifact_flow_summary_builder(cfg),
                "planning_learning": planning_learning_summary,
                "opencode_runtime": {
                    "tool_mode": str((((cfg or {}).get("opencode_runtime") or {}).get("tool_mode") or "full")).strip().lower()
                    if isinstance((cfg or {}).get("opencode_runtime"), dict)
                    else "full",
                    "execution_mode": str((((cfg or {}).get("opencode_runtime") or {}).get("execution_mode") or "live_terminal")).strip().lower()
                    if isinstance((cfg or {}).get("opencode_runtime"), dict)
                    else "live_terminal",
                    "interactive_launch_mode": str((((cfg or {}).get("opencode_runtime") or {}).get("interactive_launch_mode") or "run")).strip().lower()
                    if isinstance((cfg or {}).get("opencode_runtime"), dict)
                    else "run",
                },
                "worker_runtime": {
                    "workspace_root": (
                        str((((cfg or {}).get("worker_runtime") or {}).get("workspace_root") or "")).strip() or None
                    )
                    if isinstance((cfg or {}).get("worker_runtime"), dict)
                    else None,
                },
                "propose_policy": propose_policy_summary,
                "research_backend": {
                    "provider": research_backend_cfg.get("provider"),
                    "display_name": research_backend_cfg.get("display_name"),
                    "enabled": bool(research_backend_cfg.get("enabled")),
                    "configured": bool(research_backend_cfg.get("configured")),
                    "mode": research_backend_cfg.get("mode"),
                    "command": research_backend_cfg.get("command"),
                    "working_dir": research_backend_cfg.get("working_dir"),
                    "working_dir_exists": bool(research_backend_cfg.get("working_dir_exists")),
                    "binary_path": research_backend_cfg.get("binary_path"),
                    "binary_available": bool(research_backend_cfg.get("binary_path")),
                    "result_format": research_backend_cfg.get("result_format"),
                    "timeout_seconds": research_backend_cfg.get("timeout_seconds"),
                    "docker_binary": research_backend_cfg.get("docker_binary"),
                    "docker_available": bool(research_backend_cfg.get("docker_available")),
                    "sandbox_image": research_backend_cfg.get("sandbox_image"),
                    "sandbox_network": research_backend_cfg.get("sandbox_network"),
                    "sandbox_workdir": research_backend_cfg.get("sandbox_workdir"),
                    "sandbox_mount_repo": bool(research_backend_cfg.get("sandbox_mount_repo")),
                    "sandbox_read_only": bool(research_backend_cfg.get("sandbox_read_only")),
                    "supported_providers": research_backend_cfg.get("supported_providers") or [],
                    "providers": get_research_backend_preflight(agent_cfg=cfg),
                    "review_policy": research_backend_review,
                },
                "exposure": {
                    "openai_compat": exposure_policy.get("openai_compat") or {},
                    "mcp": exposure_policy.get("mcp") or {},
                },
                "cli_sessions": {
                    "policy": {
                        "enabled": bool(cli_session_mode.get("enabled", False)),
                        "stateful_backends": list(cli_session_mode.get("stateful_backends") or []),
                        "max_turns_per_session": int(cli_session_mode.get("max_turns_per_session") or 40),
                        "max_sessions": int(cli_session_mode.get("max_sessions") or 200),
                        "allow_task_scoped_auto_session": bool(cli_session_mode.get("allow_task_scoped_auto_session", True)),
                        "reuse_scope": str(cli_session_mode.get("reuse_scope") or "task"),
                        "native_opencode_sessions": bool(cli_session_mode.get("native_opencode_sessions", False)),
                    },
                    "runtime": get_cli_session_service().snapshot(),
                },
                "routing_split": {
                    "inference": {
                        "default_provider": effective_default_provider,
                        "default_model": effective_default_model,
                    },
                    "execution": {
                        "default_backend": execution_default_backend,
                        "codex_target_provider": codex_runtime.get("target_provider"),
                        "codex_target_kind": codex_runtime.get("target_kind"),
                        "codex_target_provider_type": codex_runtime.get("target_provider_type"),
                        "codex_target_base_url": codex_runtime.get("base_url"),
                        "codex_remote_hub": bool(codex_runtime.get("remote_hub")),
                        "codex_instance_id": codex_runtime.get("instance_id"),
                        "codex_max_hops": codex_runtime.get("max_hops"),
                        "codex_diagnostics": list(codex_runtime.get("diagnostics") or []),
                    },
                    "decision_chain": routing_decision_chain,
                    "fallback_policy": routing_fallback_policy,
                },
                "runtime_telemetry": {
                    "providers": dict(runtime_preflight.get("providers") or {}),
                    "cli_backends": dict(runtime_preflight.get("cli_backends") or {}),
                    "research_backends": dict(runtime_preflight.get("research_backends") or {}),
                    "retrieval_bundles": retrieval_telemetry,
                    "operations": operations_observability,
                    "critical_workflows": critical_workflow_observability,
                },
                "effective_policy_profile": build_effective_policy_profile(cfg),
            },
            "benchmarks": {
                "task_kind": valid_task_kind,
                "updated_at": bench.get("updated_at"),
                "items": bench_rows,
                "recommendation": benchmark_recommendation,
            },
            "context_timestamp": int(time.time()),
        }


config_read_model_service = ConfigReadModelService()


def get_config_read_model_service() -> ConfigReadModelService:
    return config_read_model_service
