from __future__ import annotations

from typing import Any

RISK_LEVEL_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def normalize_risk_level(value: str | None, default: str = "medium") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in RISK_LEVEL_RANK else default


def max_risk_level(*levels: str) -> str:
    normalized = [normalize_risk_level(level, default="low") for level in levels if str(level or "").strip()]
    if not normalized:
        return "low"
    return max(normalized, key=lambda item: RISK_LEVEL_RANK.get(item, 1))


def has_file_access_signal(command: str | None, tool_calls: list[dict] | None) -> bool:
    command_text = str(command or "").lower()
    if any(token in command_text for token in ("cat ", "sed ", "awk ", "tee ", "cp ", "mv ", "touch ", "chmod ", "chown ")):
        return True
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "").strip().lower()
        if any(token in name for token in ("file", "template", "config")):
            return True
    return False


def has_terminal_signal(command: str | None) -> bool:
    return bool(str(command or "").strip())


def classify_command_risk(command: str | None) -> str:
    text = str(command or "").strip().lower()
    if not text:
        return "low"
    if any(token in text for token in ("rm -rf", "mkfs", "shutdown", "reboot", "kill -9", "dd if=", "curl ")):
        return "critical"
    if any(token in text for token in ("chmod ", "chown ", "docker ", "systemctl ", "kubectl ", "apt-get ", "pip install ")):
        return "high"
    if any(token in text for token in ("cat ", "sed ", "grep ", "rg ", "ls ")):
        return "medium"
    return "medium"


def classify_tool_calls_risk(tool_calls: list[dict] | None, guard_cfg: dict[str, Any] | None = None) -> str:
    calls = [item for item in (tool_calls or []) if isinstance(item, dict)]
    if not calls:
        return "low"
    guard = ((guard_cfg or {}).get("llm_tool_guardrails") or {}) if isinstance(guard_cfg, dict) else {}
    class_map = dict(guard.get("tool_classes") or {})
    class_rank = {"read": "low", "unknown": "medium", "write": "high", "admin": "critical"}
    risk = "low"
    for call in calls:
        name = str(call.get("name") or "").strip()
        klass = str(class_map.get(name) or "unknown").strip().lower()
        risk = max_risk_level(risk, class_rank.get(klass, "medium"))
    return risk
