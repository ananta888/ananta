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
        "code_analysis",
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
        '- `tool_request` — {"kind":"tool_request","tool_name":"repo.read_file_range",'
        '"arguments":{"path":"app.py","line_start":1,"line_end":80}}',
        '- Tests: {"kind":"tool_request","tool_name":"test.run",'
        '"arguments":{"command":"EXACT_HUB_ALLOWLISTED_COMMAND"}}',
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
            "- Nutze fuer die Suche nach relevanten Stellen CodeCompass statt blindem Lesen: "
            '{"kind":"tool_request","tool_name":"codecompass.plan_context","arguments":{"query":"..."}} '
            "(auch codecompass.search_symbols, codecompass.search, codecompass.get_file_context).",
            "- Erst codecompass.plan_context, dann repo.read_file_range nur auf die genannten Zeilenbereiche.",
        ]
    else:
        common += [
            '- `patch_request` — {"kind": "patch_request", "target_path": "rel/pfad", "variant": "unified_diff|write_file_create_only|replace_range", "unified_diff": "...", "line_start": 10, "line_end": 20, "replacement": "...", "expected_old_hash": "...", "reason": "..."}',
            "",
            "Regeln (strict_patch_request):",
            "- Du darfst KEINE Dateien direkt ändern; jeder Patch wird vom Hub einzeln validiert und angewendet.",
            "- Nutze fuer Brownfield-Aufgaben bevorzugt: codecompass.plan_context -> repo.read_file_range -> patch_request -> workspace.diff -> test.run.",
            "- CodeCompass-Werkzeuge stehen bereit und sind der schnellste Weg zu relevanten Stellen: "
            '{"kind":"tool_request","tool_name":"codecompass.plan_context","arguments":{"query":"..."}}, '
            "codecompass.search_symbols, codecompass.search, "
            "codecompass.get_file_context, codecompass.architecture_query.",
            "- Rätsele NICHT mit repo.read_file_range über Dateien: nutze zuerst codecompass.plan_context, "
            "dann repo.read_file_range nur auf die dort genannten Zeilenbereiche, dann patch_request.",
            "- Verwende replace_range oder unified_diff statt kompletter Datei-Rewrites; repo.write_file ist nur fuer kleine neue Dateien gedacht.",
            "- PatchResults, Diffs und Policy-Ergebnisse kommen als Evidence zurück.",
        ]
    common += [
        "- Dateien, die im Auftrag deklariert sind, dürfen `file_not_found` melden — sie existieren dann "
        "NOCH NICHT und muessen ERST werden: lege sie mit `workspace_write` (neue Datei) oder "
        "`patch_request` an. `file_not_found` ist kein Blocker, sondern der Hinweis auf eine neue Datei.",
        "- Fuehre VOR `final_answer` zwingend `test.run` mit dem exakten allowlisteten "
        "Befehl aus und warte auf dessen Ergebnis.",
        "- Behaupte keine Änderung, die der Hub nicht per Result bestätigt hat.",
        "- Dieses innere Protokoll verwendet `kind`, nicht das äußere `command`/`tool_calls`-Proposal-Schema.",
        "- Fuehre VOR `final_answer` zwingend `test.run` mit dem exakten allowlisteten "
        "Befehl aus und warte auf dessen Ergebnis.",
        "- final_answer erst, wenn der letzte PolicyResult akzeptabel ist; sonst markiere das Ergebnis als partial/blocked.",
    ]
    return "\n".join(common)


ANALYSIS_CORRECTION_NUDGE = (
    "final_answer is blocked: the required source analysis is missing "
    "(no successful codecompass observation or no accepted code_analysis). "
    "Emit these protocol answers across the next turns, in order: "
    '(1) {"kind":"tool_request","tool_name":"codecompass.plan_context","arguments":{"query":"..."}} '
    "(or codecompass.get_file_context), "
    '(2) {"kind":"code_analysis","sources":[{"path":"rel/pfad","file_sha256":"HASH_AUS_LESEERGEBNIS",'
    '"line_start":1,"line_end":20}],"findings":"...","change_reasoning":"...","test_reasoning":"..."}, '
    "and only then final_answer."
)


ANALYSIS_STEP_CORRECTION_NUDGE = (
    "Your codecompass observations are already registered. final_answer stays blocked until you "
    "emit the analysis step itself — reply in the next turn with exactly one protocol answer and "
    "nothing else: "
    '{"kind":"code_analysis","sources":[{"path":"rel/pfad","file_sha256":"HASH_AUS_DEM_LESEERGEBNIS",'
    '"line_start":1,"line_end":20}],"findings":"beobachtetes Verhalten und Aufrufbeziehungen",'
    '"change_reasoning":"Aenderungsansatz und Architekturfolgen","test_reasoning":"konkrete Verifikation '
    'und relevante Regressionen"} — the sources must be ranges you actually observed, with their '
    "hashes from the read results. Only after this step is accepted may you answer final_answer."
)


ANALYSIS_CITATION_CORRECTION_NUDGE = (
    "Your code_analysis was rejected because its source citations are not in the observed set. "
    "Do not invent ranges and do not widen them: reply in the next turn with exactly one protocol "
    "answer citing ONLY entries copied verbatim from the observed_sources list of the rejection "
    "evidence (path, file_sha256, line_start and line_end must lie inside one observed range): "
    '{"kind":"code_analysis","sources":[{"path":"aus_observed_sources","file_sha256":"aus_observed_sources",'
    '"line_start":1,"line_end":20}],"findings":"...","change_reasoning":"...","test_reasoning":"..."}. '
    "Citations you did not observe keep the analysis blocked."
)


def required_analysis_instructions() -> str:
    """Describe the source-bound action consumed by the runtime prerequisite."""
    return (
        '\nVor jeder Aenderung ist Quellcodeanalyse erforderlich: zuerst einen erfolgreichen '
        'CodeCompass-Aufruf ausfuehren, dann relevante Quellcodebereiche mit repo.read_file_range lesen '
        '(codecompass.plan_context materialisiert Bereiche automatisch). Danach explizit antworten: '
        '{"kind":"code_analysis","sources":[{"path":"app.py","file_sha256":"HASH_AUS_READ_RESULT",'
        '"line_start":1,"line_end":20}],"findings":"Beobachtetes Verhalten und Aufrufbeziehungen",'
        '"change_reasoning":"Aenderungsansatz und Architekturfolgen",'
        '"test_reasoning":"Konkrete Verifikation und relevante Regressionen"}. '
        'Quellen muessen erfolgreich gelesenen aktuellen Bereichen entsprechen. '
        'Ohne bestaetigte Analyse sind Schreibaktionen und final_answer gesperrt.'
    )


def _prompt_within_budget(prompt: str) -> bool:
    """Whether the backend running this loop would accept ``prompt``.

    The workspace-mutation loop is only reached through ``run_sgpt_command``
    (``agent/cli_backends/sgpt.py``), which rejects a prompt above
    ``prompt_token_limit("sgpt")`` *before* dispatching it. Verifying against that same
    limit fits the evidence to the gate that decides, not to the planner's cheaper
    ~4-chars-per-token estimate -- the planner approves prompts the gate refuses, and such
    a refusal aborts the whole proposal instead of trimming the context.
    """
    from agent.cli_backends.budget import check_prompt_budget, prompt_token_limit

    return check_prompt_budget(prompt, max_tokens=prompt_token_limit("sgpt")) is None


def build_iteration_prompt(
    *,
    original_prompt: str,
    instructions: str,
    evidence_blocks: list[dict[str, Any]],
    iteration: int,
    max_iterations: int,
    max_chars_per_block: int,
    nudge: str | None = None,
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
    if nudge:
        parts += ["", "## Handlung erforderlich", nudge]
    if evidence_blocks:
        from agent.cli_backends.context_budget import available_chars, fit_blocks_verified

        parts += ["", "## Feedback aus vorherigen Iterationen (Evidence, dedupliziert)"]
        formatted = []
        for block in evidence_blocks[-_MAX_EVIDENCE_BLOCKS:]:
            serialized = json.dumps(block, ensure_ascii=False, indent=2)
            if len(serialized) > max_chars_per_block:
                serialized = serialized[: max_chars_per_block - 14] + "\n…[truncated]"
            formatted.append(f"```json\n{serialized}\n```")
        # LCTX-012: the evidence gets what the window leaves after task and instructions, and
        # it must additionally survive the gate that rejects the prompt before the model call.
        prefix = list(parts)
        parts = prefix + fit_blocks_verified(
            formatted,
            budget_chars=available_chars("\n".join(prefix)),
            site="mutation_loop.evidence",
            iteration=iteration,
            render=lambda kept: "\n".join(prefix + list(kept)),
            allowed=_prompt_within_budget,
        )
    return "\n".join(parts)
