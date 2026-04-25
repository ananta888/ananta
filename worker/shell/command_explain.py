from __future__ import annotations

import shlex


def explain_command(command: str) -> dict[str, str]:
    normalized = str(command or "").strip()
    if not normalized:
        return {
            "summary": "No command provided.",
            "effects": "No effects. Provide a bounded command string.",
        }
    try:
        parts = shlex.split(normalized)
    except ValueError:
        parts = [normalized]
    binary = parts[0] if parts else normalized
    args = " ".join(parts[1:]) if len(parts) > 1 else ""
    return {
        "summary": f"Planned command uses '{binary}'{f' with args: {args}' if args else ''}.",
        "effects": "Likely effects depend on command semantics and current working directory; execute only via approval-gated path.",
    }
