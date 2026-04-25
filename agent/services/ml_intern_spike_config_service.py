from __future__ import annotations


def normalize_ml_intern_spike_config(value: dict | None) -> dict:
    payload = dict(value or {})
    try:
        timeout_seconds = int(payload.get("timeout_seconds", 180))
    except (TypeError, ValueError):
        timeout_seconds = 180
    timeout_seconds = max(10, min(timeout_seconds, 900))
    try:
        max_prompt_chars = int(payload.get("max_prompt_chars", 6000))
    except (TypeError, ValueError):
        max_prompt_chars = 6000
    max_prompt_chars = max(512, min(max_prompt_chars, 64000))
    try:
        max_output_chars = int(payload.get("max_output_chars", 8000))
    except (TypeError, ValueError):
        max_output_chars = 8000
    max_output_chars = max(512, min(max_output_chars, 64000))
    env_allowlist = [
        str(item or "").strip()
        for item in list(payload.get("env_allowlist") or [])
        if str(item or "").strip()
    ]
    return {
        "enabled": bool(payload.get("enabled", False)),
        "command_template": str(payload.get("command_template") or payload.get("command") or "").strip(),
        "timeout_seconds": timeout_seconds,
        "max_prompt_chars": max_prompt_chars,
        "max_output_chars": max_output_chars,
        "working_dir": str(payload.get("working_dir") or "").strip() or None,
        "env_allowlist": sorted(set(env_allowlist)),
    }
