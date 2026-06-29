"""Operator-TUI: Workflow Adapter Status Panel (LCG-056, LCG-057)."""
from __future__ import annotations

from typing import Any


# ANSI color codes
_RESET = "\033[0m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_GRAY = "\033[90m"
_BOLD = "\033[1m"

_STATUS_COLORS = {
    "ready": _GREEN,
    "degraded": _YELLOW,
    "blocked": _RED,
    "disabled": _GRAY,
    "unavailable": _GRAY,
}

_RISK_COLORS = {
    "low": _GREEN,
    "medium": _YELLOW,
    "high": _RED,
    "critical": _RED,
}


def render_workflow_adapter_status(adapters: list[dict[str, Any]]) -> str:
    """Render adapter list as ANSI-formatted table.

    Columns: adapter_id | status | enabled | capabilities (first 3) | reason
    """
    if not adapters:
        return "Keine Workflow-Adapter registriert."

    lines = [
        f"{_BOLD}Workflow Adapter Status{_RESET}",
        "-" * 80,
        f"{'Adapter':<25} {'Status':<12} {'Enabled':<8} {'Capabilities':<30} Reason",
        "-" * 80,
    ]

    for a in adapters:
        adapter_id = str(a.get("adapter_id", ""))
        status = str(a.get("status", "unknown"))
        enabled = a.get("enabled", False)
        capabilities = list(a.get("capabilities") or [])[:3]
        reason = str(a.get("reason", ""))

        color = _STATUS_COLORS.get(status, _RESET)
        enabled_str = f"{_GREEN}yes{_RESET}" if enabled else f"{_GRAY}no{_RESET}"
        caps_str = ", ".join(capabilities) if capabilities else "-"

        line = (
            f"{adapter_id:<25} "
            f"{color}{status:<12}{_RESET} "
            f"{enabled_str:<20} "  # extra space for ANSI codes
            f"{caps_str:<30} "
            f"{reason}"
        )
        lines.append(line)

    lines.append("-" * 80)
    return "\n".join(lines)


def fetch_adapter_status(hub_base_url: str) -> list[dict[str, Any]]:
    """Fetch adapter status from Hub API.

    Returns list of adapter dicts, empty list on error.
    """
    try:
        import requests
        url = hub_base_url.rstrip("/") + "/api/workflow_adapters/"
        resp = requests.get(url, timeout=5)
        if resp.ok:
            data = resp.json()
            return data.get("adapters") or []
        return []
    except Exception:
        return []


def render_dry_run_plan(dry_run_result: dict[str, Any]) -> str:
    """Render a DryRunResult as human-readable text (LCG-057).

    Shows plan_steps, risk_level, approval_required, blocked status, estimated_tokens.
    """
    lines = [f"{_BOLD}Dry-Run Plan{_RESET}", "-" * 60]

    # Blocked banner
    if dry_run_result.get("blocked"):
        block_reason = str(dry_run_result.get("block_reason", ""))
        lines.append(f"{_RED}{_BOLD}BLOCKED: {block_reason}{_RESET}")
        lines.append("-" * 60)
        return "\n".join(lines)

    # Approval banner
    if dry_run_result.get("approval_required"):
        lines.append(f"{_YELLOW}{_BOLD}APPROVAL REQUIRED{_RESET}")
        reasons = dry_run_result.get("approval_reasons") or []
        for r in reasons:
            lines.append(f"  - {r}")

    # Risk level
    risk = str(dry_run_result.get("risk_level", "unknown"))
    risk_color = _RISK_COLORS.get(risk, _RESET)
    lines.append(f"Risk level: {risk_color}{_BOLD}{risk}{_RESET}")

    # Estimated tokens
    est_tokens = dry_run_result.get("estimated_tokens")
    if est_tokens is not None:
        lines.append(f"Estimated tokens: {est_tokens}")

    lines.append("")
    lines.append("Plan steps:")

    steps = dry_run_result.get("plan_steps") or []
    if not steps:
        lines.append("  (no steps)")
    else:
        for s in steps:
            step_num = s.get("step", "?")
            action = s.get("action", "")
            desc = s.get("description", "")
            lines.append(f"  {step_num}. {_BOLD}{action}{_RESET}: {desc}")

    # Policy decisions
    decisions = dry_run_result.get("policy_decisions") or []
    if decisions:
        lines.append("")
        lines.append("Policy decisions:")
        for d in decisions:
            allowed = d.get("allowed", False)
            symbol = f"{_GREEN}ok{_RESET}" if allowed else f"{_RED}blocked{_RESET}"
            tool = d.get("tool") or d.get("resource", "?")
            reason = d.get("reason", "")
            lines.append(f"  [{symbol}] {tool}: {reason}")

    lines.append("-" * 60)
    return "\n".join(lines)
