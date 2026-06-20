import logging
from typing import Any, Callable, Dict, List, Optional
import typing

logger = logging.getLogger(__name__)


class ToolResult:
    def __init__(self, success: bool, output: Any, error: Optional[str] = None):
        self.success = success
        self.output = output
        self.error = error

    def to_dict(self):
        return {"success": self.success, "output": self.output, "error": self.error}


_TOOL_ALIASES: Dict[str, str] = {
    "read_file": "file_read",
    "write_file": "file_write",
    "file_writer": "file_write",
    "list_files": "file_list",
    "create_file": "file_write",
    "edit_file": "file_patch",
    "patch_file": "file_patch",
    "run_command": "shell_execute",
    "execute_command": "shell_execute",
    "bash": "shell_execute",
    "context_reader": "file_read",
    "search_web": "web_search",
    "fetch_url": "web_fetch",
    # Gemma4/ananta-default hallucinated tool names
    "context_analysis_tool": "file_read",
    "text_analysis_tool": "file_read",
    "analysis_tool": "file_read",
    "search_tool": "web_search",
    "list_tool": "file_list",
    "read_tool": "file_read",
    "file_manager": "file_list",
    "workspace_reader": "file_read",
    "context_tool": "file_read",
    "info_tool": "file_read",
}


class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, description: str, parameters: Dict[str, Any]):
        def decorator(func: Callable):
            self.tools[name] = {"func": func, "description": description, "parameters": parameters}
            return func

        return decorator

    def _resolve(self, name: str) -> str:
        stripped = name.rsplit(".", 1)[-1] if "." in name else name
        return _TOOL_ALIASES.get(stripped, stripped)

    def get_tool_definitions(
        self, allowlist: Optional[typing.Iterable[str]] = None, denylist: Optional[typing.Iterable[str]] = None
    ) -> List[Dict[str, Any]]:
        defs = []
        allow_all = allowlist is not None and "*" in allowlist
        names = set(self.tools.keys()) | set(_TOOL_ALIASES.keys())
        for name in sorted(names):
            canonical = _TOOL_ALIASES.get(name, name)
            info = self.tools.get(canonical)
            if not info:
                continue
            if denylist and name in denylist:
                continue
            if allowlist is not None and not allow_all and name not in allowlist:
                continue
            defs.append({"name": name, "description": info["description"], "parameters": info["parameters"]})
        return defs

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        resolved = self._resolve(name)
        if resolved not in self.tools:
            return ToolResult(False, None, f"Tool '{name}' nicht gefunden.")

        try:
            result = self.tools[resolved]["func"](**args)
            return ToolResult(True, result)
        except Exception as e:
            logger.error(f"Fehler bei Ausführung von Tool '{name}': {e}")
            return ToolResult(False, None, str(e))


registry = ToolRegistry()
