"""VACGE-001/002: ConfigGraphBuilderService.

Builds an ``ananta_configuration_graph.v1`` snapshot from all runtime-active
configuration sources:
  - docs/agent-profiles/profile-map.json   (agent profiles)
  - AGENTS.md / profile AGENTS.md          (instruction layers)
  - agent/services/planning_utils.py       (goal templates)
  - AnantaToolRegistryService              (tools + tool groups)
  - PathAiModePolicyService                (path rules)
  - EmbeddingProviderConfigService         (embedding models)

Every node carries a ``source_file`` ref and a ``runtime_active`` flag.
Stale / hardcoded sources are marked with a ``stale`` diagnostic.
Missing or conflicting sources produce diagnostics instead of crashing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ── Re-exports from sub-modules (backward-compatible public API) ───────────────
from agent.services.config_graph_models import (  # noqa: F401
    GRAPH_SCHEMA, ALL_EDGE_TYPES, ALL_NODE_TYPES,
    EDGE_ACTIVATES, EDGE_APPLIES_TO_PATH, EDGE_APPLIES_TO_PATH_BUNDLE,
    EDGE_ASSIGNED_TO, EDGE_BLOCKED_BY_POLICY, EDGE_CLONED_FROM,
    EDGE_CONTAINS, EDGE_CONTROLS_WORKER, EDGE_CREATES_SUBTASK,
    EDGE_DEPENDS_ON, EDGE_EFFECTIVE_AFTER_MERGE, EDGE_EMITS_TRACE,
    EDGE_EXECUTES_STEP, EDGE_FALLS_BACK_TO, EDGE_HANDS_OFF_ARTIFACT_TO,
    EDGE_HANDS_OFF_TO, EDGE_INHERITS_FROM, EDGE_MAY_CALL_TOOL,
    EDGE_OVERRIDES, EDGE_OVERRIDES_HUB_DEFAULT, EDGE_READS_PATH,
    EDGE_REQUIRES_APPROVAL, EDGE_ROUTES_TASK_TO_WORKER, EDGE_ROUTES_TO_BACKEND,
    EDGE_USES_CONTEXT_SOURCE, EDGE_USES_EMBEDDING_MODEL, EDGE_USES_HUB_DEFAULT,
    EDGE_USES_MODEL, EDGE_USES_PROFILE, EDGE_USES_RESTRICTED_INFERENCE,
    EDGE_USES_TEMPLATE, EDGE_USES_TEMPLATE_VARIANT, EDGE_VERIFIES_WITH,
    EDGE_WRITES_PATH,
    NODE_AGENT_INSTANCE, NODE_AGENT_PROFILE, NODE_ARTIFACT_RULE,
    NODE_CLONE_SOURCE, NODE_CODECOMPASS_PROFILE, NODE_CODECOMPASS_RANKING,
    NODE_CONTEXT_SOURCE, NODE_EMBEDDING_MODEL, NODE_FALLBACK_CHAIN,
    NODE_GOAL_TEMPLATE, NODE_HANDOFF_RULE, NODE_HUB,
    NODE_INSTRUCTION_LAYER, NODE_MODEL_PROFILE, NODE_MODEL_PROVIDER,
    NODE_PATH_CONFIG_BUNDLE, NODE_PATH_RULE, NODE_POLICY,
    NODE_RAG_PROFILE, NODE_RESTRICTED_INFERENCE_MODEL, NODE_RESTRICTED_INFERENCE_ROOT,
    NODE_RESTRICTED_INFERENCE_TASK, NODE_ROLE, NODE_ROUTING_RULE,
    NODE_RUNTIME_OVERRIDE, NODE_SUBTASK_STEP, NODE_SURFACE,
    NODE_TASK_KIND, NODE_TASKFLOW, NODE_TASKFLOW_STEP,
    NODE_TEMPLATE_VARIANT, NODE_TOOL, NODE_TOOL_GROUP,
    NODE_TRACE_EVENT, NODE_VERIFICATION_RULE, NODE_WORKER_ADAPTER,
    NODE_WORKER_BACKEND, NODE_WORKER_INSTANCE, NODE_WRITE_RULE,
    PATH_CHARACTER_ANALYSIS, PATH_CHARACTER_CREATIVE, PATH_CHARACTER_EXPLAIN,
    PATH_CHARACTER_LABELS, PATH_CHARACTER_MAINTENANCE, PATH_CHARACTER_OPS,
    PATH_CHARACTER_TEST, PATH_CHARACTER_UNKNOWN, _ROLE_CHARACTER_RULES,
    VIEW_AGENT_RUNTIME, VIEW_CONFIGURATION_OVERVIEW, VIEW_CONTEXT_PIPELINE,
    VIEW_EFFECTIVE_CONFIG, VIEW_IDS, VIEW_PLANNING_FLOW,
    VIEW_POLICY_PATH, VIEW_PROFILE_ACTIVATION,
    ConfigGraph, ConfigGraphEdge, ConfigGraphNode,
)
from agent.services.config_graph_classifiers import (  # noqa: F401
    _CONTEXT_AUTHORITY, _EXECUTE_CONTRACT, _SCOPE_MUST_NOT,
    _build_behavior_dimensions, _classify_profile_character, _classify_rule_character,
)


# ── Builder ───────────────────────────────────────────────────────────────────

class ConfigGraphBuilderService:
    """Assembles a ConfigGraph from all available config sources.

    Parameters
    ----------
    repo_root:
        Repo root path. Defaults to 3 levels up from this file.
    user_config:
        Top-level Ananta config dict (for path_ai_modes, models, etc.).
    """

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        user_config: dict[str, Any] | None = None,
    ) -> None:
        self._root = Path(repo_root or Path(__file__).parents[2]).resolve()
        self._config = dict(user_config or {})

    def build(self) -> ConfigGraph:
        graph = ConfigGraph()
        self._add_root_instruction_layer(graph)
        self._add_agent_profiles(graph)
        self._add_surfaces(graph)
        self._add_tools(graph)
        self._add_path_rules(graph)
        self._add_context_sources(graph)
        self._add_models(graph)
        self._add_planning_templates(graph)
        self._build_views(graph)
        return graph

    # ── Instruction layers ────────────────────────────────────────────────────

    def _add_root_instruction_layer(self, graph: ConfigGraph) -> None:
        root_agents = self._root / "AGENTS.md"
        exists = root_agents.exists()
        node = ConfigGraphNode(
            id="instruction_layer::root",
            node_type=NODE_INSTRUCTION_LAYER,
            label="Root AGENTS.md",
            source_file=str(root_agents.relative_to(self._root)) if exists else "AGENTS.md",
            runtime_active=exists,
            data={"scope": "global", "overridable": False},
            diagnostics=[] if exists else ["root AGENTS.md not found"],
        )
        graph.add_node(node)
        graph.add_to_view(VIEW_PROFILE_ACTIVATION, node.id)

    # ── Agent profiles ────────────────────────────────────────────────────────

    def _add_agent_profiles(self, graph: ConfigGraph) -> None:
        profile_map_path = self._root / "docs/agent-profiles/profile-map.json"
        if not profile_map_path.exists():
            graph.diagnostics.append("profile-map.json not found")
            return
        try:
            profile_map = json.loads(profile_map_path.read_text(encoding="utf-8"))
        except Exception as exc:
            graph.diagnostics.append(f"profile-map.json parse error: {exc}")
            return

        for profile_id, pdata in (profile_map.get("profiles") or {}).items():
            node_id = f"agent_profile::{profile_id}"
            agents_file = str(pdata.get("agents_file") or "")
            agents_path = self._root / agents_file if agents_file else None
            agents_exists = bool(agents_path and agents_path.exists())

            diags = []
            if agents_file and not agents_exists:
                diags.append(f"agents_file not found: {agents_file}")

            character = _classify_profile_character(pdata.get("primary_role") or "", profile_id)
            pdata_with_id = {**pdata, "profile_id": profile_id}
            node = ConfigGraphNode(
                id=node_id,
                node_type=NODE_AGENT_PROFILE,
                label=profile_id,
                source_file=str(profile_map_path.relative_to(self._root)),
                source_kind="profile_map",
                source_pointer=f"/profiles/{profile_id}",
                writable=True,
                runtime_active=True,
                declared_value=dict(pdata),
                effective_value=dict(pdata),
                data={
                    "profile_id": profile_id,
                    "agents_file": agents_file,
                    "primary_role": pdata.get("primary_role") or "",
                    "activation": list(pdata.get("activation") or []),
                    "allowed_task_kinds": list(pdata.get("allowed_task_kinds") or []),
                    "code_change_policy": pdata.get("code_change_policy") or "",
                    "context_policy_hint": pdata.get("context_policy_hint") or "",
                    "path_character": character,
                    "path_character_label": PATH_CHARACTER_LABELS.get(character, "Allgemein"),
                    "behavior_dimensions": _build_behavior_dimensions(pdata_with_id),
                },
                diagnostics=diags,
            )
            graph.add_node(node)
            graph.add_to_view(VIEW_PROFILE_ACTIVATION, node_id)

            # Instruction layer for this profile
            if agents_exists:
                layer_id = f"instruction_layer::{profile_id}"
                layer = ConfigGraphNode(
                    id=layer_id,
                    node_type=NODE_INSTRUCTION_LAYER,
                    label=f"AGENTS.md ({profile_id})",
                    source_file=agents_file,
                    source_kind="agents_md",
                    source_pointer=None,
                    writable=False,
                    runtime_active=True,
                    data={"profile_id": profile_id, "overridable": True},
                )
                graph.add_node(layer)
                graph.add_edge(ConfigGraphEdge(
                    source=node_id, target=layer_id,
                    edge_type=EDGE_CONTAINS, source_ref=agents_file,
                ))
                graph.add_edge(ConfigGraphEdge(
                    source=layer_id, target="instruction_layer::root",
                    edge_type=EDGE_INHERITS_FROM,
                ))
                graph.add_to_view(VIEW_PROFILE_ACTIVATION, layer_id)

            # Role node
            role = pdata.get("primary_role") or ""
            if role:
                role_id = f"role::{role}"
                if role_id not in graph.nodes:
                    graph.add_node(ConfigGraphNode(
                        id=role_id,
                        node_type=NODE_ROLE,
                        label=role,
                        runtime_active=True,
                        data={"role_id": role},
                    ))
                graph.add_edge(ConfigGraphEdge(
                    source=node_id, target=role_id, edge_type=EDGE_ASSIGNED_TO,
                ))

    # ── Surfaces ──────────────────────────────────────────────────────────────

    def _add_surfaces(self, graph: ConfigGraph) -> None:
        surfaces = [
            ("ai_snake_chat", "AI Snake Chat (TUI)", "client_surfaces/operator_tui"),
            ("ananta_worker", "Ananta Worker", "agent/common/sgpt_tool_loop.py"),
            ("opencode", "OpenCode Adapter", ""),
            ("hermes", "Hermes Adapter", ""),
        ]
        for sid, label, ref in surfaces:
            node_id = f"surface::{sid}"
            graph.add_node(ConfigGraphNode(
                id=node_id,
                node_type=NODE_SURFACE,
                label=label,
                source_file=ref or None,
                runtime_active=bool(ref),
                data={"surface_id": sid},
            ))
            # Edge to matching agent profile if exists
            profile_id = f"agent_profile::{sid}"
            if profile_id in graph.nodes:
                graph.add_edge(ConfigGraphEdge(
                    source=node_id, target=profile_id, edge_type=EDGE_USES_PROFILE,
                ))
            graph.add_to_view(VIEW_PROFILE_ACTIVATION, node_id)
            graph.add_to_view(VIEW_AGENT_RUNTIME, node_id)

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _add_tools(self, graph: ConfigGraph) -> None:
        try:
            from agent.services.ananta_tool_registry_service import AnantaToolRegistryService
            registry = AnantaToolRegistryService()
            specs = registry.list_tools()
        except Exception as exc:
            graph.diagnostics.append(f"tool registry unavailable: {exc}")
            return

        groups: dict[str, str] = {}  # group_name → group node_id

        for spec in specs:
            name = str(getattr(spec, "name", "") or "")
            if not name:
                continue
            parts = name.split(".")
            group = parts[0] if len(parts) > 1 else "core"
            tool_id = f"tool::{name}"

            # Group node
            group_id = f"tool_group::{group}"
            if group_id not in graph.nodes:
                graph.add_node(ConfigGraphNode(
                    id=group_id,
                    node_type=NODE_TOOL_GROUP,
                    label=f"Tool-Gruppe: {group}",
                    runtime_active=True,
                    data={"group": group},
                ))
                groups[group] = group_id

            desc = getattr(spec, "description", "") or ""
            risk_class = str(getattr(spec, "risk_class", "") or "")
            requires_approval = bool(getattr(spec, "requires_approval", False))

            graph.add_node(ConfigGraphNode(
                id=tool_id,
                node_type=NODE_TOOL,
                label=name,
                runtime_source="AnantaToolRegistryService",
                runtime_active=True,
                data={
                    "name": name,
                    "description": str(desc)[:200],
                    "risk_class": risk_class,
                    "requires_approval": requires_approval,
                    "group": group,
                },
            ))
            graph.add_edge(ConfigGraphEdge(
                source=group_id, target=tool_id, edge_type=EDGE_CONTAINS,
            ))
            graph.add_to_view(VIEW_AGENT_RUNTIME, tool_id)

            # Approval policy node
            if requires_approval:
                policy_id = f"policy::approval::{name}"
                if policy_id not in graph.nodes:
                    graph.add_node(ConfigGraphNode(
                        id=policy_id,
                        node_type=NODE_POLICY,
                        label=f"Approval: {name}",
                        runtime_active=True,
                        data={"policy_type": "approval", "tool": name},
                    ))
                graph.add_edge(ConfigGraphEdge(
                    source=tool_id, target=policy_id, edge_type=EDGE_REQUIRES_APPROVAL,
                ))

    # ── Path rules ────────────────────────────────────────────────────────────

    def _add_path_rules(self, graph: ConfigGraph) -> None:
        raw_rules = list(self._config.get("path_ai_modes") or [])
        if not raw_rules:
            graph.diagnostics.append("path_ai_modes: no rules configured (all paths open)")
            return

        for i, rule in enumerate(raw_rules):
            if not isinstance(rule, dict):
                continue
            glob = str(rule.get("path_glob") or f"rule_{i}")
            rule_id = f"path_rule::{glob}"
            blocked = list(rule.get("blocked_ai_modes") or [])
            allowed = list(rule.get("allowed_ai_modes") or [])

            node = ConfigGraphNode(
                id=rule_id,
                node_type=NODE_PATH_RULE,
                label=glob,
                source_file="user.json",
                source_kind="user_config",
                source_pointer=f"/path_ai_modes/{i}",
                writable=True,
                runtime_active=True,
                declared_value=dict(rule),
                effective_value=dict(rule),
                data={
                    "path_glob": glob,
                    "blocked_ai_modes": blocked,
                    "allowed_ai_modes": allowed,
                    "allowed_model_engines": list(rule.get("allowed_model_engines") or []),
                    "allow_hidden_states": bool(rule.get("allow_hidden_states", True)),
                    "allow_logits": bool(rule.get("allow_logits", True)),
                    "allow_attention": bool(rule.get("allow_attention", True)),
                    "allow_free_text_generation": rule.get("allow_free_text_generation", True),
                    "allow_tool_decision_from_model_text": bool(rule.get("allow_tool_decision_from_model_text", True)),
                    "allow_code_generation": rule.get("allow_code_generation", True),
                    "require_controlled_write_policy": bool(rule.get("require_controlled_write_policy", False)),
                    "llm_scope": str(rule.get("llm_scope") or ""),
                    "max_input_chars": int(rule.get("max_input_chars") or 0),
                    "max_batch_size": int(rule.get("max_batch_size") or 0),
                    "priority": int(rule.get("priority") or 0),
                    "rule_character": _classify_rule_character(blocked, allowed),
                },
            )
            if "full_llm" in blocked and not allowed:
                node.diagnostics.append("full_llm blocked without explicit allow list")

            graph.add_node(node)
            graph.add_to_view(VIEW_POLICY_PATH, rule_id)

            # Link blocked modes as policy nodes
            for mode in blocked:
                policy_id = f"policy::block_mode::{mode}"
                if policy_id not in graph.nodes:
                    graph.add_node(ConfigGraphNode(
                        id=policy_id,
                        node_type=NODE_POLICY,
                        label=f"Block: {mode}",
                        runtime_active=True,
                        data={"policy_type": "block_ai_mode", "mode": mode},
                    ))
                graph.add_edge(ConfigGraphEdge(
                    source=rule_id, target=policy_id,
                    edge_type=EDGE_BLOCKED_BY_POLICY,
                    policy_effect=f"block_{mode}",
                ))

    # ── Context sources ───────────────────────────────────────────────────────

    def _add_context_sources(self, graph: ConfigGraph) -> None:
        sources = [
            ("codecompass", "CodeCompass", NODE_CODECOMPASS_PROFILE,
             "agent/services/codecompass_context_service.py"),
            ("rag_helper", "RAG Helper Index", NODE_RAG_PROFILE,
             "agent/services/rag_helper_index_service.py"),
            ("pre_model_context", "Pre-Model Context Orchestrator", NODE_CONTEXT_SOURCE,
             "agent/services/pre_model_context_orchestrator.py"),
            ("restricted_inference", "Restricted Transformer Inference", NODE_CONTEXT_SOURCE,
             "agent/services/restricted_model_inference_service.py"),
        ]
        for sid, label, ntype, ref in sources:
            src_path = self._root / ref
            node = ConfigGraphNode(
                id=f"context_source::{sid}",
                node_type=ntype,
                label=label,
                source_file=ref,
                runtime_active=src_path.exists(),
                data={"source_id": sid},
            )
            if not src_path.exists():
                node.diagnostics.append(f"source file not found: {ref}")
            graph.add_node(node)
            graph.add_to_view(VIEW_CONTEXT_PIPELINE, node.id)

    # ── Models ────────────────────────────────────────────────────────────────

    def _add_models(self, graph: ConfigGraph) -> None:
        emb_cfg = self._config.get("embedding_provider") or {}
        emb_provider = str(emb_cfg.get("provider") or "local_hash")
        emb_id = "embedding_model::default"
        graph.add_node(ConfigGraphNode(
            id=emb_id,
            node_type=NODE_EMBEDDING_MODEL,
            label=f"Embedding: {emb_provider}",
            source_file="user.json",
            source_kind="user_config",
            source_pointer="/embedding_provider",
            writable=True,
            runtime_active=True,
            declared_value=dict(emb_cfg),
            effective_value=dict(emb_cfg),
            data={
                "provider": emb_provider,
                "model": emb_cfg.get("model") or "",
                "external_calls_allowed": bool(emb_cfg.get("external_calls_allowed", False)),
            },
        ))
        graph.add_to_view(VIEW_CONTEXT_PIPELINE, emb_id)
        graph.add_to_view(VIEW_AGENT_RUNTIME, emb_id)
        self._add_restricted_inference(graph)
        self._add_codecompass_ranking(graph)

        # Backend model provider
        backend = str(self._config.get("chat_backend") or
                      self._config.get("backend") or "lmstudio")
        provider_id = f"model_provider::{backend}"
        if provider_id not in graph.nodes:
            graph.add_node(ConfigGraphNode(
                id=provider_id,
                node_type=NODE_MODEL_PROVIDER,
                label=f"Provider: {backend}",
                source_file="user.json",
                source_kind="user_config",
                source_pointer="/chat_backend",
                writable=True,
                runtime_active=True,
                data={"backend": backend},
            ))
        graph.add_to_view(VIEW_AGENT_RUNTIME, provider_id)

    def _add_restricted_inference(self, graph: ConfigGraph) -> None:
        raw = dict(self._config.get("restricted_inference") or {})
        root_id = "restricted_inference::root"
        graph.add_node(ConfigGraphNode(
            id=root_id,
            node_type=NODE_RESTRICTED_INFERENCE_ROOT,
            label="Restricted Inference",
            source_file="user.json",
            source_kind="user_config",
            source_pointer="/restricted_inference",
            writable=True,
            runtime_active=bool(raw.get("enabled", True)),
            declared_value=dict(raw),
            effective_value=dict(raw),
            data={
                "enabled": bool(raw.get("enabled", True)),
                "default_engine": str(raw.get("default_engine") or "mock"),
                "default_model_id": str(raw.get("default_model_id") or "mock-default"),
                "device": str(raw.get("device") or "cpu"),
                "allow_mock_fallback": bool(raw.get("allow_mock_fallback", True)),
                "allowed_engines": list(raw.get("allowed_engines") or [
                    "mock", "sentence-transformers", "huggingface-transformers", "onnxruntime", "pytorch",
                ]),
            },
        ))
        graph.add_to_view(VIEW_CONTEXT_PIPELINE, root_id)
        graph.add_to_view(VIEW_EFFECTIVE_CONFIG, root_id)

        context_id = "context_source::restricted_inference"
        if context_id in graph.nodes:
            graph.add_edge(ConfigGraphEdge(
                source=context_id,
                target=root_id,
                edge_type=EDGE_USES_RESTRICTED_INFERENCE,
            ))

        models = list(raw.get("models") or [])
        for index, model_raw in enumerate(models):
            if not isinstance(model_raw, dict):
                continue
            mid = str(model_raw.get("id") or model_raw.get("model") or f"model_{index}")
            node_id = f"restricted_inference_model::{mid}"
            graph.add_node(ConfigGraphNode(
                id=node_id,
                node_type=NODE_RESTRICTED_INFERENCE_MODEL,
                label=f"Restricted Model: {mid}",
                source_file="user.json",
                source_kind="user_config",
                source_pointer=f"/restricted_inference/models/{index}",
                writable=True,
                runtime_active=bool(model_raw.get("enabled", True)),
                declared_value=dict(model_raw),
                effective_value=dict(model_raw),
                data={
                    "id": mid,
                    "engine": str(model_raw.get("engine") or "mock"),
                    "model": str(model_raw.get("model") or ""),
                    "revision": str(model_raw.get("revision") or ""),
                    "local_path": str(model_raw.get("local_path") or ""),
                    "device": str(model_raw.get("device") or raw.get("device") or "cpu"),
                    "enabled": bool(model_raw.get("enabled", True)),
                    "tasks": list(model_raw.get("tasks") or []),
                },
            ))
            graph.add_edge(ConfigGraphEdge(source=root_id, target=node_id, edge_type=EDGE_USES_MODEL))
            graph.add_to_view(VIEW_CONTEXT_PIPELINE, node_id)
            graph.add_to_view(VIEW_EFFECTIVE_CONFIG, node_id)

        tasks = raw.get("tasks") if isinstance(raw.get("tasks"), dict) else {}
        for task_id, task_raw in sorted(tasks.items()):
            if not isinstance(task_raw, dict):
                continue
            node_id = f"restricted_inference_task::{task_id}"
            graph.add_node(ConfigGraphNode(
                id=node_id,
                node_type=NODE_RESTRICTED_INFERENCE_TASK,
                label=f"Restricted Task: {task_id}",
                source_file="user.json",
                source_kind="user_config",
                source_pointer=f"/restricted_inference/tasks/{task_id}",
                writable=True,
                runtime_active=bool(task_raw.get("enabled", True)),
                declared_value=dict(task_raw),
                effective_value=dict(task_raw),
                data={"id": task_id, **dict(task_raw)},
            ))
            graph.add_edge(ConfigGraphEdge(source=root_id, target=node_id, edge_type=EDGE_CONTAINS))
            graph.add_to_view(VIEW_CONTEXT_PIPELINE, node_id)
            graph.add_to_view(VIEW_EFFECTIVE_CONFIG, node_id)

    def _add_codecompass_ranking(self, graph: ConfigGraph) -> None:
        raw = dict(self._config.get("codecompass_ranking") or {})
        node_id = "codecompass_ranking::default"
        graph.add_node(ConfigGraphNode(
            id=node_id,
            node_type=NODE_CODECOMPASS_RANKING,
            label="CodeCompass Ranking",
            source_file="user.json",
            source_kind="user_config",
            source_pointer="/codecompass_ranking",
            writable=True,
            runtime_active=True,
            declared_value=dict(raw),
            effective_value=dict(raw),
            data={
                "restricted_inference_rerank_enabled": bool(raw.get("restricted_inference_rerank_enabled", False)),
                "score_weights": dict(raw.get("score_weights") or {}),
                "trace_scores": bool(raw.get("trace_scores", False)),
                "fallback_without_model": bool(raw.get("fallback_without_model", True)),
            },
        ))
        graph.add_to_view(VIEW_CONTEXT_PIPELINE, node_id)
        graph.add_to_view(VIEW_EFFECTIVE_CONFIG, node_id)
        if "context_source::codecompass" in graph.nodes:
            graph.add_edge(ConfigGraphEdge(
                source="context_source::codecompass",
                target=node_id,
                edge_type=EDGE_USES_CONTEXT_SOURCE,
            ))
        if "restricted_inference::root" in graph.nodes:
            graph.add_edge(ConfigGraphEdge(
                source=node_id,
                target="restricted_inference::root",
                edge_type=EDGE_USES_RESTRICTED_INFERENCE,
            ))

    # ── Planning templates ────────────────────────────────────────────────────

    def _add_planning_templates(self, graph: ConfigGraph) -> None:
        try:
            from agent.services.planning_template_catalog import get_planning_template_catalog
            catalog = get_planning_template_catalog()
            templates = catalog.list_templates() if hasattr(catalog, "list_templates") else []
        except Exception:
            templates = []

        # Fallback: read known task kinds from profile-map
        profile_map_path = self._root / "docs/agent-profiles/profile-map.json"
        known_kinds: list[str] = []
        if profile_map_path.exists():
            try:
                pm = json.loads(profile_map_path.read_text(encoding="utf-8"))
                for pid, pdata in (pm.get("profiles") or {}).items():
                    known_kinds.extend(pdata.get("allowed_task_kinds") or [pid])
            except Exception:
                pass

        processed: set[str] = set()

        for tmpl in templates:
            tid = str(tmpl.get("template_id") or tmpl.get("id") or "")
            if not tid or tid in processed:
                continue
            processed.add(tid)
            self._add_template_node(graph, tid, tmpl, source="catalog")

        for kind in known_kinds:
            if kind and kind not in processed:
                processed.add(kind)
                self._add_template_node(graph, kind, {}, source="profile_map")

    def _add_template_node(
        self, graph: ConfigGraph, tid: str, data: dict[str, Any], source: str
    ) -> None:
        tmpl_id = f"goal_template::{tid}"
        node = ConfigGraphNode(
            id=tmpl_id,
            node_type=NODE_GOAL_TEMPLATE,
            label=tid,
            runtime_source=source,
            runtime_active=True,
            stale=(source == "profile_map"),
            data={
                "template_id": tid,
                "description": str(data.get("description") or "")[:200],
            },
        )
        if source == "profile_map":
            node.diagnostics.append("derived from profile map — may be stale/hardcoded")
        graph.add_node(node)
        graph.add_to_view(VIEW_PLANNING_FLOW, tmpl_id)

        # task_kind node
        kind_id = f"task_kind::{tid}"
        if kind_id not in graph.nodes:
            graph.add_node(ConfigGraphNode(
                id=kind_id, node_type=NODE_TASK_KIND, label=tid,
                runtime_active=True, data={"task_kind": tid},
            ))
        graph.add_edge(ConfigGraphEdge(
            source=kind_id, target=tmpl_id, edge_type=EDGE_USES_TEMPLATE,
        ))
        graph.add_to_view(VIEW_PLANNING_FLOW, kind_id)

        # Link to agent profile
        profile_id = f"agent_profile::{tid}"
        if profile_id in graph.nodes:
            graph.add_edge(ConfigGraphEdge(
                source=tmpl_id, target=profile_id, edge_type=EDGE_ACTIVATES,
            ))

        # Subtask steps from template data
        for i, step in enumerate(data.get("steps") or data.get("subtasks") or []):
            step_label = str(step.get("title") or step.get("name") or f"step_{i+1}")
            step_id = f"subtask_step::{tid}::step_{i+1}"
            graph.add_node(ConfigGraphNode(
                id=step_id,
                node_type=NODE_SUBTASK_STEP,
                label=step_label,
                runtime_active=True,
                data={
                    "index": i + 1,
                    "role": str(step.get("role") or step.get("assigned_role") or ""),
                    "description": str(step.get("description") or "")[:200],
                },
            ))
            graph.add_edge(ConfigGraphEdge(
                source=tmpl_id, target=step_id, edge_type=EDGE_CREATES_SUBTASK,
                priority=i,
            ))
            if i > 0:
                prev_id = f"subtask_step::{tid}::step_{i}"
                graph.add_edge(ConfigGraphEdge(
                    source=step_id, target=prev_id, edge_type=EDGE_DEPENDS_ON,
                ))
            graph.add_to_view(VIEW_PLANNING_FLOW, step_id)

    # ── Views ─────────────────────────────────────────────────────────────────

    def _build_views(self, graph: ConfigGraph) -> None:
        # Make sure all views exist
        for view_id in (
            VIEW_CONFIGURATION_OVERVIEW,
            VIEW_PROFILE_ACTIVATION, VIEW_PLANNING_FLOW, VIEW_AGENT_RUNTIME,
            VIEW_POLICY_PATH, VIEW_CONTEXT_PIPELINE, VIEW_EFFECTIVE_CONFIG,
        ):
            graph.views.setdefault(view_id, [])

        # configuration_overview_view: complete graph surface for discovery.
        for nid in graph.nodes:
            graph.add_to_view(VIEW_CONFIGURATION_OVERVIEW, nid)

        # effective_config_view: union of key nodes
        for nid in list(graph.nodes):
            node = graph.nodes[nid]
            if node.runtime_active and node.node_type in (
                NODE_AGENT_PROFILE, NODE_INSTRUCTION_LAYER, NODE_TOOL_GROUP,
                NODE_PATH_RULE, NODE_CONTEXT_SOURCE, NODE_EMBEDDING_MODEL,
                NODE_MODEL_PROVIDER, NODE_RESTRICTED_INFERENCE_ROOT,
                NODE_RESTRICTED_INFERENCE_MODEL, NODE_RESTRICTED_INFERENCE_TASK,
                NODE_CODECOMPASS_RANKING,
            ):
                graph.add_to_view(VIEW_EFFECTIVE_CONFIG, nid)


def get_config_graph_builder_service(
    *,
    repo_root: str | Path | None = None,
    user_config: dict[str, Any] | None = None,
) -> ConfigGraphBuilderService:
    return ConfigGraphBuilderService(repo_root=repo_root, user_config=user_config)
