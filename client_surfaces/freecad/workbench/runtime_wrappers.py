from __future__ import annotations

from typing import Any

from client_surfaces.freecad.workbench.client import FreecadHubClient
from client_surfaces.freecad.workbench.execution import execute_approved_macro


def invoke_read_operation(client: FreecadHubClient, *, context: dict[str, Any], goal: str) -> dict[str, Any]:
    return client.submit_goal(goal=goal, context=context, capability_id="freecad.model.inspect")


def invoke_export_plan(client: FreecadHubClient, *, fmt: str, target_path: str, selection_only: bool = False) -> dict[str, Any]:
    return client.request_export_plan(fmt=fmt, target_path=target_path, selection_only=selection_only)


def invoke_macro_execution(
    client: FreecadHubClient,
    *,
    script_hash: str,
    session_id: str,
    correlation_id: str,
    approval_id: str | None,
    macro_text: str,
) -> dict[str, Any]:
    return execute_approved_macro(
        client,
        script_hash=script_hash,
        session_id=session_id,
        correlation_id=correlation_id,
        approval_id=approval_id,
        macro_text=macro_text,
    )
