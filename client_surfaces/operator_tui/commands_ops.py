from __future__ import annotations

import os
from typing import Any

from client_surfaces.common.workflow_runtime_projection import (
    WorkflowRuntimeProjectionError,
    operations_status_line,
    require_operation_detail,
    require_operations_list,
)
from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.ops_api_client import OpsApiClient


def handle_ops_command(args: list[str], state: OperatorState) -> CommandResult:
    subcommand = str(args[0] if args else "status").lower()
    token = _token_from_state(state)
    client = OpsApiClient(state.endpoint, token=token)
    if subcommand not in {"status", "git", "docker", "compose", "runtime"}:
        return CommandResult(state, "ops status|git|docker|compose|runtime", handled=False)

    if subcommand == "runtime":
        return _handle_runtime_ops(args[1:], state, client)
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
    return str(
        header.get("token")
        or header.get("auth_token")
        or os.environ.get("ANANTA_AUTH_TOKEN")
        or ""
    )


def _handle_runtime_ops(args: list[str], state: OperatorState, client: OpsApiClient) -> CommandResult:
    action = str(args[0] if args else "list").lower()
    try:
        if action == "run":
            if len(args) != 2:
                return CommandResult(state, "ops runtime run <run-id>", handled=False)
            payload: dict[str, Any] = {"run": require_operation_detail(client.workflow_runtime_run(args[1]))}
            status = f"workflow runtime run={args[1]}"
        else:
            if action not in {"list", "healthy", "degraded", "stale", "parity_gap", "unverified"}:
                return CommandResult(
                    state,
                    "ops runtime [list|healthy|degraded|stale|parity_gap|unverified|run <run-id>]",
                    handled=False,
                )
            health = "" if action == "list" else action
            projection = require_operations_list(client.workflow_runtime_operations(health=health))
            payload = projection
            status = f"workflow runtime {operations_status_line(projection)}"
    except WorkflowRuntimeProjectionError as exc:
        return CommandResult(
            state.with_updates(status_message=f"workflow runtime unavailable: {exc}"),
            f"workflow runtime unavailable: {exc}",
            handled=False,
        )

    section_payloads = dict(state.section_payloads or {})
    section_payloads["workflow_runtime"] = payload
    return CommandResult(
        state.with_updates(
            mode=OperatorMode.NORMAL,
            command_line="",
            section_id="ops",
            focus=FocusPane.CONTENT,
            section_payloads=section_payloads,
            status_message=status,
        ),
        status,
    )
