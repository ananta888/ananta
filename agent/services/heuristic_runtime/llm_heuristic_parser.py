"""LLM Heuristic Parser — parst und repariert LLM-Antworten zu DSL-Kandidaten."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LlmHeuristicParser:
    def parse(self, raw: str) -> dict[str, Any] | None:
        """Parst LLM-Antwort zu DSL-Dict. Gibt None bei ungültigem DSL."""
        if not raw or not raw.strip():
            return None

        text = raw.strip()

        # Strikte JSON: direkt parsen
        try:
            result = json.loads(text)
            return self._validate_structure(result)
        except json.JSONDecodeError:
            pass

        # Reparatur: Markdown-Fences entfernen
        m = _FENCE_RE.search(text)
        if m:
            try:
                result = json.loads(m.group(1).strip())
                result["_repaired"] = True
                return self._validate_structure(result)
            except json.JSONDecodeError:
                pass

        # Reparatur: JSON-Block am Anfang finden
        for start_char in ["{", "["]:
            idx = text.find(start_char)
            if idx >= 0:
                try:
                    result = json.loads(text[idx:])
                    result["_repaired"] = True
                    return self._validate_structure(result)
                except json.JSONDecodeError:
                    continue

        return None

    def _validate_structure(self, data: Any) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None
        # Mindeststruktur prüfen
        if data.get("dsl_version") != "2.0":
            return None
        if "action" not in data:
            return None
        if "safety" not in data:
            return None
        if "provenance" not in data:
            return None
        return data
