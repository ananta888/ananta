from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("agent.tui_tool_registry")

_GLOBAL_DEFAULT_CONFIG: dict[str, Any] = {
    "default_editor": "vim",
    "allow_environment_editor": True,
    "allowed_tools": ["vim", "nvim", "nano", "micro", "helix", "lazygit", "mc", "ranger"],
    "filetype_editors": [
        {"match": "*.py", "editor": "vim", "args": ["{file}"]},
        {"match": "*.ts", "editor": "vim", "args": ["{file}"]},
        {"match": "*.js", "editor": "vim", "args": ["{file}"]},
        {"match": "*.md", "editor": "vim", "args": ["-c", "set ft=markdown", "{file}"]},
        {"match": "*.json", "editor": "vim", "args": ["-c", "set ft=json", "{file}"]},
        {"match": "*.yaml", "editor": "vim", "args": ["-c", "set ft=yaml", "{file}"]},
        {"match": "*.yml", "editor": "vim", "args": ["-c", "set ft=yaml", "{file}"]},
        {"match": "Dockerfile*", "editor": "vim", "args": ["-c", "set ft=dockerfile", "{file}"]},
    ],
    "tool_profiles": [
        {"id": "git_ui", "command": "lazygit", "args": [], "working_directory": "{workspace}"},
        {"id": "file_manager", "command": "ranger", "args": ["{workspace}"], "working_directory": "{workspace}"},
    ],
}

# Known readonly flags per editor. Empty list = readonly not natively supported.
_EDITOR_READONLY_FLAGS: dict[str, list[str]] = {
    "vim": ["-R"],
    "nvim": ["-R"],
    "nano": ["-v"],
    "micro": [],
    "helix": [],
}


@dataclass(frozen=True)
class EditorProfile:
    editor_id: str
    command: str
    args_template: list[str]
    readonly_supported: bool
    readonly_extra_args: list[str]


@dataclass(frozen=True)
class ToolProfile:
    tool_id: str
    command: str
    args_template: list[str]
    working_directory_template: str


@dataclass(frozen=True)
class FiletypeRule:
    match: str
    editor_id: str
    args_template: list[str]


@dataclass
class TuiToolConfig:
    default_editor: str
    allow_environment_editor: bool
    allowed_tools: list[str]
    filetype_rules: list[FiletypeRule]
    editor_profiles: dict[str, EditorProfile]
    tool_profiles: dict[str, ToolProfile]


class TuiConfigValidationError(ValueError):
    pass


def _validate_tool_name(name: str, context: str) -> None:
    stripped = str(name or "").strip()
    if not stripped:
        raise TuiConfigValidationError(f"{context}: tool name must not be empty")
    if os.sep in stripped or ("/" in stripped and os.sep != "/"):
        raise TuiConfigValidationError(f"{context}: tool name must not contain path separators: {stripped!r}")
    if " " in stripped:
        raise TuiConfigValidationError(f"{context}: tool name must not contain spaces: {stripped!r}")


def _load_json_file(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        LOGGER.warning("tui-tools config at %s is not a JSON object — ignored", path)
    except FileNotFoundError:
        pass
    except Exception:
        LOGGER.warning("Failed to load tui-tools config from %s", path, exc_info=True)
    return None


def _merge_configs(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """Merge config layers left-to-right; later layers win for scalar values.
    Lists (allowed_tools, filetype_editors, tool_profiles) are replaced, not appended.
    """
    merged: dict[str, Any] = {}
    for layer in layers:
        if not layer:
            continue
        merged.update(layer)
    return merged


def _build_editor_profiles(allowed_tools: list[str]) -> dict[str, EditorProfile]:
    profiles: dict[str, EditorProfile] = {}
    for tool_id in allowed_tools:
        readonly_flags = _EDITOR_READONLY_FLAGS.get(tool_id, [])
        profiles[tool_id] = EditorProfile(
            editor_id=tool_id,
            command=tool_id,
            args_template=["{file}"],
            readonly_supported=bool(readonly_flags),
            readonly_extra_args=list(readonly_flags),
        )
    return profiles


def _parse_config(raw: dict[str, Any]) -> TuiToolConfig:
    allowed_tools: list[str] = []
    for item in raw.get("allowed_tools", []):
        name = str(item or "").strip()
        _validate_tool_name(name, "allowed_tools")
        allowed_tools.append(name)

    if not allowed_tools:
        allowed_tools = list(_GLOBAL_DEFAULT_CONFIG["allowed_tools"])

    default_editor = str(raw.get("default_editor") or "vim").strip()
    if default_editor not in allowed_tools:
        raise TuiConfigValidationError(
            f"default_editor {default_editor!r} is not in allowed_tools: {allowed_tools}"
        )

    filetype_rules: list[FiletypeRule] = []
    for entry in raw.get("filetype_editors", []):
        if not isinstance(entry, dict):
            continue
        match = str(entry.get("match") or "").strip()
        editor_id = str(entry.get("editor") or "").strip()
        if not match or not editor_id:
            LOGGER.warning("Skipping invalid filetype_editors entry: %s", entry)
            continue
        if editor_id not in allowed_tools:
            raise TuiConfigValidationError(
                f"filetype_editors entry for {match!r} references unknown editor {editor_id!r}"
            )
        args = [str(a) for a in entry.get("args", ["{file}"])]
        filetype_rules.append(FiletypeRule(match=match, editor_id=editor_id, args_template=args))

    tool_profiles: dict[str, ToolProfile] = {}
    for entry in raw.get("tool_profiles", []):
        if not isinstance(entry, dict):
            continue
        tool_id = str(entry.get("id") or "").strip()
        command = str(entry.get("command") or "").strip()
        if not tool_id or not command:
            LOGGER.warning("Skipping invalid tool_profiles entry: %s", entry)
            continue
        if command not in allowed_tools:
            raise TuiConfigValidationError(
                f"tool_profiles entry {tool_id!r} uses command {command!r} not in allowed_tools"
            )
        args = [str(a) for a in entry.get("args", [])]
        workdir = str(entry.get("working_directory") or "{workspace}")
        tool_profiles[tool_id] = ToolProfile(
            tool_id=tool_id,
            command=command,
            args_template=args,
            working_directory_template=workdir,
        )

    return TuiToolConfig(
        default_editor=default_editor,
        allow_environment_editor=bool(raw.get("allow_environment_editor", True)),
        allowed_tools=allowed_tools,
        filetype_rules=filetype_rules,
        editor_profiles=_build_editor_profiles(allowed_tools),
        tool_profiles=tool_profiles,
    )


class TuiToolRegistry:
    def __init__(
        self,
        *,
        user_config_path: str | None = None,
        project_config_path: str | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._config: TuiToolConfig | None = None
        self._user_config_path = user_config_path
        self._project_config_path = project_config_path

    def _resolve_user_config_path(self) -> str:
        if self._user_config_path:
            return self._user_config_path
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
        return os.path.join(xdg, "ananta", "tui-tools.json")

    def _resolve_project_config_path(self) -> str:
        if self._project_config_path:
            return self._project_config_path
        return os.path.join(".ananta", "tui-tools.json")

    def load(self) -> TuiToolConfig:
        with self._lock:
            if self._config is not None:
                return self._config
            global_layer = dict(_GLOBAL_DEFAULT_CONFIG)
            user_layer = _load_json_file(self._resolve_user_config_path())
            project_layer = _load_json_file(self._resolve_project_config_path())
            merged = _merge_configs(global_layer, user_layer, project_layer)

            # env-level overrides from settings
            try:
                from agent.config import settings
                if os.environ.get("TUI_DEFAULT_EDITOR"):
                    merged["default_editor"] = settings.tui_default_editor
                if os.environ.get("TUI_ALLOW_ENVIRONMENT_EDITOR") is not None:
                    merged["allow_environment_editor"] = settings.tui_allow_environment_editor
            except Exception:
                pass

            try:
                self._config = _parse_config(merged)
            except TuiConfigValidationError:
                LOGGER.error("TUI tool config validation failed — falling back to global defaults", exc_info=True)
                self._config = _parse_config(dict(_GLOBAL_DEFAULT_CONFIG))
            return self._config

    def reload(self) -> TuiToolConfig:
        with self._lock:
            self._config = None
        return self.load()

    def get_config(self) -> TuiToolConfig:
        return self.load()

    def is_allowed_tool(self, command: str) -> bool:
        return str(command or "").strip() in self.load().allowed_tools

    def get_editor_profile(self, editor_id: str) -> EditorProfile | None:
        return self.load().editor_profiles.get(str(editor_id or "").strip())

    def get_tool_profile(self, tool_id: str) -> ToolProfile | None:
        return self.load().tool_profiles.get(str(tool_id or "").strip())

    def list_allowed_tools(self) -> list[str]:
        return list(self.load().allowed_tools)

    def list_tool_profiles(self) -> list[ToolProfile]:
        return list(self.load().tool_profiles.values())

    def list_filetype_rules(self) -> list[FiletypeRule]:
        return list(self.load().filetype_rules)


_registry: TuiToolRegistry | None = None
_registry_lock = threading.Lock()


def get_tui_tool_registry() -> TuiToolRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = TuiToolRegistry()
    return _registry
