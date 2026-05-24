from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.services.tui_tool_registry import TuiToolRegistry

LOGGER = logging.getLogger("agent.editor_resolver")

# Resolution reason codes
REASON_EXPLICIT = "explicit_open_with_argument"
REASON_PROJECT_FILETYPE = "project_filetype_rule"
REASON_USER_FILETYPE = "user_filetype_rule"
REASON_GLOBAL_FILETYPE = "global_filetype_rule"
REASON_ENVIRONMENT = "environment_EDITOR_or_VISUAL"
REASON_CONFIG_DEFAULT = "config_default_editor"
REASON_VIM_FALLBACK = "vim_fallback"


@dataclass(frozen=True)
class EditorResolution:
    editor_id: str
    command: str
    argv_template: list[str]
    readonly_supported: bool
    readonly_extra_args: list[str]
    reason: str

    def build_argv(self, file_path: str, *, readonly: bool = False) -> list[str]:
        """Build the final argv list with {file} substituted."""
        base = list(self.readonly_extra_args) if readonly and self.readonly_supported else []
        args = [arg.replace("{file}", file_path) for arg in self.argv_template]
        return [self.command] + base + args


def _match_filetype_rules(rules: list, filename: str) -> tuple[str, list[str]] | None:
    """Return (editor_id, args_template) for the first matching rule, or None."""
    for rule in rules:
        pattern = getattr(rule, "match", None) or rule.get("match", "")
        if fnmatch.fnmatch(filename, pattern):
            editor_id = getattr(rule, "editor_id", None) or rule.get("editor", "")
            args = list(getattr(rule, "args_template", None) or rule.get("args", ["{file}"]))
            return editor_id, args
    return None


def _make_resolution(
    editor_id: str,
    args_template: list[str],
    reason: str,
    registry: "TuiToolRegistry",
) -> EditorResolution:
    profile = registry.get_editor_profile(editor_id)
    if profile:
        return EditorResolution(
            editor_id=editor_id,
            command=profile.command,
            argv_template=args_template,
            readonly_supported=profile.readonly_supported,
            readonly_extra_args=list(profile.readonly_extra_args),
            reason=reason,
        )
    return EditorResolution(
        editor_id=editor_id,
        command=editor_id,
        argv_template=args_template,
        readonly_supported=False,
        readonly_extra_args=[],
        reason=reason,
    )


class EditorResolver:
    """Resolves the editor for a file path using a deterministic 7-step order.

    Resolution order:
      1. explicit_open_with_argument — caller passed --with <editor>
      2. project_config_filetype_rule — .ananta/tui-tools.json filetype match
      3. user_config_filetype_rule — ~/.config/ananta/tui-tools.json filetype match
      4. global_filetype_rule — hardcoded global defaults
      5. environment_EDITOR_or_VISUAL_if_allowed — $EDITOR/$VISUAL env vars
      6. global_default_editor — config default_editor field
      7. vim_fallback — always "vim"
    """

    def __init__(self, registry: "TuiToolRegistry | None" = None) -> None:
        self._registry = registry

    def _get_registry(self) -> "TuiToolRegistry":
        if self._registry is not None:
            return self._registry
        from agent.services.tui_tool_registry import get_tui_tool_registry
        return get_tui_tool_registry()

    def resolve(
        self,
        file_path: str,
        *,
        with_editor: str | None = None,
        project_rules: list | None = None,
        user_rules: list | None = None,
    ) -> EditorResolution:
        """Resolve editor for file_path.

        Args:
            file_path: Target file path (used for filetype matching by basename).
            with_editor: Explicit editor override from --with argument.
            project_rules: Filetype rules from project config scope (optional override for testing).
            user_rules: Filetype rules from user config scope (optional override for testing).
        """
        registry = self._get_registry()
        config = registry.load()
        filename = os.path.basename(str(file_path or ""))

        # Step 1: explicit override
        if with_editor:
            editor_id = str(with_editor).strip()
            if registry.is_allowed_tool(editor_id):
                return _make_resolution(editor_id, ["{file}"], REASON_EXPLICIT, registry)
            LOGGER.warning("Explicit editor %r is not in allowed_tools — ignoring", editor_id)

        # Step 2: project filetype rules
        p_rules = project_rules if project_rules is not None else []
        match = _match_filetype_rules(p_rules, filename)
        if match:
            return _make_resolution(match[0], match[1], REASON_PROJECT_FILETYPE, registry)

        # Step 3: user filetype rules
        u_rules = user_rules if user_rules is not None else []
        match = _match_filetype_rules(u_rules, filename)
        if match:
            return _make_resolution(match[0], match[1], REASON_USER_FILETYPE, registry)

        # Step 4: global (merged) filetype rules from registry
        match = _match_filetype_rules(config.filetype_rules, filename)
        if match:
            return _make_resolution(match[0], match[1], REASON_GLOBAL_FILETYPE, registry)

        # Step 5: $EDITOR / $VISUAL if allowed
        if config.allow_environment_editor:
            for env_var in ("EDITOR", "VISUAL"):
                env_editor = os.environ.get(env_var, "").strip()
                if env_editor and registry.is_allowed_tool(env_editor):
                    return _make_resolution(env_editor, ["{file}"], REASON_ENVIRONMENT, registry)

        # Step 6: configured default_editor
        default = config.default_editor
        if default and registry.is_allowed_tool(default):
            return _make_resolution(default, ["{file}"], REASON_CONFIG_DEFAULT, registry)

        # Step 7: vim fallback
        return EditorResolution(
            editor_id="vim",
            command="vim",
            argv_template=["{file}"],
            readonly_supported=True,
            readonly_extra_args=["-R"],
            reason=REASON_VIM_FALLBACK,
        )


_resolver: EditorResolver | None = None


def get_editor_resolver() -> EditorResolver:
    global _resolver
    if _resolver is None:
        _resolver = EditorResolver()
    return _resolver
