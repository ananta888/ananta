from __future__ import annotations

import threading
from typing import Optional

AGENT_TEMPLATE_DEFAULTS: dict[str, dict] = {
    "code-reviewer": {
        "scope_mode": "selective",
        "codecompass_profile": "subtask_refactor_navigation",
        "max_files": 50,
    },
    "bugfix-specialist": {
        "scope_mode": "selective",
        "codecompass_profile": "subtask_bugfix_local",
        "max_files": 30,
    },
    "architecture-analyst": {
        "scope_mode": "selective",
        "codecompass_profile": "subtask_architecture_review",
        "max_files": 100,
    },
    "config-integrator": {
        "scope_mode": "selective",
        "codecompass_profile": "subtask_config_integration",
        "max_files": 40,
    },
}


class AgentTemplateRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._overrides: dict[str, dict] = {}

    def get_context_policy_defaults(self, agent_template: Optional[str]) -> Optional[dict]:
        if not agent_template:
            return None
        with self._lock:
            if agent_template in self._overrides:
                return dict(self._overrides[agent_template])
        return dict(AGENT_TEMPLATE_DEFAULTS[agent_template]) if agent_template in AGENT_TEMPLATE_DEFAULTS else None

    def register_override(self, agent_template: str, policy: dict) -> None:
        with self._lock:
            self._overrides[agent_template] = dict(policy)

    def clear_override(self, agent_template: str) -> None:
        with self._lock:
            self._overrides.pop(agent_template, None)

    def list_templates(self) -> list[dict]:
        result = []
        with self._lock:
            for template_id, defaults in AGENT_TEMPLATE_DEFAULTS.items():
                entry = {"id": template_id, "defaults": dict(defaults)}
                if template_id in self._overrides:
                    entry["override"] = dict(self._overrides[template_id])
                result.append(entry)
        return result


_instance: Optional[AgentTemplateRegistry] = None
_lock = threading.Lock()


def get_agent_template_registry() -> AgentTemplateRegistry:
    global _instance
    with _lock:
        if _instance is None:
            _instance = AgentTemplateRegistry()
        return _instance
