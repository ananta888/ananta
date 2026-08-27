from __future__ import annotations

import time

from agent.llm_integration import _list_lmstudio_candidates, _load_lmstudio_history, resolve_preferred_local_runtime
from agent.services.hub_llm_service import generate_text as generate_text
from agent.tool_guardrails import evaluate_tool_call_guardrails

from . import benchmarks, llm_generate, shared
from .benchmarks import benchmarks_bp
from .cognitive_styles import cognitive_styles_bp
from .llm_generate import (
    _infer_tool_calls_from_prompt as _infer_tool_calls_from_prompt,
)
from .llm_generate import llm_generate_bp
from .local_model_runtime import local_model_runtime_bp
from .providers import providers_bp
from .read_models import read_models_bp
from .settings import get_config as get_config
from .settings import set_config as set_config
from .settings import settings_bp
from .settings import unwrap_config as unwrap_config
from .templates import templates_bp
from .templates import validate_template_variables as validate_template_variables

_LMSTUDIO_CATALOG_CACHE = shared._LMSTUDIO_CATALOG_CACHE
_LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES = shared._LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES
config_bp = settings_bp


def _sync_patchable_exports() -> None:
    shared._LMSTUDIO_CATALOG_CACHE = _LMSTUDIO_CATALOG_CACHE
    shared._LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES = _LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES
    shared._list_lmstudio_candidates = _list_lmstudio_candidates
    shared.time = time
    benchmarks._load_lmstudio_history = _load_lmstudio_history
    llm_generate.evaluate_tool_call_guardrails = evaluate_tool_call_guardrails
    llm_generate.resolve_preferred_local_runtime = resolve_preferred_local_runtime


def register_config_blueprints(app):
    _sync_patchable_exports()

    @app.before_request
    def _refresh_config_route_exports():
        # Refresh compatibility injection seams without replacing the dynamic
        # hub-LLM facade used by llm_generate.
        _sync_patchable_exports()

    for blueprint in (
        settings_bp,
        read_models_bp,
        providers_bp,
        local_model_runtime_bp,
        cognitive_styles_bp,
        benchmarks_bp,
        templates_bp,
        llm_generate_bp,
    ):
        app.register_blueprint(blueprint)
