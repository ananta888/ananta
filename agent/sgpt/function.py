import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

from pydantic import BaseModel

from .config import cfg


try:
    from agent.tools import registry as ananta_registry
except ImportError:
    ananta_registry = None


class Function:
    def __init__(self, path: str):
        module = self._read(path)
        self._function = module.Function.execute
        self._openai_schema = module.Function.openai_schema()
        self._name = self._openai_schema["function"]["name"]

    @property
    def name(self) -> str:
        return self._name  # type: ignore

    @property
    def openai_schema(self) -> dict[str, Any]:
        return self._openai_schema  # type: ignore

    @property
    def execute(self) -> Callable[..., str]:
        return self._function  # type: ignore

    @classmethod
    def _read(cls, path: str) -> Any:
        module_name = path.replace("/", ".").rstrip(".py")
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)  # type: ignore
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore

        if not issubclass(module.Function, BaseModel):
            raise TypeError(f"Function {module_name} must be a subclass of pydantic.BaseModel")
        if not hasattr(module.Function, "execute"):
            raise TypeError(f"Function {module_name} must have an 'execute' classmethod")
        if not hasattr(module.Function, "openai_schema"):
            raise TypeError(f"Function {module_name} must have an 'openai_schema' classmethod")

        return module


functions_folder = Path(cfg.get("OPENAI_FUNCTIONS_PATH"))
functions_folder.mkdir(parents=True, exist_ok=True)
functions = [Function(str(path)) for path in functions_folder.glob("*.py")]


def get_function(name: str) -> Callable[..., Any]:
    # Try SGPT functions first
    for function in functions:
        if function.name == name:
            return function.execute

    # Fallback to Ananta Tool Registry
    if ananta_registry:

        def ananta_tool_wrapper(**kwargs):
            result = ananta_registry.execute(name, kwargs)
            if result.success:
                return str(result.output)
            return f"Error: {result.error}"

        # Check if tool exists in registry
        if any(t["function"]["name"] == name for t in ananta_registry.get_tool_definitions()):
            return ananta_tool_wrapper

    raise ValueError(f"Function {name} not found")


def get_openai_schemas() -> List[Dict[str, Any]]:
    schemas = [function.openai_schema for function in functions]
    if ananta_registry:
        # Add tools from Ananta registry, avoid duplicates
        sgpt_names = {s["function"]["name"] for s in schemas}
        for tool in ananta_registry.get_tool_definitions():
            if tool["function"]["name"] not in sgpt_names:
                schemas.append(tool)
    return schemas
