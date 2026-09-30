from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.services.worker_workspace_service import WorkerWorkspaceContext


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(content or ""), encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _truncate_text(value: str | None, *, limit: int | None) -> str:
    text = str(value or "")
    if not limit or limit <= 0:
        return text
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 14)].rstrip() + "\n\n[gekürzt]"


def _write_task_material(task: dict, bundle_dir: Path, record: Any) -> None:
    """LCTX-005/006: material moved out of an oversized task; the worker reads it section by section."""
    material = str(((task or {}).get("worker_execution_context") or {}).get("context_material") or "")
    if material.strip():
        material_path = bundle_dir / "task-material.md"
        _write_text(material_path, material.strip() + "\n")
        record(material_path, key="task_material_path")


class _ContextManifest:
    """Manifest of the files written into one worker workspace context bundle."""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.data: dict[str, object] = {"workspace_dir": str(workspace_dir), "files": []}

    def record(self, path: Path, *, key: str | None = None) -> str:
        rel = _safe_rel(path, self.workspace_dir)
        files = self.data.setdefault("files", [])
        if isinstance(files, list) and rel not in files:
            files.append(rel)
        if key:
            self.data[key] = rel
        return rel

    def write_text(self, path: Path, content: str, *, key: str) -> None:
        _write_text(path, content)
        self.record(path, key=key)

    def write_json(self, path: Path, payload: object, *, key: str) -> None:
        _write_json(path, payload)
        self.record(path, key=key)


_RUNTIME_CONSTRAINT_LINES = (
    "## Execution environment constraints",
    "- Do NOT use `sudo` — the execution environment is a Docker container without root privileges.",
    "- Do NOT use `su`, `sudo -i`, or any privilege escalation command.",
    "- Do NOT use `systemctl` — there is no systemd in this Docker container.",
    "- Do NOT use `service` — init.d service management is unavailable in this container.",
    "- Do NOT use `ss` — not installed. Use `netstat -tlnp` or `cat /proc/net/tcp` for port info.",
    "- To check if a process is running use `pgrep -x <name>` or `ps aux`.",
    "- To check open ports use `netstat -tlnp` or `cat /proc/net/tcp`.",
    "- Shell commands must work as a non-root user inside a container.",
    "- If the target software (nginx, apache, mysql, etc.) is not installed: write commands as a shell script file in the artifacts directory instead.",
    "",
    "## Workspace guidance",
    "- Read `.ananta/context-index.md` first for task-specific context files.",
    "- Read `.ananta/agent-profile.json` for the active agent profile metadata.",
    "- Use `rag_helper/` for retrieved research and knowledge files when present.",
)

_CODECOMPASS_RUNTIME_RULES = (
    "",
    "## CodeCompass runtime rules",
    "- CodeCompass context (snippets, file excerpts, graph nodes/edges, evidence paths) is **indexed repository hints**, not truth.",
    "- Do **not** fabricate or guess missing data. Name the missing context and request a reload via the Hub (see `docs/contracts/codecompass-context-reload-request.md`).",
    "- Do **not** claim coverage, policy effect, or dependency without an evidence path. A name match in the graph is not coverage; a frontend guard reference is not backend enforcement.",
    "- Surface warnings, do not filter them. Heuristic edges come with a warning; the warning is part of the answer.",
)

_CONTEXT_INDEX_KEYS = (
    "agents_path",
    "agent_profile_path",
    "task_brief_path",
    "system_prompt_path",
    "hub_context_path",
    "task_material_path",
    "research_context_prompt_path",
    "research_context_json_path",
    "tool_definitions_path",
    "output_schema_path",
    "pattern_selection_contract_path",
    "pattern_allowed_path",
    "notation_selection_contract_path",
    "notation_allowed_path",
)


def prepare_opencode_context_files(
    *,
    task: dict,
    workspace_context: WorkerWorkspaceContext,
    base_prompt: str,
    system_prompt: str | None,
    context_text: str | None,
    expected_output_schema: dict | None,
    tool_definitions: list[dict] | None,
    research_context: dict | None,
    include_response_contract: bool = True,
    allow_complex_shell: bool = False,
    task_brief_char_limit: int | None = None,
    context_text_char_limit: int | None = None,
    research_prompt_char_limit: int | None = None,
    pattern_hints: dict | None = None,
    notation_hints: dict | None = None,
) -> dict:
    workspace_dir = workspace_context.workspace_dir
    bundle_dir = workspace_dir / ".ananta"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest = _ContextManifest(workspace_dir)

    active_profile = _write_agents_and_profile(
        manifest, task=task, bundle_dir=bundle_dir, include_response_contract=include_response_contract
    )
    manifest.write_text(
        bundle_dir / "task-brief.md",
        _task_brief_content(
            task,
            base_prompt=base_prompt,
            active_profile=active_profile,
            include_response_contract=include_response_contract,
            char_limit=task_brief_char_limit,
        ),
        key="task_brief_path",
    )
    _write_response_contract(
        manifest,
        bundle_dir / "response-contract.md",
        include_response_contract=include_response_contract,
        allow_complex_shell=allow_complex_shell,
    )
    _write_prompt_inputs(
        manifest,
        task=task,
        bundle_dir=bundle_dir,
        system_prompt=system_prompt,
        context_text=context_text,
        context_text_char_limit=context_text_char_limit,
        expected_output_schema=expected_output_schema,
        tool_definitions=tool_definitions,
    )
    if research_context:
        _write_research_context(
            manifest, workspace_context.rag_helper_dir, research_context, char_limit=research_prompt_char_limit
        )
    if isinstance(pattern_hints, dict) and pattern_hints:
        _write_pattern_hints(manifest, bundle_dir / "patterns", pattern_hints)
    if isinstance(notation_hints, dict) and notation_hints:
        _write_notation_hints(manifest, bundle_dir / "notation", notation_hints)
    _write_context_index(manifest, bundle_dir / "context-index.md", include_response_contract=include_response_contract)
    return manifest.data


def _runtime_constraints(task: dict, *, include_response_contract: bool) -> str:
    lines = list(_RUNTIME_CONSTRAINT_LINES)
    if include_response_contract:
        lines.append("- Follow `.ananta/response-contract.md` for the required response format.")
    else:
        lines.append("- Apply the requested changes directly in the workspace; results are collected from workspace diffs.")
    agent_template_name = str((task or {}).get("agent_template") or "").strip().lower()
    if agent_template_name in {"opencode", "ananta_worker"}:
        lines.extend(_CODECOMPASS_RUNTIME_RULES)
    return "\n".join(lines)


def _write_agents_and_profile(
    manifest: _ContextManifest, *, task: dict, bundle_dir: Path, include_response_contract: bool
) -> Any:
    from agent.services.agent_profile_service import get_agent_profile_service

    profile_service = get_agent_profile_service()
    active_profile = profile_service.resolve_for_task(task)
    composed_agents = profile_service.compose_content(
        active_profile,
        runtime_constraints=_runtime_constraints(task, include_response_contract=include_response_contract),
    )
    manifest.write_text(manifest.workspace_dir / "AGENTS.md", composed_agents, key="agents_path")
    manifest.write_json(bundle_dir / "agent-profile.json", active_profile.to_metadata(), key="agent_profile_path")
    manifest.data["active_agent_profile"] = active_profile.to_metadata()
    return active_profile


def _task_brief_content(
    task: dict, *, base_prompt: str, active_profile: Any, include_response_contract: bool, char_limit: int | None
) -> str:
    profile_line = f"- Active agent profile: {active_profile.profile_id}" + (
        " (root-only fallback)" if active_profile.is_fallback else ""
    )
    brief_assignment = _truncate_text(str(base_prompt or "").strip(), limit=char_limit).strip()
    execution_mode = "structured-json-proposal" if include_response_contract else "interactive-workspace-execution"
    task_lines = [
        "# Task Brief",
        "",
        f"- Task ID: {str(task.get('id') or '').strip() or 'unknown'}",
        f"- Title: {str(task.get('title') or '').strip() or 'unknown'}",
        f"- Execution mode: {execution_mode}",
        profile_line,
        "",
        "## Current assignment (source of truth)",
        brief_assignment or "No task prompt available.",
    ]
    description = str(task.get("description") or "").strip()
    if description and description != str(base_prompt or "").strip():
        task_lines.extend(
            [
                "",
                "## Task metadata description (secondary context)",
                _truncate_text(description, limit=char_limit).strip(),
            ]
        )
    task_lines.extend(
        [
            "",
            "## Working directives",
            "- Prioritize the current assignment above metadata if they differ.",
            "- Apply changes directly in this workspace and keep edits auditable.",
        ]
    )
    if include_response_contract:
        task_lines.append("- Return exactly one JSON object according to `.ananta/response-contract.md`.")
    else:
        task_lines.append("- No JSON response is required; workspace diffs are collected automatically after the run.")
    return "\n".join(task_lines).strip() + "\n"


def _write_response_contract(
    manifest: _ContextManifest, response_contract: Path, *, include_response_contract: bool, allow_complex_shell: bool
) -> None:
    if not include_response_contract:
        if response_contract.exists():
            response_contract.unlink(missing_ok=True)
        return
    if allow_complex_shell:
        shell_rule = (
            "- `command` may use pipelines (`|`), redirects (`>`, `<`, `2>&1`), "
            "and chaining (`&&`, `||`, `;`) — full shell syntax is allowed."
        )
    else:
        shell_rule = "- `command` must not use shell chaining or redirection (`&&`, `||`, `;`, `>`, `<`, `|`)."
    response_lines = [
        "# Response Contract",
        "",
        "Return exactly one JSON object and no Markdown.",
        "",
        "Required rules:",
        "- The first character must be '{' and the last character must be '}'.",
        "- Set at least one of `command` or `tool_calls`.",
        "- `reason` must stay short and technical.",
        "- Prefer `tool_calls` for file, directory, and code-change operations.",
        "- If `command` is used, it must be exactly one concrete shell command.",
        shell_rule,
        "",
        "Expected shape:",
        "```json",
        '{',
        '  "reason": "Short technical reason",',
        '  "command": "optional shell command",',
        '  "tool_calls": [ { "name": "tool_name", "args": { "arg1": "value" } } ]',
        '}',
        "```",
    ]
    manifest.write_text(response_contract, "\n".join(response_lines) + "\n", key="response_contract_path")


def _write_prompt_inputs(
    manifest: _ContextManifest,
    *,
    task: dict,
    bundle_dir: Path,
    system_prompt: str | None,
    context_text: str | None,
    context_text_char_limit: int | None,
    expected_output_schema: dict | None,
    tool_definitions: list[dict] | None,
) -> None:
    if system_prompt:
        manifest.write_text(
            bundle_dir / "system-prompt.md", str(system_prompt).strip() + "\n", key="system_prompt_path"
        )
    if context_text:
        manifest.write_text(
            bundle_dir / "hub-context.md",
            _truncate_text(str(context_text).strip(), limit=context_text_char_limit).strip() + "\n",
            key="hub_context_path",
        )
    _write_task_material(task, bundle_dir, manifest.record)
    if expected_output_schema:
        manifest.write_json(bundle_dir / "output-schema.json", expected_output_schema, key="output_schema_path")
    if tool_definitions:
        manifest.write_json(bundle_dir / "tool-definitions.json", tool_definitions, key="tool_definitions_path")


def _write_research_context(
    manifest: _ContextManifest, rag_helper_dir: Path, research_context: dict, *, char_limit: int | None
) -> None:
    manifest.write_json(
        rag_helper_dir / "research-context.json", research_context, key="research_context_json_path"
    )
    prompt_section = str((research_context or {}).get("prompt_section") or "").strip()
    if prompt_section:
        manifest.write_text(
            rag_helper_dir / "research-context.md",
            _truncate_text(prompt_section, limit=char_limit).strip() + "\n",
            key="research_context_prompt_path",
        )


def _append_id_section(lines: list[str], heading: str, ids: list, *, suffix: str = "") -> None:
    if not ids:
        return
    lines.append(heading)
    for item_id in ids:
        lines.append(f"- `{item_id}`{suffix}")
    lines.append("")


def _write_pattern_hints(manifest: _ContextManifest, pattern_dir: Path, pattern_hints: dict) -> None:
    pattern_dir.mkdir(parents=True, exist_ok=True)
    manifest.write_json(
        pattern_dir / "pattern-selection-contract.json",
        {
            "schema": "pattern_selection_contract.v1",
            "allowed_patterns": list(pattern_hints.get("allowed_patterns") or []),
            "preferred_patterns": list(pattern_hints.get("preferred_patterns") or []),
            "forbid_patterns": list(pattern_hints.get("forbid_patterns") or []),
            "language_targets": list(pattern_hints.get("language_targets") or []),
            "require_tests": bool(pattern_hints.get("require_tests", True)),
        },
        key="pattern_selection_contract_path",
    )
    allowed_md_lines = [
        "# Allowed Design Patterns",
        "",
        "Use ONLY the pattern IDs listed here when proposing a pattern_plan.",
        "Patterns not in this list will be rejected by the hub validator.",
        "",
    ]
    _append_id_section(allowed_md_lines, "## Allowed", list(pattern_hints.get("allowed_patterns") or []))
    _append_id_section(
        allowed_md_lines, "## Preferred (subset of allowed)", list(pattern_hints.get("preferred_patterns") or [])
    )
    _append_id_section(
        allowed_md_lines,
        "## Forbidden",
        list(pattern_hints.get("forbid_patterns") or []),
        suffix=" — must NOT be used",
    )
    if bool(pattern_hints.get("require_tests", True)):
        allowed_md_lines.append("**Tests are required for any pattern output.**")
    else:
        allowed_md_lines.append("*Tests are optional for this step.*")
    manifest.write_text(
        pattern_dir / "allowed-patterns.md", "\n".join(allowed_md_lines).strip() + "\n", key="pattern_allowed_path"
    )
    manifest.data["pattern_context_paths"] = [
        str(manifest.data.get("pattern_selection_contract_path") or ""),
        str(manifest.data.get("pattern_allowed_path") or ""),
    ]


def _write_notation_hints(manifest: _ContextManifest, notation_dir: Path, notation_hints: dict) -> None:
    notation_dir.mkdir(parents=True, exist_ok=True)
    manifest.write_json(
        notation_dir / "notation-selection-contract.json",
        {
            "schema": "notation_selection_contract.v1",
            "allowed_notations": list(notation_hints.get("allowed_notations") or []),
            "preferred_notations": list(notation_hints.get("preferred_notations") or []),
            "forbid_notations": list(notation_hints.get("forbid_notations") or []),
            "default_notation": str(notation_hints.get("default_notation") or ""),
            "task_kind": str(notation_hints.get("task_kind") or "diagram"),
        },
        key="notation_selection_contract_path",
    )
    allowed_md_lines = [
        "# Allowed Diagram Notations",
        "",
        "Use ONLY the notation pattern IDs listed here when proposing a notation pattern_plan.",
        "Patterns not in this list will be rejected by the hub validator.",
        "",
    ]
    _append_id_section(allowed_md_lines, "## Allowed", list(notation_hints.get("allowed_notations") or []))
    _append_id_section(
        allowed_md_lines, "## Preferred (subset of allowed)", list(notation_hints.get("preferred_notations") or [])
    )
    _append_id_section(
        allowed_md_lines,
        "## Forbidden",
        list(notation_hints.get("forbid_notations") or []),
        suffix=" — must NOT be used",
    )
    default = notation_hints.get("default_notation")
    if isinstance(default, str) and default:
        allowed_md_lines.append(f"**Default notation:** `{default}`")
        allowed_md_lines.append("")
    manifest.write_text(
        notation_dir / "allowed-notations.md", "\n".join(allowed_md_lines).strip() + "\n", key="notation_allowed_path"
    )
    manifest.data["notation_context_paths"] = [
        str(manifest.data.get("notation_selection_contract_path") or ""),
        str(manifest.data.get("notation_allowed_path") or ""),
    ]


def _write_context_index(manifest: _ContextManifest, context_index: Path, *, include_response_contract: bool) -> None:
    index_lines = [
        "# OpenCode Workspace Context",
        "",
        "Read these files before planning or executing changes:",
    ]
    preferred_keys = list(_CONTEXT_INDEX_KEYS)
    if include_response_contract:
        preferred_keys.append("response_contract_path")
    for key in preferred_keys:
        rel = str(manifest.data.get(key) or "").strip()
        if rel:
            index_lines.append(f"- `{rel}`")
    manifest.write_text(context_index, "\n".join(index_lines).strip() + "\n", key="context_index_path")


def prepare_ananta_worker_context_files(
    *,
    task: dict,
    workspace_context: WorkerWorkspaceContext,
    base_prompt: str,
    system_prompt: str | None = None,
    context_text: str | None = None,
    research_context: dict | None = None,
    mutation_mode: str = "read_only",
    notation_hints: dict | None = None,
) -> dict:
    manifest = prepare_opencode_context_files(
        task=task,
        workspace_context=workspace_context,
        base_prompt=base_prompt,
        system_prompt=system_prompt,
        context_text=context_text,
        expected_output_schema=None,
        tool_definitions=None,
        research_context=research_context,
        include_response_contract=False,
        notation_hints=notation_hints,
    )
    mode = str(mutation_mode or "read_only").strip().lower()
    contract_lines = [
        "# Ananta-Worker Response Contract",
        "",
        f"- mutation_mode: `{mode}`",
        "- Antworte mit genau einem JSON-Objekt nach `ananta_worker_tool_loop.v1`",
        "  (siehe docs/contracts/ananta-worker-tool-loop.md).",
        "",
    ]
    if mode == "read_only":
        contract_lines += [
            "## read_only",
            "- Du darfst KEINE Dateien ändern. Nur Analyse, Tool-Requests (read-only) und final_answer.",
        ]
    elif mode == "controlled_workspace":
        contract_lines += [
            "## controlled_workspace",
            "- Du darfst innerhalb der erlaubten (materialisierten) Dateien direkt arbeiten:",
            '  nutze `{"kind": "workspace_write", "files": [{"path": "...", "content": "..."}]}`.',
            "- Der Hub prüft danach Diff, Pfade, Größe und Policy gegen die Baseline.",
            "- Änderungen außerhalb des Manifests werden blockiert.",
        ]
    elif mode == "strict_patch_request":
        contract_lines += [
            "## strict_patch_request",
            "- Du darfst KEINE Dateien direkt ändern.",
            "- Liefere einzelne PatchRequests als tool_request `repo.apply_patch` oder `repo.write_file`;",
            "  der Hub validiert und wendet jeden Patch einzeln an.",
        ]
    response_contract = workspace_context.workspace_dir / ".ananta" / "response-contract.md"
    _write_text(response_contract, "\n".join(contract_lines).strip() + "\n")
    rel = _safe_rel(response_contract, workspace_context.workspace_dir)
    files = manifest.setdefault("files", [])
    if isinstance(files, list) and rel not in files:
        files.append(rel)
    manifest["response_contract_path"] = rel
    manifest["mutation_mode"] = mode
    return manifest
