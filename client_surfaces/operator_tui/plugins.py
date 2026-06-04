from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

from client_runtime import process


class ContentPlugin(ABC):
    """Base for TUI content plugins — render content or launch external tools."""

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    def label(self) -> str:
        return self.id

    def render(self, payload: dict[str, Any], width: int, height: int, selected: int) -> list[str]:
        """Return lines for the content pane. Empty list = not handled by this plugin."""
        return []

    def can_launch(self, payload: dict[str, Any], selected: int) -> bool:
        return False

    def launch(self, payload: dict[str, Any], selected: int) -> None:
        """Run an external tool. Called inside run_in_terminal — TUI is suspended."""
        pass


class EditorPlugin(ContentPlugin):
    """Open selected item in $EDITOR / $VISUAL / vim."""

    @property
    def id(self) -> str:
        return "editor"

    @property
    def label(self) -> str:
        return "Editor"

    def _resolve_path(self, payload: dict[str, Any], selected: int) -> str | None:
        return resolve_item_reference(payload, selected)

    def can_launch(self, payload: dict[str, Any], selected: int) -> bool:
        return self._resolve_path(payload, selected) is not None

    def launch(self, payload: dict[str, Any], selected: int) -> None:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vim"
        path = self._resolve_path(payload, selected)
        process.run([editor] + ([path] if path else []))


class ShellPlugin(ContentPlugin):
    """Run an arbitrary shell command and display output in the content pane."""

    def __init__(self, plugin_id: str, command: str, label: str = "") -> None:
        self._id = plugin_id
        self._command = command
        self._label = label or plugin_id

    @property
    def id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return self._label

    def can_launch(self, payload: dict[str, Any], selected: int) -> bool:
        return True

    def launch(self, payload: dict[str, Any], selected: int) -> None:
        process.run(self._command, shell=True)


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, ContentPlugin] = {}

    def register(self, plugin: ContentPlugin) -> None:
        self._plugins[plugin.id] = plugin

    def get(self, plugin_id: str) -> ContentPlugin | None:
        return self._plugins.get(plugin_id)

    def all(self) -> list[ContentPlugin]:
        return list(self._plugins.values())

    def launcher_for(self, payload: dict[str, Any], selected: int) -> ContentPlugin | None:
        """Return the first plugin that can launch for the current selection."""
        for plugin in self._plugins.values():
            if plugin.can_launch(payload, selected):
                return plugin
        return None


def default_plugin_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.register(EditorPlugin())
    return registry


def resolve_item_reference(payload: dict[str, Any], selected: int) -> str | None:
    items = payload.get("items") or []
    if not items or selected < 0 or selected >= len(items):
        return None
    item = items[selected]
    if not isinstance(item, dict):
        return None
    value = item.get("path") or item.get("file") or item.get("id")
    text = str(value or "").strip()
    return text or None
