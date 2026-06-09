"""Helpcenter command handler for the Ananta operator TUI.

Extracted from client_surfaces/operator_tui/commands.py (SPLIT-002).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.services.helpcenter_contract_service import load_helpcenter_index
from agent.services.helpcenter_ingest_service import ingest_github_failures, StaticGithubWorkflowApiClient
from agent.services.planning_summary_doctor_service import doctor_file, fix_file
from client_surfaces.operator_tui.models import CommandResult, OperatorMode, OperatorState, PanelState


def _helpcenter_repo_root() -> Path:
    return Path.cwd()



def _build_helpcenter_payload(*, game: dict[str, object], repo_root: Path) -> dict[str, object]:
    index = load_helpcenter_index(repo_root=repo_root)
    rows = [dict(item) for item in list(index.get("reports") or []) if isinstance(item, dict)]
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    selected_analysis_id = str(game.get("helpcenter_selected_analysis_id") or "").strip()
    if not selected_analysis_id and rows:
        selected_analysis_id = str(rows[0].get("analysis_id") or "")
        game["helpcenter_selected_analysis_id"] = selected_analysis_id
    selected_row = next((item for item in rows if str(item.get("analysis_id") or "") == selected_analysis_id), rows[0] if rows else None)
    selected_analysis: dict[str, object] = {}
    if isinstance(selected_row, dict):
        json_ref = str(selected_row.get("json_ref") or "").strip()
        if json_ref:
            json_path = repo_root / json_ref
            if json_path.exists():
                try:
                    payload = json.loads(json_path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        selected_analysis = payload
                except json.JSONDecodeError:
                    selected_analysis = {}
    followup = str(selected_analysis.get("suggested_followup_task") or "").strip()
    return {
        "helpcenter_mode": True,
        "reports": rows,
        "selected_analysis_id": selected_analysis_id,
        "selected_report": dict(selected_row or {}) if isinstance(selected_row, dict) else {},
        "selected_analysis": selected_analysis,
        "followup_suggestion": followup,
        "last_ingest": dict(game.get("helpcenter_last_ingest") or {}),
    }


def handle_helpcenter_command(args: list[str], state: OperatorState) -> CommandResult:
    """Dispatch :helpcenter subcommands."""
    game = dict(state.header_logo_game or {})
    repo_root = _helpcenter_repo_root()
    if not args:
        payload = _build_helpcenter_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message="helpcenter opened",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    sub = str(args[0]).lower()
    if sub == "open":
        if len(args) < 2:
            return CommandResult(state, "helpcenter open <analysis-id>", handled=False)
        game["helpcenter_selected_analysis_id"] = str(args[1]).strip()
        payload = _build_helpcenter_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"helpcenter open {args[1]}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "suggest-followup":
        analysis_id = str(args[1]).strip() if len(args) > 1 else str(game.get("helpcenter_selected_analysis_id") or "").strip()
        if analysis_id:
            game["helpcenter_selected_analysis_id"] = analysis_id
        payload = _build_helpcenter_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        followup = str(payload.get("followup_suggestion") or "").strip() or "no follow-up suggestion available"
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message="helpcenter follow-up suggestion ready (no task created)",
            ),
            json.dumps({"analysis_id": analysis_id, "followup_suggestion": followup, "auto_create": False, "payload": payload}, ensure_ascii=False),
        )
    if sub == "ingest":
        source_kind = str(args[1]).lower() if len(args) > 1 else ""
        if source_kind != "github-failures":
            return CommandResult(state, "helpcenter ingest github-failures [--repo owner/repo] [--limit N] [--dry-run]", handled=False)
        repo = "ananta888/ananta"
        limit = 5
        dry_run = False
        tokens = list(args[2:])
        i = 0
        while i < len(tokens):
            token = str(tokens[i]).strip().lower()
            if token == "--repo" and i + 1 < len(tokens):
                repo = str(tokens[i + 1]).strip()
                i += 2
                continue
            if token == "--limit" and i + 1 < len(tokens):
                try:
                    limit = max(1, int(str(tokens[i + 1]).strip()))
                except ValueError:
                    return CommandResult(state, "helpcenter ingest --limit requires integer", handled=False)
                i += 2
                continue
            if token == "--dry-run":
                dry_run = True
                i += 1
                continue
            i += 1
        mock_rows = [dict(item) for item in list(game.get("helpcenter_mock_github_rows") or []) if isinstance(item, dict)]
        api_client = StaticGithubWorkflowApiClient(rows=mock_rows) if mock_rows else None
        result = ingest_github_failures(
            repo=repo,
            limit=limit,
            dry_run=dry_run,
            repo_root=str(repo_root),
            api_client=api_client,
        )
        game["helpcenter_last_ingest"] = result
        payload = _build_helpcenter_payload(game=game, repo_root=repo_root)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = payload
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        mode_label = "dry-run" if dry_run else "write"
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"helpcenter ingest {mode_label} found={result.get('found')} written={result.get('written')}",
            ),
            json.dumps({"ingest": result, "payload": payload}, ensure_ascii=False),
        )
    return CommandResult(
        state,
        "helpcenter | helpcenter open <analysis-id> | helpcenter ingest github-failures [--repo owner/repo] [--limit N] [--dry-run] | helpcenter suggest-followup [analysis-id]",
        handled=False,
    )
