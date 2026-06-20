"""RED/GREEN test: agent.common.sgpt_* shims must emit DeprecationWarning.

Per SGDEC-D3 the shims are temporary; the detector in scripts/check_cli_backend_shim_imports.py
will eventually report 0 importers and Welle 3 deletes them. Until then, every
``import agent.common.sgpt_*`` must emit a DeprecationWarning so downstream
consumers know to migrate.
"""
from __future__ import annotations

import importlib
import sys
import warnings


def _fresh_import(module_name: str):
    """Force a fresh import so DeprecationWarning fires (Python caches modules)."""
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


def test_sgpt_shim_emits_deprecation() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _fresh_import("agent.common.sgpt")

        assert any(issubclass(item.category, DeprecationWarning) for item in w), (
            f"sgpt shim must emit DeprecationWarning, got: {[item.category.__name__ for item in w]}"
        )


def test_sgpt_helpers_shim_emits_deprecation() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _fresh_import("agent.common.sgpt_helpers")

        assert any(issubclass(item.category, DeprecationWarning) for item in w)


def test_sgpt_backend_semaphore_shim_emits_deprecation() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _fresh_import("agent.common.sgpt_backend_semaphore")

        assert any(issubclass(item.category, DeprecationWarning) for item in w)


def test_sgpt_backend_routing_shim_emits_deprecation() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _fresh_import("agent.common.sgpt_backend_routing")

        assert any(issubclass(item.category, DeprecationWarning) for item in w)


def test_sgpt_tool_loop_shim_emits_deprecation() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _fresh_import("agent.common.sgpt_tool_loop")

        assert any(issubclass(item.category, DeprecationWarning) for item in w)


def test_sgpt_workspace_mutation_shim_emits_deprecation() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _fresh_import("agent.common.sgpt_workspace_mutation")

        assert any(issubclass(item.category, DeprecationWarning) for item in w)


def test_sgpt_opencode_shim_emits_deprecation() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _fresh_import("agent.common.sgpt_opencode")

        assert any(issubclass(item.category, DeprecationWarning) for item in w)


def test_sgpt_architecture_scan_shim_emits_deprecation() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _fresh_import("agent.common.sgpt_architecture_scan")

        assert any(issubclass(item.category, DeprecationWarning) for item in w)


def test_shims_still_re_export_public_api() -> None:
    """The shim must still expose the same public symbols (no behavior change)."""
    from agent.common.sgpt import run_sgpt_command
    from agent.common.sgpt_helpers import _get_agent_config
    from agent.common.sgpt_backend_semaphore import _acquire_backend_permit
    from agent.common.sgpt_backend_routing import SUPPORTED_CLI_BACKENDS
    from agent.common.sgpt_tool_loop import run_ananta_worker_tool_loop
    from agent.common.sgpt_workspace_mutation import run_ananta_worker_workspace_mutation
    from agent.common.sgpt_opencode import run_opencode_command
    from agent.common.sgpt_architecture_scan import _resolve_repo_root

    assert callable(run_sgpt_command)
    assert callable(_get_agent_config)
    assert callable(_acquire_backend_permit)
    assert isinstance(SUPPORTED_CLI_BACKENDS, (list, tuple, set))
    assert callable(run_ananta_worker_tool_loop)
    assert callable(run_ananta_worker_workspace_mutation)
    assert callable(run_opencode_command)
    assert callable(_resolve_repo_root)
