from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

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

    manifest: dict[str, object] = {"workspace_dir": str(workspace_dir), "files": []}

    def _record(path: Path, *, key: str | None = None) -> str:
        rel = _safe_rel(path, workspace_dir)
        files = manifest.setdefault("files", [])
        if isinstance(files, list) and rel not in files:
            files.append(rel)
        if key:
            manifest[key] = rel
        return rel

    from agent.services.agent_profile_service import get_agent_profile_service
    _profile_svc = get_agent_profile_service()
    _active_profile = _profile_svc.resolve_for_task(task)

    _runtime_constraints_lines = [
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
    ]
    if include_response_contract:
        _runtime_constraints_lines.append("- Follow `.ananta/response-contract.md` for the required response format.")
    else:
        _runtime_constraints_lines.append("- Apply the requested changes directly in the workspace; results are collected from workspace diffs.")

    _agent_template_name = str((task or {}).get("agent_template") or "").strip().lower()
    if _agent_template_name in {"opencode", "ananta_worker"}:
        _runtime_constraints_lines.extend(
            [
                "",
                "## CodeCompass runtime rules",
                "- CodeCompass context (snippets, file excerpts, graph nodes/edges, evidence paths) is **indexed repository hints**, not truth.",
                "- Do **not** fabricate or guess missing data. Name the missing context and request a reload via the Hub (see `docs/contracts/codecompass-context-reload-request.md`).",
                "- Do **not** claim coverage, policy effect, or dependency without an evidence path. A name match in the graph is not coverage; a frontend guard reference is not backend enforcement.",
                "- Surface warnings, do not filter them. Heuristic edges come with a warning; the warning is part of the answer.",
            ]
        )

    _composed_agents = _profile_svc.compose_content(
        _active_profile,
        runtime_constraints="\n".join(_runtime_constraints_lines),
    )

    agents_dst = workspace_dir / "AGENTS.md"
    _write_text(agents_dst, _composed_agents)
    _record(agents_dst, key="agents_path")

    _profile_meta_path = bundle_dir / "agent-profile.json"
    _write_json(_profile_meta_path, _active_profile.to_metadata())
    _record(_profile_meta_path, key="agent_profile_path")
    manifest["active_agent_profile"] = _active_profile.to_metadata()

    task_brief = bundle_dir / "task-brief.md"
    _profile_line = (
        f"- Active agent profile: {_active_profile.profile_id}"
        + (" (root-only fallback)" if _active_profile.is_fallback else "")
    )
    brief_assignment = _truncate_text(str(base_prompt or "").strip(), limit=task_brief_char_limit).strip()
    task_lines = [
        "# Task Brief",
        "",
        f"- Task ID: {str(task.get('id') or '').strip() or 'unknown'}",
        f"- Title: {str(task.get('title') or '').strip() or 'unknown'}",
        f"- Execution mode: {'structured-json-proposal' if include_response_contract else 'interactive-workspace-execution'}",
        _profile_line,
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
                _truncate_text(description, limit=task_brief_char_limit).strip(),
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
    _write_text(task_brief, "\n".join(task_lines).strip() + "\n")
    _record(task_brief, key="task_brief_path")

    response_contract = bundle_dir / "response-contract.md"
    if include_response_contract:
        if allow_complex_shell:
            shell_rule = (
                "- `command` may use pipelines (`|`), redirects (`>`, `<`, `2>&1`), "
                "and chaining (`&&`, `||`, `;`) — full shell syntax is allowed."
            )
        else:
            shell_rule = (
                "- `command` must not use shell chaining or redirection (`&&`, `||`, `;`, `>`, `<`, `|`)."
            )
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
        _write_text(response_contract, "\n".join(response_lines) + "\n")
        _record(response_contract, key="response_contract_path")
    elif response_contract.exists():
        response_contract.unlink(missing_ok=True)

    if system_prompt:
        system_prompt_path = bundle_dir / "system-prompt.md"
        _write_text(system_prompt_path, str(system_prompt).strip() + "\n")
        _record(system_prompt_path, key="system_prompt_path")

    if context_text:
        hub_context_path = bundle_dir / "hub-context.md"
        _write_text(
            hub_context_path,
            _truncate_text(str(context_text).strip(), limit=context_text_char_limit).strip() + "\n",
        )
        _record(hub_context_path, key="hub_context_path")

    if expected_output_schema:
        schema_path = bundle_dir / "output-schema.json"
        _write_json(schema_path, expected_output_schema)
        _record(schema_path, key="output_schema_path")

    if tool_definitions:
        tool_defs_path = bundle_dir / "tool-definitions.json"
        _write_json(tool_defs_path, tool_definitions)
        _record(tool_defs_path, key="tool_definitions_path")

    if research_context:
        research_json_path = workspace_context.rag_helper_dir / "research-context.json"
        _write_json(research_json_path, research_context)
        _record(research_json_path, key="research_context_json_path")
        prompt_section = str((research_context or {}).get("prompt_section") or "").strip()
        if prompt_section:
            research_md_path = workspace_context.rag_helper_dir / "research-context.md"
            _write_text(
                research_md_path,
                _truncate_text(prompt_section, limit=research_prompt_char_limit).strip() + "\n",
            )
            _record(research_md_path, key="research_context_prompt_path")

    if isinstance(pattern_hints, dict) and pattern_hints:
        pattern_dir = bundle_dir / "patterns"
        pattern_dir.mkdir(parents=True, exist_ok=True)
        contract_path = pattern_dir / "pattern-selection-contract.json"
        _write_json(contract_path, {
            "schema": "pattern_selection_contract.v1",
            "allowed_patterns": list(pattern_hints.get("allowed_patterns") or []),
            "preferred_patterns": list(pattern_hints.get("preferred_patterns") or []),
            "forbid_patterns": list(pattern_hints.get("forbid_patterns") or []),
            "language_targets": list(pattern_hints.get("language_targets") or []),
            "require_tests": bool(pattern_hints.get("require_tests", True)),
        })
        _record(contract_path, key="pattern_selection_contract_path")
        allowed_md_lines = [
            "# Allowed Design Patterns",
            "",
            "Use ONLY the pattern IDs listed here when proposing a pattern_plan.",
            "Patterns not in this list will be rejected by the hub validator.",
            "",
        ]
        allowed = list(pattern_hints.get("allowed_patterns") or [])
        if allowed:
            allowed_md_lines.append("## Allowed")
            for pid in allowed:
                allowed_md_lines.append(f"- `{pid}`")
            allowed_md_lines.append("")
        preferred = list(pattern_hints.get("preferred_patterns") or [])
        if preferred:
            allowed_md_lines.append("## Preferred (subset of allowed)")
            for pid in preferred:
                allowed_md_lines.append(f"- `{pid}`")
            allowed_md_lines.append("")
        forbidden = list(pattern_hints.get("forbid_patterns") or [])
        if forbidden:
            allowed_md_lines.append("## Forbidden")
            for pid in forbidden:
                allowed_md_lines.append(f"- `{pid}` — must NOT be used")
            allowed_md_lines.append("")
        if bool(pattern_hints.get("require_tests", True)):
            allowed_md_lines.append("**Tests are required for any pattern output.**")
        else:
            allowed_md_lines.append("*Tests are optional for this step.*")
        allowed_path = pattern_dir / "allowed-patterns.md"
        _write_text(allowed_path, "\n".join(allowed_md_lines).strip() + "\n")
        _record(allowed_path, key="pattern_allowed_path")
        manifest["pattern_context_paths"] = [
            str(manifest.get("pattern_selection_contract_path") or ""),
            str(manifest.get("pattern_allowed_path") or ""),
        ]

    if isinstance(notation_hints, dict) and notation_hints:
        notation_dir = bundle_dir / "notation"
        notation_dir.mkdir(parents=True, exist_ok=True)
        contract_path = notation_dir / "notation-selection-contract.json"
        _write_json(contract_path, {
            "schema": "notation_selection_contract.v1",
            "allowed_notations": list(notation_hints.get("allowed_notations") or []),
            "preferred_notations": list(notation_hints.get("preferred_notations") or []),
            "forbid_notations": list(notation_hints.get("forbid_notations") or []),
            "default_notation": str(notation_hints.get("default_notation") or ""),
            "task_kind": str(notation_hints.get("task_kind") or "diagram"),
        })
        _record(contract_path, key="notation_selection_contract_path")
        allowed_md_lines = [
            "# Allowed Diagram Notations",
            "",
            "Use ONLY the notation pattern IDs listed here when proposing a notation pattern_plan.",
            "Patterns not in this list will be rejected by the hub validator.",
            "",
        ]
        allowed = list(notation_hints.get("allowed_notations") or [])
        if allowed:
            allowed_md_lines.append("## Allowed")
            for nid in allowed:
                allowed_md_lines.append(f"- `{nid}`")
            allowed_md_lines.append("")
        preferred = list(notation_hints.get("preferred_notations") or [])
        if preferred:
            allowed_md_lines.append("## Preferred (subset of allowed)")
            for nid in preferred:
                allowed_md_lines.append(f"- `{nid}`")
            allowed_md_lines.append("")
        forbidden = list(notation_hints.get("forbid_notations") or [])
        if forbidden:
            allowed_md_lines.append("## Forbidden")
            for nid in forbidden:
                allowed_md_lines.append(f"- `{nid}` — must NOT be used")
            allowed_md_lines.append("")
        default = notation_hints.get("default_notation")
        if isinstance(default, str) and default:
            allowed_md_lines.append(f"**Default notation:** `{default}`")
            allowed_md_lines.append("")
        allowed_path = notation_dir / "allowed-notations.md"
        _write_text(allowed_path, "\n".join(allowed_md_lines).strip() + "\n")
        _record(allowed_path, key="notation_allowed_path")
        manifest["notation_context_paths"] = [
            str(manifest.get("notation_selection_contract_path") or ""),
            str(manifest.get("notation_allowed_path") or ""),
        ]

    context_index = bundle_dir / "context-index.md"
    index_lines = [
        "# OpenCode Workspace Context",
        "",
        "Read these files before planning or executing changes:",
    ]
    preferred_keys = [
        "agents_path",
        "agent_profile_path",
        "task_brief_path",
        "system_prompt_path",
        "hub_context_path",
        "research_context_prompt_path",
        "research_context_json_path",
        "tool_definitions_path",
        "output_schema_path",
        "pattern_selection_contract_path",
        "pattern_allowed_path",
        "notation_selection_contract_path",
        "notation_allowed_path",
    ]
    if include_response_contract:
        preferred_keys.append("response_contract_path")
    for key in preferred_keys:
        rel = str(manifest.get(key) or "").strip()
        if rel:
            index_lines.append(f"- `{rel}`")
    _write_text(context_index, "\n".join(index_lines).strip() + "\n")
    _record(context_index, key="context_index_path")
    return manifest


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
