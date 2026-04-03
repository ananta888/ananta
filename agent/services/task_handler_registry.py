from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from flask import Flask, current_app


class TaskHandler(Protocol):
    def propose(self, **kwargs: Any) -> Any: ...

    def execute(self, **kwargs: Any) -> Any: ...


@dataclass
class TaskHandlerRegistry:
    """Registers task-kind specific handlers behind a narrow extension seam."""

    _handlers: dict[str, TaskHandler] = field(default_factory=dict)
    _descriptors: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register(
        self,
        task_kind: str,
        handler: TaskHandler,
        *,
        capabilities: list[str] | None = None,
        safety_flags: dict[str, Any] | None = None,
        verification_hooks: list[str] | None = None,
    ) -> None:
        key = self._normalize(task_kind)
        if not key:
            raise ValueError("task_kind_required")
        self._handlers[key] = handler
        self._descriptors[key] = {
            "task_kind": key,
            "capabilities": [str(item).strip().lower() for item in (capabilities or []) if str(item).strip()],
            "safety_flags": dict(safety_flags or {}),
            "verification_hooks": [str(item).strip() for item in (verification_hooks or []) if str(item).strip()],
        }

    def unregister(self, task_kind: str) -> None:
        key = self._normalize(task_kind)
        if key:
            self._handlers.pop(key, None)
            self._descriptors.pop(key, None)

    def resolve(self, task_kind: str | None) -> TaskHandler | None:
        key = self._normalize(task_kind)
        if not key:
            return None
        return self._handlers.get(key)

    def supported_task_kinds(self) -> list[str]:
        return sorted(self._handlers.keys())

    def resolve_descriptor(self, task_kind: str | None) -> dict[str, Any] | None:
        key = self._normalize(task_kind)
        if not key:
            return None
        descriptor = self._descriptors.get(key) or {}
        if not descriptor:
            return None
        return {
            "task_kind": key,
            "capabilities": list(descriptor.get("capabilities") or []),
            "safety_flags": dict(descriptor.get("safety_flags") or {}),
            "verification_hooks": list(descriptor.get("verification_hooks") or []),
        }

    @staticmethod
    def _normalize(task_kind: str | None) -> str:
        return str(task_kind or "").strip().lower()


def get_task_handler_registry(app: Flask | None = None) -> TaskHandlerRegistry:
    target_app = app or current_app
    registry = target_app.extensions.get("task_handler_registry")
    if registry is None:
        registry = TaskHandlerRegistry()
        target_app.extensions["task_handler_registry"] = registry
    return registry


def register_task_handler(
    task_kind: str,
    handler: TaskHandler,
    app: Flask | None = None,
    *,
    capabilities: list[str] | None = None,
    safety_flags: dict[str, Any] | None = None,
    verification_hooks: list[str] | None = None,
) -> TaskHandlerRegistry:
    registry = get_task_handler_registry(app)
    registry.register(
        task_kind,
        handler,
        capabilities=capabilities,
        safety_flags=safety_flags,
        verification_hooks=verification_hooks,
    )
    return registry
