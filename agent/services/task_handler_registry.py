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

    def register(self, task_kind: str, handler: TaskHandler) -> None:
        key = self._normalize(task_kind)
        if not key:
            raise ValueError("task_kind_required")
        self._handlers[key] = handler

    def unregister(self, task_kind: str) -> None:
        key = self._normalize(task_kind)
        if key:
            self._handlers.pop(key, None)

    def resolve(self, task_kind: str | None) -> TaskHandler | None:
        key = self._normalize(task_kind)
        if not key:
            return None
        return self._handlers.get(key)

    def supported_task_kinds(self) -> list[str]:
        return sorted(self._handlers.keys())

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


def register_task_handler(task_kind: str, handler: TaskHandler, app: Flask | None = None) -> TaskHandlerRegistry:
    registry = get_task_handler_registry(app)
    registry.register(task_kind, handler)
    return registry
