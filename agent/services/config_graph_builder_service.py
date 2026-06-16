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
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GRAPH_SCHEMA = "ananta_configuration_graph.v1"

# ── Node types ────────────────────────────────────────────────────────────────
NODE_SURFACE = "surface"
NODE_GOAL_TEMPLATE = "goal_template"
NODE_TASK_KIND = "task_kind"
NODE_SUBTASK_STEP = "subtask_step"
NODE_AGENT_PROFILE = "agent_profile"
NODE_ROLE = "role"
NODE_AGENT_INSTANCE = "agent_instance"
NODE_WORKER_BACKEND = "worker_backend"
NODE_MODEL_PROVIDER = "model_provider"
NODE_MODEL_PROFILE = "model_profile"
NODE_EMBEDDING_MODEL = "embedding_model"
NODE_RESTRICTED_INFERENCE_MODEL = "restricted_inference_model"
NODE_TOOL = "tool"
NODE_TOOL_GROUP = "tool_group"
NODE_POLICY = "policy"
NODE_PATH_RULE = "path_rule"
NODE_CONTEXT_SOURCE = "context_source"
NODE_CODECOMPASS_PROFILE = "codecompass_profile"
NODE_RAG_PROFILE = "rag_profile"
NODE_ARTIFACT_RULE = "artifact_rule"
NODE_WRITE_RULE = "write_rule"
NODE_VERIFICATION_RULE = "verification_rule"
NODE_HANDOFF_RULE = "handoff_rule"
NODE_INSTRUCTION_LAYER = "instruction_layer"
NODE_RUNTIME_OVERRIDE = "runtime_override"
NODE_TRACE_EVENT = "trace_event"

ALL_NODE_TYPES = frozenset({
    NODE_SURFACE, NODE_GOAL_TEMPLATE, NODE_TASK_KIND, NODE_SUBTASK_STEP,
    NODE_AGENT_PROFILE, NODE_ROLE, NODE_AGENT_INSTANCE, NODE_WORKER_BACKEND,
    NODE_MODEL_PROVIDER, NODE_MODEL_PROFILE, NODE_EMBEDDING_MODEL,
    NODE_RESTRICTED_INFERENCE_MODEL, NODE_TOOL, NODE_TOOL_GROUP, NODE_POLICY,
    NODE_PATH_RULE, NODE_CONTEXT_SOURCE, NODE_CODECOMPASS_PROFILE, NODE_RAG_PROFILE,
    NODE_ARTIFACT_RULE, NODE_WRITE_RULE, NODE_VERIFICATION_RULE, NODE_HANDOFF_RULE,
    NODE_INSTRUCTION_LAYER, NODE_RUNTIME_OVERRIDE, NODE_TRACE_EVENT,
})

# ── Edge types ────────────────────────────────────────────────────────────────
EDGE_ACTIVATES = "activates"
EDGE_CONTAINS = "contains"
EDGE_INHERITS_FROM = "inherits_from"
EDGE_OVERRIDES = "overrides"
EDGE_USES_PROFILE = "uses_profile"
EDGE_USES_TEMPLATE = "uses_template"
EDGE_CREATES_SUBTASK = "creates_subtask"
EDGE_ASSIGNED_TO = "assigned_to"
EDGE_MAY_CALL_TOOL = "may_call_tool"
EDGE_BLOCKED_BY_POLICY = "blocked_by_policy"
EDGE_REQUIRES_APPROVAL = "requires_approval"
EDGE_USES_CONTEXT_SOURCE = "uses_context_source"
EDGE_USES_MODEL = "uses_model"
EDGE_USES_EMBEDDING_MODEL = "uses_embedding_model"
EDGE_USES_RESTRICTED_INFERENCE = "uses_restricted_inference"
EDGE_ROUTES_TO_BACKEND = "routes_to_backend"
EDGE_HANDS_OFF_TO = "hands_off_to"
EDGE_DEPENDS_ON = "depends_on"
EDGE_VERIFIES_WITH = "verifies_with"
EDGE_READS_PATH = "reads_path"
EDGE_WRITES_PATH = "writes_path"
EDGE_APPLIES_TO_PATH = "applies_to_path"
EDGE_EMITS_TRACE = "emits_trace"
EDGE_EFFECTIVE_AFTER_MERGE = "effective_after_merge"

ALL_EDGE_TYPES = frozenset({
    EDGE_ACTIVATES, EDGE_CONTAINS, EDGE_INHERITS_FROM, EDGE_OVERRIDES,
    EDGE_USES_PROFILE, EDGE_USES_TEMPLATE, EDGE_CREATES_SUBTASK, EDGE_ASSIGNED_TO,
    EDGE_MAY_CALL_TOOL, EDGE_BLOCKED_BY_POLICY, EDGE_REQUIRES_APPROVAL,
    EDGE_USES_CONTEXT_SOURCE, EDGE_USES_MODEL, EDGE_USES_EMBEDDING_MODEL,
    EDGE_USES_RESTRICTED_INFERENCE, EDGE_ROUTES_TO_BACKEND, EDGE_HANDS_OFF_TO,
    EDGE_DEPENDS_ON, EDGE_VERIFIES_WITH, EDGE_READS_PATH, EDGE_WRITES_PATH,
    EDGE_APPLIES_TO_PATH, EDGE_EMITS_TRACE, EDGE_EFFECTIVE_AFTER_MERGE,
})

# ── View IDs ──────────────────────────────────────────────────────────────────
VIEW_PROFILE_ACTIVATION = "profile_activation_view"
VIEW_PLANNING_FLOW = "planning_flow_view"
VIEW_AGENT_RUNTIME = "agent_runtime_view"
VIEW_POLICY_PATH = "policy_path_view"
VIEW_CONTEXT_PIPELINE = "context_pipeline_view"
VIEW_EFFECTIVE_CONFIG = "effective_config_view"

VIEW_IDS = {
    "profileActivation": VIEW_PROFILE_ACTIVATION,
    "planningFlow": VIEW_PLANNING_FLOW,
    "agentRuntime": VIEW_AGENT_RUNTIME,
    "policyPath": VIEW_POLICY_PATH,
    "contextPipeline": VIEW_CONTEXT_PIPELINE,
    "effectiveConfig": VIEW_EFFECTIVE_CONFIG,
}

# ── Path-character classification ─────────────────────────────────────────────
#
# A "path character" describes the operational nature of a profile or rule —
# independent of whether it uses LLM or deterministic processing.
#
PATH_CHARACTER_TEST        = "test"        # test design, TDD, coverage
PATH_CHARACTER_ANALYSIS    = "analysis"    # repo analysis, review, diagnostics
PATH_CHARACTER_OPS         = "ops"         # incident, admin repair
PATH_CHARACTER_MAINTENANCE = "maintenance" # bug fix, patch, refactor
PATH_CHARACTER_CREATIVE    = "creative"    # new project, feature, evolution
PATH_CHARACTER_EXPLAIN     = "explain"     # explanation / chat surfaces
PATH_CHARACTER_UNKNOWN     = "unknown"

_ROLE_CHARACTER_RULES: list[tuple[tuple[str, ...], str]] = [
    (("test", "tdd", "verify", "coverage"), PATH_CHARACTER_TEST),
    (("analys", "review", "diag", "audit", "evidenc"), PATH_CHARACTER_ANALYSIS),
    (("repair", "incident", "admin", "triage", "mitigation"), PATH_CHARACTER_OPS),
    (("fix", "patch", "refactor", "behavior_preserv"), PATH_CHARACTER_MAINTENANCE),
    (("explain", "chat", "explainer"), PATH_CHARACTER_EXPLAIN),  # before creative: "architecture_explainer"
    (("project", "feature", "evolution", "architect", "implement", "bounded"), PATH_CHARACTER_CREATIVE),
]

PATH_CHARACTER_LABELS: dict[str, str] = {
    PATH_CHARACTER_TEST:        "Testpfad",
    PATH_CHARACTER_ANALYSIS:    "Analysepfad",
    PATH_CHARACTER_OPS:         "Betriebspfad",
    PATH_CHARACTER_MAINTENANCE: "Wartungspfad",
    PATH_CHARACTER_CREATIVE:    "Entwicklungspfad",
    PATH_CHARACTER_EXPLAIN:     "Erklärpfad",
    PATH_CHARACTER_UNKNOWN:     "Allgemein",
}


def _classify_profile_character(role: str, profile_id: str) -> str:
    """Infer path character from role name and profile id."""
    text = (role + " " + profile_id).lower()
    for keywords, character in _ROLE_CHARACTER_RULES:
        if any(kw in text for kw in keywords):
            return character
    return PATH_CHARACTER_UNKNOWN


def _classify_rule_character(blocked: list[str], allowed: list[str]) -> str:
    """Classify a path rule by its AI-mode constraints."""
    if "full_llm" in blocked:
        return "kein_vollstaendiges_llm"
    if blocked:
        return "eingeschraenkt"
    if allowed:
        return "selektiv_erlaubt"
    return "offen"


# ── Behavioral dimensions ─────────────────────────────────────────────────────
#
# Structured annotations that explain WHAT an agent profile actually DOES at
# runtime — beyond the raw policy strings.  Shown in the config graph detail
# view so operators can understand the internal differences between profiles.
#

_EXECUTE_CONTRACT: dict[str, dict] = {
    "none": {
        "label": "Nur Lesen",
        "description": (
            "Keine Code-Änderungen möglich. Ausschließlich lesende Operationen. "
            "Ausgabe als strukturierter Befund oder Erklärung."
        ),
        "gate": "blocked",
        "can_write_files": False,
        "can_run_commands": False,
        "mechanism": "read_only",
    },
    "plan_only": {
        "label": "Vorschlag + Freigabe",
        "description": (
            "Darf Aktionen nur als Plan vorschlagen, nie direkt ausführen. "
            "Jeder Vorschlag muss Risiko-Level und Rollback-Plan enthalten. "
            "Ausführung erfordert explizite Freigabe durch den Operator."
        ),
        "gate": "explicit_approval_required",
        "can_write_files": False,
        "can_run_commands": False,
        "mechanism": "propose_only",
    },
    "via_hub_task_worker": {
        "label": "Hub-Task-Worker",
        "description": (
            "Änderungen werden über den Hub-Task-Worker ausgeführt. "
            "Der Hub validiert automatisch und serialisiert parallele Änderungen. "
            "Kein direkter Dateizugriff durch den Agenten selbst."
        ),
        "gate": "hub_validated",
        "can_write_files": True,
        "can_run_commands": True,
        "mechanism": "hub_worker",
    },
}

_CONTEXT_AUTHORITY: dict[str, dict] = {
    "diagnose": {
        "label": "Diagnose-Kontext",
        "description": (
            "Logs, Config-Dateien und Kommando-Output sind die primäre Wahrheitsquelle. "
            "CodeCompass wird nur für Projekt-Dateien verwendet, "
            "nicht als Host-Wahrheit für laufende Systeme."
        ),
        "primary_sources": ["logs", "config_files", "command_output"],
        "codecompass": "secondary",
    },
    "implement": {
        "label": "Implementierungs-Kontext",
        "description": (
            "Source-Code und Test-Output sind autoritativ. "
            "CodeCompass routet primär zu Kandidaten-Dateien für die Implementierung."
        ),
        "primary_sources": ["source_code", "test_output"],
        "codecompass": "primary",
    },
    "analyse": {
        "label": "Analyse-Kontext",
        "description": (
            "Alle Quellen werden lesend ausgewertet: Code, Logs, Git-History. "
            "Ausgabe als strukturierter, evidenzbasierter Befund."
        ),
        "primary_sources": ["source_code", "logs", "git_history", "config_files"],
        "codecompass": "primary",
    },
    "explain_navigate": {
        "label": "Erklär-/Navigations-Kontext",
        "description": (
            "Navigation und Erklärung im bestehenden Code. "
            "Keine Modifikationsabsicht — reiner Lesezugriff."
        ),
        "primary_sources": ["source_code", "codecompass"],
        "codecompass": "primary",
    },
    "plan_only": {
        "label": "Planungs-Kontext",
        "description": "Nur Planungsaktivitäten ohne Ausführung.",
        "primary_sources": ["source_code"],
        "codecompass": "secondary",
    },
}

_SCOPE_MUST_NOT: dict[str, list[str]] = {
    PATH_CHARACTER_TEST: [
        "Produktions-Logik ändern ohne explizite Freigabe",
        "Fehlschlagende Tests löschen statt reparieren",
        "Test-Fixtures ohne Begründung überschreiben",
    ],
    PATH_CHARACTER_ANALYSIS: [
        "Code-Änderungen vorschlagen oder ausführen",
        "Annahmen als Fakten ausgeben — nur evidenzbasierte Befunde",
        "Externe Quellen ohne Verifikation zitieren",
    ],
    PATH_CHARACTER_OPS: [
        "Destruktive Aktionen ohne explizite Freigabe ausführen",
        "Dry-run-Schritt überspringen",
        "Host-Logs/Config mit CodeCompass-Annahmen überschreiben",
        "Risiko-Level im Plan weglassen",
    ],
    PATH_CHARACTER_MAINTENANCE: [
        "Architektur-Umbau statt minimalem Patch",
        "Nicht-autorisierte Dateien ändern",
        "Verhalten ohne Test-Abdeckung ändern",
    ],
    PATH_CHARACTER_CREATIVE: [
        "Bestehende Verträge brechen ohne Migration",
        "Abhängigkeiten ohne Zustimmung hinzufügen",
    ],
    PATH_CHARACTER_EXPLAIN: [
        "Code-Änderungen vornehmen",
        "Interne Implementierungsdetails ohne Kontext preisgeben",
    ],
    PATH_CHARACTER_UNKNOWN: [],
}


def _build_behavior_dimensions(pdata: dict) -> dict:
    """Derive structured behavioral annotations for an agent profile node."""
    policy = str(pdata.get("code_change_policy") or "none")
    hint = str(pdata.get("context_policy_hint") or "implement")
    role = str(pdata.get("primary_role") or "")
    profile_id = str(pdata.get("profile_id") or "")

    character = _classify_profile_character(role, profile_id)

    execute = dict(_EXECUTE_CONTRACT.get(policy, _EXECUTE_CONTRACT["none"]))
    execute["policy"] = policy

    context = dict(_CONTEXT_AUTHORITY.get(hint, _CONTEXT_AUTHORITY["implement"]))
    context["hint"] = hint

    must_not = _SCOPE_MUST_NOT.get(character, [])

    return {
        "execute_contract": execute,
        "context_authority": context,
        "must_not": must_not,
        "scope": character,
        "scope_label": PATH_CHARACTER_LABELS.get(character, "Allgemein"),
    }


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ConfigGraphNode:
    id: str
    node_type: str
    label: str
    source_file: str | None = None
    source_line: int | None = None
    runtime_source: str | None = None
    runtime_active: bool = True
    stale: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_type": self.node_type,
            "label": self.label,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "runtime_source": self.runtime_source,
            "runtime_active": self.runtime_active,
            "stale": self.stale,
            "data": self.data,
            "diagnostics": self.diagnostics,
        }


@dataclass
class ConfigGraphEdge:
    source: str
    target: str
    edge_type: str
    priority: int = 0
    condition: str | None = None
    policy_effect: str | None = None
    source_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "priority": self.priority,
            "condition": self.condition,
            "policy_effect": self.policy_effect,
            "source_ref": self.source_ref,
        }


@dataclass
class ConfigGraph:
    schema: str = GRAPH_SCHEMA
    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    nodes: dict[str, ConfigGraphNode] = field(default_factory=dict)
    edges: list[ConfigGraphEdge] = field(default_factory=list)
    views: dict[str, list[str]] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)

    def add_node(self, node: ConfigGraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: ConfigGraphEdge) -> None:
        if edge.source in self.nodes and edge.target in self.nodes:
            self.edges.append(edge)

    def add_to_view(self, view_id: str, node_id: str) -> None:
        self.views.setdefault(view_id, [])
        if node_id not in self.views[view_id]:
            self.views[view_id].append(node_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "snapshot_id": self.snapshot_id,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "views": self.views,
            "diagnostics": self.diagnostics,
            "generated_at": self.generated_at,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
        }


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
                runtime_active=True,
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
                runtime_active=True,
                data={
                    "path_glob": glob,
                    "blocked_ai_modes": blocked,
                    "allowed_ai_modes": allowed,
                    "allow_free_text_generation": rule.get("allow_free_text_generation", True),
                    "allow_code_generation": rule.get("allow_code_generation", True),
                    "llm_scope": str(rule.get("llm_scope") or ""),
                    "max_input_chars": int(rule.get("max_input_chars") or 0),
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
            ("restricted_inference", "Restricted Transformer Inference", NODE_RESTRICTED_INFERENCE_MODEL,
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
            runtime_active=True,
            data={
                "provider": emb_provider,
                "model": emb_cfg.get("model") or "",
                "external_calls_allowed": bool(emb_cfg.get("external_calls_allowed", False)),
            },
        ))
        graph.add_to_view(VIEW_CONTEXT_PIPELINE, emb_id)
        graph.add_to_view(VIEW_AGENT_RUNTIME, emb_id)

        # Backend model provider
        backend = str(self._config.get("chat_backend") or
                      self._config.get("backend") or "lmstudio")
        provider_id = f"model_provider::{backend}"
        if provider_id not in graph.nodes:
            graph.add_node(ConfigGraphNode(
                id=provider_id,
                node_type=NODE_MODEL_PROVIDER,
                label=f"Provider: {backend}",
                runtime_active=True,
                data={"backend": backend},
            ))
        graph.add_to_view(VIEW_AGENT_RUNTIME, provider_id)

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
            VIEW_PROFILE_ACTIVATION, VIEW_PLANNING_FLOW, VIEW_AGENT_RUNTIME,
            VIEW_POLICY_PATH, VIEW_CONTEXT_PIPELINE, VIEW_EFFECTIVE_CONFIG,
        ):
            graph.views.setdefault(view_id, [])

        # effective_config_view: union of key nodes
        for nid in list(graph.nodes):
            node = graph.nodes[nid]
            if node.runtime_active and node.node_type in (
                NODE_AGENT_PROFILE, NODE_INSTRUCTION_LAYER, NODE_TOOL_GROUP,
                NODE_PATH_RULE, NODE_CONTEXT_SOURCE, NODE_EMBEDDING_MODEL,
                NODE_MODEL_PROVIDER,
            ):
                graph.add_to_view(VIEW_EFFECTIVE_CONFIG, nid)


def get_config_graph_builder_service(
    *,
    repo_root: str | Path | None = None,
    user_config: dict[str, Any] | None = None,
) -> ConfigGraphBuilderService:
    return ConfigGraphBuilderService(repo_root=repo_root, user_config=user_config)
