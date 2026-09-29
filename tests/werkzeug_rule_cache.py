"""Reuse werkzeug's compiled URL builders across the app instances of a test process.

Every test that uses the app fixture calls ``create_app``, which binds ~1,450 URL rules; werkzeug compiles a
Python function per rule (twice: with and without appending unknown arguments) through ``ast`` and
``compile``. That is most of ``create_app``'s time. The generated function depends only on the rule's
parsed trace, the constants its defaults resolve to and ``append_unknown``: converters are reached through
the bound rule (``.self._converters``) at call time. This module caches the unbound function under exactly
those inputs, so an identical rule of the next app reuses it and is bound to its own rule as before.
``ANANTA_TEST_WERKZEUG_RULE_CACHE=0`` switches the cache off.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from werkzeug.routing.rules import Rule

_original_compile_builder = Rule._compile_builder
_cache: dict[tuple[Any, ...], Any] = {}
_lock = threading.Lock()


def _builder_key(rule: Rule, append_unknown: bool) -> tuple[Any, ...] | None:
    defaults = rule.defaults or {}
    try:
        resolved = tuple(
            (data, rule._converters[data].to_url(defaults[data]))
            for is_dynamic, data in rule._trace
            if is_dynamic and data in defaults
        )
        return (rule.rule, tuple(rule._trace), resolved, tuple(str(key) for key in defaults), bool(append_unknown))
    except Exception:  # unusual converters or defaults: compile as usual
        return None


def _cached_compile_builder(self: Rule, append_unknown: bool = True) -> Any:
    key = _builder_key(self, append_unknown)
    if key is None:
        return _original_compile_builder(self, append_unknown)
    with _lock:
        builder = _cache.get(key)
    if builder is None:
        builder = _original_compile_builder(self, append_unknown)
        with _lock:
            _cache[key] = builder
    return builder


def install() -> None:
    if os.environ.get("ANANTA_TEST_WERKZEUG_RULE_CACHE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    Rule._compile_builder = _cached_compile_builder  # type: ignore[method-assign]
