"""Derived architecture wiki pages. Never source of truth."""

from __future__ import annotations

from typing import Any, Mapping


def render_wiki(result: Mapping[str, Any]) -> str:
    lines = [
        "# Architecture intelligence",
        "",
        f"Snapshot: `{result.get('snapshot_ref') or 'unknown'}`",
        f"Algorithm: `{((result.get('algorithm') or {}).get('name'))}` fingerprint `{((result.get('algorithm') or {}).get('fingerprint'))}`",
        f"Coverage: {result.get('coverage')}",
        "",
        "## Communities",
    ]
    for community in list(result.get("communities") or [])[:20]:
        lines.append(f"- **{community.get('label')}** (`{community.get('id')}`): {community.get('size')} members")
    lines.extend(["", "## Smells"])
    smells = list(result.get("smells") or [])
    if not smells:
        lines.append("- none")
    for smell in smells:
        lines.append(f"- {smell.get('kind')} ({smell.get('severity')}): {', '.join(smell.get('nodes') or [])}")
    lines.extend(["", "This page is derived documentation and must not be written back as graph evidence."])
    return "\n".join(lines)
