"""prompts — iteration-prompt building + mutation-output parsing.

Extracted from agent.common.sgpt_workspace_mutation as part of the
SGDEC Welle-2 4-split (T04). Owns the prompt-template and JSON-parsing
logic for the mutation-loop model output.
"""
from __future__ import annotations

import json
from typing import Any

from agent.cli_backends.tool_loop import _extract_json_candidate

# Mutation-loop kind values. Defined here (not imported from the source
# module) to avoid the circular import: agent.common.sgpt_workspace_mutation
# already imports from this sub-module.
KIND_WORKSPACE_WRITE = "workspace_write"
KIND_PATCH_REQUEST = "patch_request"

# KIND_TOOL_REQUEST and the other tool-loop kinds are pulled lazily
# because importing them at top would create a cycle (tool_loop → wm).
def _ensure_kinds() -> set[str]:
    from agent.cli_backends.tool_loop import (
        KIND_CANNOT_CONTINUE,
        KIND_FINAL_ANSWER,
        KIND_NEEDS_APPROVAL,
        KIND_TOOL_REQUEST,
    )

    return {
        KIND_TOOL_REQUEST,
        KIND_FINAL_ANSWER,
        KIND_NEEDS_APPROVAL,
        KIND_CANNOT_CONTINUE,
        KIND_WORKSPACE_WRITE,
        KIND_PATCH_REQUEST,
    }


_MAX_EVIDENCE_BLOCKS = 8


def parse_mutation_output(text: str) -> dict[str, Any] | None:
    """Parse one model answer of the mutation loop (raw or fenced JSON)."""
    candidate = _extract_json_candidate(text)
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    kinds = _ensure_kinds()
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in kinds:
        return None
    if kind == KIND_WORKSPACE_WRITE and not isinstance(payload.get("files"), list):
        return None
    if kind == KIND_PATCH_REQUEST and not str(payload.get("target_path") or "").strip():
        return None
    # KIND_TOOL_REQUEST is checked here to keep back-compat with callers
    # that only import this module.
    if kind == "tool_request" and not str(payload.get("tool_name") or "").strip():
        return None
    return payload


def build_mode_instructions(mode: str) -> str:
    """Return the LLM instructions for the given mutation mode."""
    common = [
        "## Workspace-Mutations-Protokoll (ananta_worker_mutation.v1)",
        "",
        "Antworte mit GENAU EINEM JSON-Objekt. Erlaubte `kind`-Werte:",
        '- `tool_request` — z.B. codecompass.plan_context, repo.grep, repo.read_file_range, workspace.diff, test.run.',
        '- `final_answer` — {"kind": "final_answer", "answer": "...", "summary_of_changes": "..."}',
        "- `needs_approval` / `cannot_continue_without_context`.",
    ]
    if mode == "controlled_workspace":
        common += [
            '- `workspace_write` — {"kind": "workspace_write", "reason": "...", "files": [{"path": "rel/pfad", "content": "kompletter neuer Inhalt"}]}',
            "",
            "Regeln (controlled_workspace):",
            "- Du darfst nur innerhalb der erlaubten (materialisierten) Dateien arbeiten.",
            "- Nach jeder Änderung prüft der Hub Diff, Pfade und Policy; das Ergebnis bekommst du als Evidence.",
            "- Verbessere gezielt anhand von DiffResult/PolicyResult/TestResult statt neu zu raten.",
        ]
    else:
        common += [
            '- `patch_request` — {"kind": "patch_request", "target_path": "rel/pfad", "variant": "unified_diff|write_file_create_only|replace_range", "unified_diff": "...", "line_start": 10, "line_end": 20, "replacement": "...", "expected_old_hash": "...", "reason": "..."}',
            "",
            "Regeln (strict_patch_request):",
            "- Du darfst KEINE Dateien direkt ändern; jeder Patch wird vom Hub einzeln validiert und angewendet.",
            "- Nutze fuer Brownfield-Aufgaben bevorzugt: codecompass.plan_context -> repo.read_file_range -> patch_request -> workspace.diff -> test.run.",
            "- Verwende replace_range oder unified_diff statt kompletter Datei-Rewrites; repo.write_file ist nur fuer kleine neue Dateien gedacht.",
            "- PatchResults, Diffs und Policy-Ergebnisse kommen als Evidence zurück.",
        ]
    common += [
        "- Behaupte keine Änderung, die der Hub nicht per Result bestätigt hat.",
        "- final_answer erst, wenn der letzte PolicyResult akzeptabel ist; sonst markiere das Ergebnis als partial/blocked.",
    ]
    return "\n".join(common)


def build_iteration_prompt(
    *,
    original_prompt: str,
    instructions: str,
    evidence_blocks: list[dict[str, Any]],
    iteration: int,
    max_iterations: int,
    max_chars_per_block: int,
) -> str:
    """Compose the next-iteration prompt with feedback evidence."""
    parts = [
        str(original_prompt or "").rstrip(),
        "",
        "---",
        "",
        instructions,
        "",
        f"Feedback-Iteration {iteration}/{max_iterations}.",
    ]
    if evidence_blocks:
        parts += ["", "## Feedback aus vorherigen Iterationen (Evidence, dedupliziert)"]
        for block in evidence_blocks[-_MAX_EVIDENCE_BLOCKS:]:
            serialized = json.dumps(block, ensure_ascii=False, indent=2)
            if len(serialized) > max_chars_per_block:
                serialized = serialized[: max_chars_per_block - 14] + "\n…[truncated]"
            parts.append(f"```json\n{serialized}\n```")
    return "\n".join(parts)
