"""AWTCL-016: external agents as propose/review tools.

OpenCode, Hermes, Aider and Codex stay hub-controlled backends: the
ananta-worker may only request *proposals* or *reviews* from them. The
backend selection runs through ``ToolRoutingService`` (no separate
routing logic); ineligible backends return a policy-style error instead
of being invoked. The proposal prompt explicitly forbids mutations so an
external agent never executes uncontrolled changes from this path.
"""
from __future__ import annotations

from typing import Any

from agent.services.tools._evidence import build_evidence_entry, build_tool_result

_TOOL_TO_BACKEND = {
    "opencode.propose": ("opencode", "patch_propose"),
    "hermes.review": ("hermes", "review"),
    "aider.propose": ("aider", "patch_propose"),
    "codex.propose": ("codex", "patch_propose"),
}

_PROPOSE_GUARD = (
    "PROPOSAL-ONLY MODE: Do not modify any files, do not run shell commands, "
    "do not execute mutations. Return a textual proposal/review only."
)


def run_external_backend_tool(
    *,
    tool_name: str,
    workspace_dir: str,
    arguments: dict[str, Any],
    tool_call_id: str,
    config: dict[str, Any] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    mapping = _TOOL_TO_BACKEND.get(str(tool_name or "").strip())
    if mapping is None:
        return build_tool_result(
            tool_name=str(tool_name or ""), tool_call_id=tool_call_id, status="error", error="unknown_external_tool"
        )
    backend, task_kind = mapping
    prompt = str((arguments or {}).get("prompt") or "").strip()
    if not prompt:
        return build_tool_result(tool_name=tool_name, tool_call_id=tool_call_id, status="error", error="prompt_required")

    from agent.services.tool_routing_service import get_tool_routing_service

    routing = get_tool_routing_service().route_execution_backend(
        task_kind=task_kind,
        requested_backend=backend,
        required_capabilities=None,
        governance_mode=str((config or {}).get("governance_mode") or "balanced"),
        agent_cfg=(config or {}).get("agent_cfg"),
    )
    decision = dict(routing.get("decision") or {})
    selected = str(decision.get("selected_target") or "")
    if selected != backend:
        return build_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status="policy_blocked",
            risk_class="external_agent",
            error=f"backend_not_eligible:{backend}",
            policy_decision={"router_decision": decision},
        )

    from agent.cli_backends.sgpt import run_llm_cli_command

    guarded_prompt = f"{_PROPOSE_GUARD}\n\n{prompt}"
    rc, out, err, backend_used = run_llm_cli_command(
        prompt=guarded_prompt,
        backend=backend,
        timeout=timeout,
        workdir=str(workspace_dir) if workspace_dir else None,
    )
    if rc != 0 and not out:
        return build_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status="error",
            risk_class="external_agent",
            error=(err or f"backend_failed_rc_{rc}")[:500],
            data={"backend_used": backend_used},
        )
    entry, _ = build_evidence_entry(
        kind="external_proposal",
        path=backend_used,
        excerpt=out,
        source=backend_used,
        max_excerpt_chars=6000,
    )
    return build_tool_result(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status="ok",
        risk_class="external_agent",
        evidence=[entry],
        data={"backend_used": backend_used, "router_decision": decision},
    )
