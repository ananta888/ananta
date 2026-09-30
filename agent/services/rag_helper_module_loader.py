"""Isolated dynamic import of the rag-helper package modules."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any


def load_rag_helper_modules(rag_helper_root: Path) -> dict[str, Any]:
    helper_root = rag_helper_root.resolve()
    if not helper_root.exists():
        raise RuntimeError("rag_helper_not_found")
    helper_root_str = str(helper_root)
    # Temporarily add rag-helper to sys.path for dynamic imports, then remove it
    # to prevent the regular package at rag-helper/tests/ from shadowing the
    # tests/ namespace package used by the test suite.
    path_added = helper_root_str not in sys.path
    if path_added:
        sys.path.insert(0, helper_root_str)
    try:
        codecompass = importlib.import_module("codecompass_rag")
        processing_limits = importlib.import_module("rag_helper.application.processing_limits")
        project_processor = importlib.import_module("rag_helper.application.project_processor")
        file_filters = importlib.import_module("rag_helper.filesystem.file_filters")
        file_scanner = importlib.import_module("rag_helper.application.file_scanner")
        incremental_cache = importlib.import_module(
            "rag_helper.application.incremental_cache"
        )
    except Exception as exc:
        raise RuntimeError(f"rag_helper_import_failed:{exc}") from exc
    finally:
        if path_added and helper_root_str in sys.path:
            sys.path.remove(helper_root_str)
    return {
        "codecompass": codecompass,
        "ProcessingLimits": processing_limits.ProcessingLimits,
        "process_project": project_processor.process_project,
        "effective_extension": file_filters.effective_extension,
        "build_source_fingerprint": file_scanner.build_source_fingerprint,
        "invalidate_incremental_cache_paths": (
            incremental_cache.invalidate_incremental_cache_paths
        ),
    }
