from __future__ import annotations

from typing import Any

from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.ops_api_client import OpsApiClient


def handle_ops_command(args: list[str], state: OperatorState) -> CommandResult:
    subcommand = str(args[0] if args else "status").lower()
    token = _token_from_state(state)
    client = OpsApiClient(state.endpoint, token=token)
    if subcommand not in {"status", "git", "docker", "compose"}:
        return CommandResult(state, "ops status|git|docker|compose", handled=False)

    if subcommand == "git":
        payload: dict[str, Any] = {"git": client.git_status()}
    elif subcommand == "docker":
        payload = {"docker": client.docker_status()}
    elif subcommand == "compose":
        payload = {"compose": client.compose_projects()}
    else:
        payload = client.snapshot()

    section_payloads = dict(state.section_payloads or {})
    section_payloads["ops"] = payload
    lights = payload.get("traffic_lights") or {}
    suffix = ""
    if lights:
        suffix = f" git_dirty={lights.get('git_dirty')} docker={lights.get('docker_engine')} compose={lights.get('compose_health')}"
    return CommandResult(
        state.with_updates(
            mode=OperatorMode.NORMAL,
            command_line="",
            section_id="ops",
            focus=FocusPane.CONTENT,
            section_payloads=section_payloads,
            status_message=f"ops {subcommand}{suffix}",
        ),
        f"ops {subcommand}",
    )


def _token_from_state(state: OperatorState) -> str:
    audit = dict(state.audit_context or {})
    token = str(audit.get("token") or audit.get("auth_token") or "")
    if token:
        return token
    header = dict(state.header_logo_game or {})
    return str(header.get("token") or header.get("auth_token") or "")
