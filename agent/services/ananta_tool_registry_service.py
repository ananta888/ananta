"""AWTCL-005: Tool registry for the ananta-worker tool calling loop.

The registry is the single source of truth for which tools the
ananta-worker may request from the hub. Every tool carries a category
(read_only / controlled_execution / controlled_write / blocked), a risk
class (read / execution / write / admin / external_agent), an argument
schema, a result schema and explicit policy requirements. Tools that are
not registered here are rejected deterministically by the policy gate
(``agent/services/ananta_tool_policy_service.py``).

Contract: ``docs/contracts/ananta-worker-tool-loop.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATEGORY_READ_ONLY = "read_only"
CATEGORY_CONTROLLED_EXECUTION = "controlled_execution"
CATEGORY_CONTROLLED_WRITE = "controlled_write"
CATEGORY_BLOCKED = "blocked"

RISK_READ = "read"
RISK_EXECUTION = "execution"
RISK_WRITE = "write"
RISK_ADMIN = "admin"
RISK_EXTERNAL_AGENT = "external_agent"

# HDW-003: every tool declares where it executes. The hub process never
# runs workspace/shell logic itself (HDW-DD-001); it only dispatches.
PLANE_HUB_CONTROL_ONLY = "hub_control_only"
PLANE_WORKER_RUNTIME = "worker_runtime"
PLANE_SANDBOX_RUNTIME = "sandbox_runtime"
PLANE_EXTERNAL_BACKEND = "external_backend"
KNOWN_EXECUTION_PLANES = {
    PLANE_HUB_CONTROL_ONLY,
    PLANE_WORKER_RUNTIME,
    PLANE_SANDBOX_RUNTIME,
    PLANE_EXTERNAL_BACKEND,
}

_TOOL_RESULT_SCHEMA_REF = "ananta_tool_result.v1"


@dataclass(frozen=True)
class AnantaToolSpec:
    name: str
    category: str
    risk_class: str
    description: str
    argument_schema: dict[str, Any]
    result_schema: str = _TOOL_RESULT_SCHEMA_REF
    policy_requirements: dict[str, Any] = field(default_factory=dict)
    execution_plane: str = PLANE_WORKER_RUNTIME

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "risk_class": self.risk_class,
            "description": self.description,
            "argument_schema": dict(self.argument_schema),
            "result_schema": self.result_schema,
            "policy_requirements": dict(self.policy_requirements),
            "execution_plane": self.execution_plane,
        }


def _spec(
    name: str,
    category: str,
    risk_class: str,
    description: str,
    arguments: dict[str, Any],
    *,
    requires_approval: bool = False,
    requires_workspace: bool = True,
    allowed_mutation_modes: tuple[str, ...] | None = None,
    execution_plane: str | None = None,
) -> AnantaToolSpec:
    # HDW-003 default derivation: workspace tools run in the worker
    # runtime, workspace-free tools talk to an external backend/index.
    if execution_plane is None:
        execution_plane = PLANE_WORKER_RUNTIME if requires_workspace else PLANE_EXTERNAL_BACKEND
    return AnantaToolSpec(
        name=name,
        category=category,
        risk_class=risk_class,
        description=description,
        argument_schema={"type": "object", "properties": arguments},
        policy_requirements={
            "requires_approval": requires_approval,
            "requires_workspace": requires_workspace,
            "allowed_mutation_modes": list(allowed_mutation_modes or []),
        },
        execution_plane=execution_plane,
    )


_REGISTRY: dict[str, AnantaToolSpec] = {
    spec.name: spec
    for spec in [
        # --- read-only tools -------------------------------------------------
        _spec(
            "repo.list_files",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "List workspace files (sorted, limited).",
            {"path_glob": {"type": "string"}, "limit": {"type": "integer"}},
        ),
        _spec(
            "repo.read_file_range",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Read a line range from a workspace file.",
            {
                "path": {"type": "string"},
                "line_start": {"type": "integer"},
                "line_end": {"type": "integer"},
            },
        ),
        _spec(
            "repo.grep",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Deterministic regex search over workspace files.",
            {
                "pattern": {"type": "string"},
                "path_globs": {"type": "array"},
                "limit": {"type": "integer"},
                "context_before": {"type": "integer"},
                "context_after": {"type": "integer"},
            },
        ),
        _spec(
            "codecompass.plan_context",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Plan bounded CodeCompass LocationRefs and patch_targets for a query; returns location references and candidate patch ranges for subsequent resolve/patch steps.",
            {
                "query": {"type": "string"},
                "max_ranges": {"type": "integer"},
                "include_neighbors": {"type": "boolean"},
                "task_kind": {"type": "string"},
            },
            requires_workspace=False,
        ),
        _spec(
            "codecompass.resolve_context",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Resolve a policy-bounded CodeCompass ContextPackage containing ranked context chunks, file paths, and graph edges for the given query.",
            {
                "query": {"type": "string"},
                "task_kind": {"type": "string"},
                "mode": {"type": "string"},
                "working_files": {"type": "array"},
                "domain_hint": {"type": "string"},
                "domain_scope": {"type": "string"},
                "max_tokens": {"type": "integer"},
                "max_files": {"type": "integer"},
                "include_original_files": {"type": "boolean"},
                "include_jsonl_records": {"type": "boolean"},
                "include_graph": {"type": "boolean"},
                "llm_scope": {"type": "string"},
            },
            requires_workspace=False,
        ),
        _spec(
            "codecompass.search",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "CodeCompass hybrid retrieval search with score and source.",
            {"query": {"type": "string"}, "limit": {"type": "integer"}},
            requires_workspace=False,
        ),
        _spec(
            "codecompass.search_symbols",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Search CodeCompass symbol, file, domain and JSONL records.",
            {
                "query": {"type": "string"},
                "record_kinds": {"type": "array"},
                "path_globs": {"type": "array"},
                "domain_hint": {"type": "string"},
                "limit": {"type": "integer"},
            },
            requires_workspace=False,
        ),
        _spec(
            "codecompass.expand_graph",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Expand the CodeCompass graph around a node or seed list (bounded nodes/edges).",
            {
                "node": {"type": "string"},
                "seeds": {"type": "array"},
                "depth": {"type": "integer"},
                "max_depth": {"type": "integer"},
                "limit": {"type": "integer"},
                "max_nodes": {"type": "integer"},
            },
            requires_workspace=False,
        ),
        _spec(
            "codecompass.get_file_context",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Read authoritative original file excerpts for CodeCompass candidates; returns policy-checked, line-bounded file content for the requested paths.",
            {
                "paths": {"type": "array"},
                "line_ranges": {"type": "array"},
                "max_bytes_per_file": {"type": "integer"},
                "max_total_bytes": {"type": "integer"},
                "redaction_mode": {"type": "string"},
                "reason": {"type": "string"},
            },
            requires_workspace=True,
        ),
        _spec(
            "codecompass.get_domain_map",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Return a compact domain/subsystem map from CodeCompass.",
            {
                "domain_hint": {"type": "string"},
                "include_files": {"type": "boolean"},
                "include_edges": {"type": "boolean"},
                "max_entries": {"type": "integer"},
            },
            requires_workspace=False,
        ),
        _spec(
            "codecompass.architecture_query",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Run a CodeCompass architecture query (query engine contract).",
            {"question": {"type": "string"}},
            requires_workspace=False,
        ),
        _spec(
            "codecompass.semantic_equivalents",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Find deterministic Semantic Translation Graph nodes, equivalence rules and target constructs for a symbol, file or language mapping.",
            {
                "symbol": {"type": "string"},
                "file": {"type": "string"},
                "language": {"type": "string"},
                "target_languages": {"type": "array"},
                "semantic_kind": {"type": "string"},
            },
            requires_workspace=False,
        ),
        _spec(
            "codecompass.translation_plan",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Create a Semantic Translation plan without modifying code; classifies safe_auto_transform, needs_review and unsupported items.",
            {
                "source_path": {"type": "string"},
                "source_code": {"type": "string"},
                "target_language": {"type": "string"},
                "allowed_rule_ids": {"type": "array"},
            },
            requires_workspace=False,
        ),
        _spec(
            "codecompass.verify_translation",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Verify source, target and transform artifact against deterministic Semantic Translation rules.",
            {
                "source_path": {"type": "string"},
                "source_code": {"type": "string"},
                "target_code": {"type": "string"},
                "transform_artifact": {"type": "object"},
            },
            requires_workspace=False,
        ),
        _spec(
            "git.status",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Read-only git status of the workspace.",
            {},
        ),
        _spec(
            "git.diff_readonly",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Read-only git diff of the workspace (no mutation).",
            {"path": {"type": "string"}},
        ),
        _spec(
            "workspace.diff",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Diff of the workspace against the hub baseline incl. policy result.",
            {},
        ),
        # --- controlled execution tools --------------------------------------
        _spec(
            "test.discover",
            CATEGORY_CONTROLLED_EXECUTION,
            RISK_EXECUTION,
            "Discover test files in the workspace (no execution).",
            {"limit": {"type": "integer"}},
        ),
        _spec(
            "test.run",
            CATEGORY_CONTROLLED_EXECUTION,
            RISK_EXECUTION,
            "Run an allowlisted test command with timeout and output limits.",
            {"command": {"type": "string"}},
        ),
        _spec(
            "shell.run_allowlisted",
            CATEGORY_CONTROLLED_EXECUTION,
            RISK_EXECUTION,
            "Run an explicitly allowlisted shell command.",
            {"command": {"type": "string"}},
            requires_approval=True,
        ),
        _spec(
            "opencode.propose",
            CATEGORY_CONTROLLED_EXECUTION,
            RISK_EXTERNAL_AGENT,
            "Ask the OpenCode backend for a proposal (propose-only, no mutation).",
            {"prompt": {"type": "string"}},
            requires_workspace=False,
        ),
        _spec(
            "hermes.review",
            CATEGORY_CONTROLLED_EXECUTION,
            RISK_EXTERNAL_AGENT,
            "Ask the Hermes backend for a review (review capability only).",
            {"prompt": {"type": "string"}},
            requires_workspace=False,
        ),
        _spec(
            "aider.propose",
            CATEGORY_CONTROLLED_EXECUTION,
            RISK_EXTERNAL_AGENT,
            "Ask the Aider backend for a proposal (propose-only, no mutation).",
            {"prompt": {"type": "string"}},
            requires_workspace=False,
        ),
        _spec(
            "codex.propose",
            CATEGORY_CONTROLLED_EXECUTION,
            RISK_EXTERNAL_AGENT,
            "Ask the Codex backend for a proposal (propose-only, no mutation).",
            {"prompt": {"type": "string"}},
            requires_workspace=False,
        ),
        # --- controlled write tools -------------------------------------------
        _spec(
            "repo.write_file",
            CATEGORY_CONTROLLED_WRITE,
            RISK_WRITE,
            "Write a workspace file (create_only or replace_existing with hash check).",
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["create_only", "replace_existing"]},
                "expected_old_hash": {"type": "string"},
            },
            allowed_mutation_modes=("controlled_workspace", "strict_patch_request"),
        ),
        _spec(
            "repo.apply_patch",
            CATEGORY_CONTROLLED_WRITE,
            RISK_WRITE,
            "Apply a unified diff to a workspace file (hub-validated, atomic).",
            {
                "target_path": {"type": "string"},
                "unified_diff": {"type": "string"},
                "expected_old_hash": {"type": "string"},
                "variant": {"type": "string", "enum": ["unified_diff", "replace_range"]},
                "line_start": {"type": "integer"},
                "line_end": {"type": "integer"},
                "replacement": {"type": "string"},
                "reason": {"type": "string"},
            },
            allowed_mutation_modes=("strict_patch_request",),
        ),
        _spec(
            "todo.create_or_update",
            CATEGORY_CONTROLLED_WRITE,
            RISK_WRITE,
            "Create or update a todo file in the workspace.",
            {"path": {"type": "string"}, "content": {"type": "string"}},
            requires_approval=True,
            allowed_mutation_modes=("controlled_workspace", "strict_patch_request"),
        ),
        _spec(
            "git.add_selected",
            CATEGORY_CONTROLLED_WRITE,
            RISK_WRITE,
            "Stage selected workspace files (no commit).",
            {"paths": {"type": "array"}},
            requires_approval=True,
            allowed_mutation_modes=("controlled_workspace", "strict_patch_request"),
        ),
        # --- blocked without separate approval --------------------------------
        _spec("shell.run_unrestricted", CATEGORY_BLOCKED, RISK_ADMIN, "Unrestricted shell (blocked).", {}),
        _spec("network.fetch_arbitrary", CATEGORY_BLOCKED, RISK_ADMIN, "Arbitrary network fetch (blocked).", {}),
        _spec("service.restart", CATEGORY_BLOCKED, RISK_ADMIN, "Service restart (blocked).", {}),
        _spec("secret.read", CATEGORY_BLOCKED, RISK_ADMIN, "Secret read (blocked).", {}),
        _spec("git.push", CATEGORY_BLOCKED, RISK_ADMIN, "git push (blocked).", {}),
        _spec("git.commit", CATEGORY_BLOCKED, RISK_ADMIN, "git commit (blocked).", {}),
        _spec(
            "external_worker.execute_mutation",
            CATEGORY_BLOCKED,
            RISK_EXTERNAL_AGENT,
            "External worker mutation execution (blocked).",
            {},
        ),
    ]
}


class AnantaToolRegistryService:
    """Lists and resolves the tools the ananta-worker may request."""

    def list_tools(self, *, category: str | None = None) -> list[AnantaToolSpec]:
        specs = sorted(_REGISTRY.values(), key=lambda spec: spec.name)
        if category:
            specs = [spec for spec in specs if spec.category == category]
        return specs

    def get_tool(self, name: str | None) -> AnantaToolSpec | None:
        return _REGISTRY.get(str(name or "").strip())

    def is_known_tool(self, name: str | None) -> bool:
        return self.get_tool(name) is not None

    def describe_for_prompt(
        self,
        allowed_tools: list[str] | None = None,
        *,
        include_dynamic: bool = False,
    ) -> str:
        """Compact tool list for the worker prompt (name, args, purpose).

        HDE-013: with ``include_dynamic`` active custom tools from the
        dynamic registry are appended — name, purpose, arguments and
        risk only, never script internals. ``allowed_tools`` filters
        dynamic tools the same way it filters static ones.
        """
        allowed = {str(item or "").strip() for item in (allowed_tools or []) if str(item or "").strip()}
        lines: list[str] = []
        for spec in self.list_tools():
            if spec.category == CATEGORY_BLOCKED:
                continue
            if allowed and spec.name not in allowed:
                continue
            args = ", ".join(sorted((spec.argument_schema.get("properties") or {}).keys()))
            lines.append(f"- `{spec.name}` ({spec.risk_class}): {spec.description} Arguments: {args or 'none'}")
        if include_dynamic:
            for row in self._dynamic_tool_rows():
                name = str(row.get("name") or "")
                if not name or (allowed and name not in allowed):
                    continue
                args = ", ".join(sorted((row.get("argument_schema", {}).get("properties") or {}).keys()))
                description = str(row.get("description") or "")
                risk = str(row.get("risk_class") or "unknown")
                lines.append(f"- `{name}` ({risk}, custom): {description} Arguments: {args or 'none'}")
        return "\n".join(lines)

    def describe_for_openai_tools(
        self,
        allowed_tools: list[str] | None = None,
        *,
        include_dynamic: bool = False,
    ) -> list[dict[str, Any]]:
        """UTCR-001: Emit OpenAI-native tool schema for each non-BLOCKED spec.

        Each entry follows the ``{"type": "function", "function": {...}}``
        envelope required by the OpenAI chat-completions API.

        ``allowed_tools`` filters exactly like ``describe_for_prompt()``.
        CATEGORY_BLOCKED tools never appear. Dynamic tools are appended when
        ``include_dynamic=True``; static names always shadow dynamic ones.
        """
        allowed = {str(item or "").strip() for item in (allowed_tools or []) if str(item or "").strip()}
        seen: set[str] = set()
        result: list[dict[str, Any]] = []

        for spec in self.list_tools():
            if spec.category == CATEGORY_BLOCKED:
                continue
            if allowed and spec.name not in allowed:
                continue
            if spec.name in seen:
                continue
            seen.add(spec.name)
            props = dict((spec.argument_schema.get("properties") or {}))
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": {
                            "type": "object",
                            "properties": props,
                            "required": [],
                        },
                    },
                }
            )

        if include_dynamic:
            for row in self._dynamic_tool_rows():
                name = str(row.get("name") or "")
                if not name or name in seen:
                    continue
                if allowed and name not in allowed:
                    continue
                seen.add(name)
                props = dict((row.get("argument_schema", {}).get("properties") or {}))
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": str(row.get("description") or ""),
                            "parameters": {
                                "type": "object",
                                "properties": props,
                                "required": [],
                            },
                        },
                    }
                )

        return result

    def registry_snapshot(self, *, include_dynamic: bool = False) -> dict[str, Any]:
        tools = [dict(spec.as_dict(), source="static") for spec in self.list_tools()]
        if include_dynamic:
            tools.extend(self._dynamic_tool_rows())
        return {
            "schema": "ananta_worker_tool_registry.v1",
            "tools": tools,
        }

    def _dynamic_tool_rows(self) -> list[dict[str, Any]]:
        """Active custom tools as redacted snapshot rows (HDE-012/HDE-013).

        Static names always win: a dynamic tool shadowing a static name
        is skipped here (defense in depth — the dynamic registry already
        refuses such records).
        """
        try:
            from agent.services.dynamic_tool_registry_service import get_dynamic_tool_registry_service

            dynamic = get_dynamic_tool_registry_service()
            rows = []
            for record in dynamic.list_active_tools():
                spec = dict(record.get("spec") or {})
                name = str(spec.get("name") or "")
                if not name or name in _REGISTRY:
                    continue
                rows.append(
                    {
                        "name": name,
                        "category": spec.get("category"),
                        "risk_class": spec.get("risk_class"),
                        "description": spec.get("description"),
                        "argument_schema": dict(spec.get("argument_schema") or {}),
                        "result_schema": _TOOL_RESULT_SCHEMA_REF,
                        "execution_plane": spec.get("execution_plane"),
                        "source": "dynamic",
                        "version": record.get("version"),
                        "proposal_digest": record.get("proposal_digest"),
                    }
                )
            return rows
        except Exception:
            return []


ananta_tool_registry_service = AnantaToolRegistryService()


def get_ananta_tool_registry_service() -> AnantaToolRegistryService:
    return ananta_tool_registry_service
