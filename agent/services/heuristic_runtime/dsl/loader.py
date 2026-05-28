"""DSL v2 Loader — lädt und validiert DSL aus HeuristicDefinition JSON."""
from __future__ import annotations

import json
import os
from typing import Any


_HEURISTICS_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "heuristics")
_ALLOWED_STATUSES = frozenset({"candidate", "active", "experimental_live"})


class DslLoadError(Exception):
    pass


class DslLoader:
    def load_from_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        """Lädt DSL aus HeuristicDefinition dict. Nur mode=dsl_v2 wird verarbeitet."""
        runtime = definition.get("runtime") or {}
        mode = runtime.get("mode")
        if mode != "dsl_v2":
            raise DslLoadError(f"mode={mode!r} ist nicht dsl_v2")

        status = definition.get("status", "candidate")
        if status not in _ALLOWED_STATUSES:
            raise DslLoadError(f"status={status!r} nicht erlaubt für DSL Laden")

        dsl_block = runtime.get("dsl_v2") or {}
        dsl = dsl_block.get("dsl")
        if not isinstance(dsl, dict):
            raise DslLoadError("runtime.dsl_v2.dsl fehlt oder ist kein Objekt")

        return dsl

    def load_from_file(self, path: str) -> dict[str, Any]:
        """Lädt HeuristicDefinition JSON und extrahiert DSL."""
        if not path.endswith(".json"):
            raise DslLoadError(f"Nur JSON erlaubt: {path}")
        with open(path, encoding="utf-8") as f:
            definition = json.load(f)
        return self.load_from_definition(definition)
