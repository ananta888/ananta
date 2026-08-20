"""Deterministic preselection over an already-authorized tool subset."""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

_TOKEN = re.compile(r"[a-z0-9_.-]+")


class AllowedToolPreselector:
    def select(
        self, prompt: str, tools: Sequence[Mapping[str, Any]], *, top_k: int,
    ) -> tuple[Mapping[str, Any], ...]:
        limit = max(1, int(top_k))
        prompt_terms = set(_TOKEN.findall(str(prompt or "").lower()))
        ranked: list[tuple[int, str, Mapping[str, Any]]] = []
        for item in tools:
            function = item.get("function") if isinstance(item, Mapping) else None
            if not isinstance(function, Mapping):
                continue
            name = str(function.get("name") or "")
            properties = (function.get("parameters") or {}).get("properties") or {}
            searchable = " ".join([
                name, str(function.get("description") or ""),
                " ".join(str(key) for key in properties),
            ]).lower()
            terms = set(_TOKEN.findall(searchable))
            name_fragments = set(_TOKEN.findall(name.replace(".", " ").lower()))
            score = len(prompt_terms & terms) * 10 + len(prompt_terms & name_fragments) * 20
            ranked.append((-score, name, item))
        ranked.sort(key=lambda row: (row[0], row[1]))
        return tuple(row[2] for row in ranked[:limit])
