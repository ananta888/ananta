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

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "risk_class": self.risk_class,
            "description": self.description,
            "argument_schema": dict(self.argument_schema),
            "result_schema": self.result_schema,
            "policy_requirements": dict(self.policy_requirements),
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
) -> AnantaToolSpec:
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
            },
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
            "codecompass.expand_graph",
            CATEGORY_READ_ONLY,
            RISK_READ,
            "Expand the CodeCompass graph around a node (bounded nodes/edges).",
            {"node": {"type": "string"}, "depth": {"type": "integer"}, "limit": {"type": "integer"}},
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

    def describe_for_prompt(self, allowed_tools: list[str] | None = None) -> str:
        """Compact tool list for the worker prompt (name, args, purpose)."""
        allowed = {str(item or "").strip() for item in (allowed_tools or []) if str(item or "").strip()}
        lines: list[str] = []
        for spec in self.list_tools():
            if spec.category == CATEGORY_BLOCKED:
                continue
            if allowed and spec.name not in allowed:
                continue
            args = ", ".join(sorted((spec.argument_schema.get("properties") or {}).keys()))
            lines.append(f"- `{spec.name}` ({spec.risk_class}): {spec.description} Arguments: {args or 'none'}")
        return "\n".join(lines)

    def registry_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "ananta_worker_tool_registry.v1",
            "tools": [spec.as_dict() for spec in self.list_tools()],
        }


ananta_tool_registry_service = AnantaToolRegistryService()


def get_ananta_tool_registry_service() -> AnantaToolRegistryService:
    return ananta_tool_registry_service
