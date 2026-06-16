import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, cast

from pydantic import BaseModel

from .config import cfg

if TYPE_CHECKING:
    from agent.tools import ToolRegistry

log = logging.getLogger(__name__)


def _get_ananta_registry() -> Optional["ToolRegistry"]:
    try:
        from agent.tools import registry

        return registry
    except ImportError:
        return None


ananta_registry: Optional["ToolRegistry"] = _get_ananta_registry()


def _tool_name_from_entry(tool: Dict[str, Any]) -> str:
    """UTCR-003: normalise both wrapped and bare tool-definition formats.

    Legacy ``ToolRegistry.get_tool_definitions()`` returns bare
    ``{name, description, parameters}`` dicts. OpenAI-wrapped tools from
    ``AnantaToolRegistryService.describe_for_openai_tools()`` return
    ``{type, function: {name, ...}}``.  Both are handled here so callers
    need not care about the format.
    """
    if "function" in tool and isinstance(tool["function"], dict):
        return str(tool["function"].get("name") or "")
    return str(tool.get("name") or "")


def _ensure_openai_wrapped(tool: Dict[str, Any]) -> Dict[str, Any]:
    """UTCR-003: guarantee the OpenAI ``type/function`` envelope is present."""
    if "function" in tool and isinstance(tool["function"], dict):
        return tool
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
        },
    }


class Function:
    _name: str
    _openai_schema: Dict[str, Any]
    _function: Callable[..., str]

    def __init__(self, path: str):
        module = self._read(path)
        self._function = module.Function.execute
        self._openai_schema = module.Function.openai_schema()
        self._name = cast(str, self._openai_schema["function"]["name"])

    @property
    def name(self) -> str:
        return self._name

    @property
    def openai_schema(self) -> Dict[str, Any]:
        return self._openai_schema

    @property
    def execute(self) -> Callable[..., str]:
        return self._function

    @classmethod
    def _read(cls, path: str) -> Any:
        module_name = path.replace("/", ".").rstrip(".py")
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        if not issubclass(module.Function, BaseModel):
            raise TypeError(f"Function {module_name} must be a subclass of pydantic.BaseModel")
        if not hasattr(module.Function, "execute"):
            raise TypeError(f"Function {module_name} must have an 'execute' classmethod")
        if not hasattr(module.Function, "openai_schema"):
            raise TypeError(f"Function {module_name} must have an 'openai_schema' classmethod")

        return module


functions_folder = Path(cfg.get("OPENAI_FUNCTIONS_PATH"))
functions_folder.mkdir(parents=True, exist_ok=True)
functions: List[Function] = []
for _path in functions_folder.glob("*.py"):
    try:
        functions.append(Function(str(_path)))
    except Exception as _exc:  # UTCR-003: skip bad legacy functions
        log.warning("sgpt function skipped (bad schema): %s — %s", _path, _exc)


# ---------------------------------------------------------------------------
# UTCR-004: AnantaToolRegistryService-backed native schema loader
# ---------------------------------------------------------------------------

def get_ananta_worker_native_schemas(allowed_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """UTCR-004: Return OpenAI-tool schemas from AnantaToolRegistryService.

    Called when ``sgpt_native_tools.source == "ananta_tool_registry_service"``.
    """
    from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

    return get_ananta_tool_registry_service().describe_for_openai_tools(allowed_tools)


def _sgpt_native_tools_cfg() -> Dict[str, Any]:
    """Lazy config read — avoids import-time Flask context requirement."""
    try:
        from agent.common.sgpt_helpers import _get_agent_config

        raw = _get_agent_config().get("sgpt_native_tools") or {}
        return dict(raw) if isinstance(raw, dict) else {}
    except Exception:
        return {}


def get_function(name: str) -> Callable[..., Any]:
    # Try SGPT functions first
    for function in functions:
        if function.name == name:
            return function.execute

    # UTCR-004: try UnifiedToolExecutionService for ananta-registry tools
    ananta_func = get_ananta_function_executor(name)
    if ananta_func is not None:
        return ananta_func

    # Legacy fallback to ToolRegistry
    native_cfg = _sgpt_native_tools_cfg()
    legacy_enabled = native_cfg.get("legacy_agent_tools_enabled", True)
    if legacy_enabled and ananta_registry is not None:
        reg = cast("ToolRegistry", ananta_registry)

        def ananta_tool_wrapper(**kwargs: Any) -> str:
            result = reg.execute(name, kwargs)
            if result.success:
                return str(result.output)
            return f"Error: {result.error}"

        # UTCR-003: handle both bare and wrapped tool definitions
        if any(_tool_name_from_entry(t) == name for t in reg.get_tool_definitions()):
            return ananta_tool_wrapper

    raise ValueError(f"Function {name} not found")


def get_ananta_function_executor(name: str) -> Optional[Callable[..., Any]]:
    """UTCR-004: Return an executor wrapping UnifiedToolExecutionService,
    or None if the tool is not in the ananta registry.
    """
    try:
        from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service
        from agent.services.unified_tool_execution_service import get_unified_tool_execution_service

        registry_svc = get_ananta_tool_registry_service()
        if registry_svc.get_tool(name) is None:
            return None

        utes = get_unified_tool_execution_service()

        def _executor(**kwargs: Any) -> str:
            result = utes.execute(tool_name=name, arguments=kwargs)
            import json

            status = result.get("status", "")
            if status in ("allow", "ok", "success"):
                evidence = result.get("evidence") or []
                if evidence:
                    return json.dumps(evidence, ensure_ascii=False)
                data = result.get("data")
                if data:
                    return json.dumps(data, ensure_ascii=False)
                return json.dumps(result, ensure_ascii=False)
            return f"Error: {result.get('error') or status}"

        return _executor
    except Exception:
        return None


def get_openai_schemas() -> List[Dict[str, Any]]:
    schemas = [function.openai_schema for function in functions]
    sgpt_names = {s["function"]["name"] for s in schemas}

    native_cfg = _sgpt_native_tools_cfg()
    source = native_cfg.get("source", "")
    legacy_enabled = native_cfg.get("legacy_agent_tools_enabled", True)

    if source == "ananta_tool_registry_service":
        # UTCR-004: delegate entirely to AnantaToolRegistryService
        for tool in get_ananta_worker_native_schemas():
            tool_name = _tool_name_from_entry(tool)
            if tool_name and tool_name not in sgpt_names:
                sgpt_names.add(tool_name)
                schemas.append(tool)
    elif legacy_enabled and ananta_registry is not None:
        # UTCR-003: legacy path — normalise bare dicts to OpenAI wrapper
        reg = cast("ToolRegistry", ananta_registry)
        for tool in reg.get_tool_definitions():
            tool_name = _tool_name_from_entry(tool)
            if tool_name and tool_name not in sgpt_names:
                sgpt_names.add(tool_name)
                schemas.append(_ensure_openai_wrapped(tool))

    return schemas
