from __future__ import annotations

from agent.common.sgpt import get_cli_backend_capabilities


class WorkerCapabilityService:
    """Maps worker tooling to hub-visible capability descriptors."""

    def build_tooling_capability_map(self) -> dict[str, dict]:
        capabilities = get_cli_backend_capabilities()
        tool_keys = ("sgpt", "codex", "opencode", "aider", "mistral_code")
        mapping: dict[str, dict] = {}
        for key in tool_keys:
            info = capabilities.get(key) or {}
            mapping[key] = {
                "tool": key,
                "available": bool(info.get("available")),
                "supports_model_selection": bool(info.get("supports_model_selection")),
                "supported_options": list(info.get("supported_options") or []),
                "install_hint": info.get("install_hint"),
            }
        return mapping


worker_capability_service = WorkerCapabilityService()


def get_worker_capability_service() -> WorkerCapabilityService:
    return worker_capability_service
