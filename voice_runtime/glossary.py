from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class Glossary:
    replacements: dict[str, str]
    warnings: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: str | None) -> "Glossary":
        if not path:
            return cls(replacements={})
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            return cls(replacements={}, warnings=(f"glossary_unavailable:{exc}",))
        if not isinstance(raw, dict):
            return cls(replacements={}, warnings=("glossary_invalid:root_not_object",))
        replacements: dict[str, str] = {}
        terms = raw.get("terms")
        if isinstance(terms, dict):
            for canonical, aliases in terms.items():
                canonical_text = str(canonical).strip()
                if not canonical_text:
                    continue
                replacements[canonical_text.lower()] = canonical_text
                if isinstance(aliases, list):
                    for alias in aliases:
                        alias_text = str(alias).strip()
                        if alias_text:
                            replacements[alias_text.lower()] = canonical_text
        aliases = raw.get("aliases")
        if isinstance(aliases, dict):
            for alias, canonical in aliases.items():
                alias_text = str(alias).strip()
                canonical_text = str(canonical).strip()
                if alias_text and canonical_text:
                    replacements[alias_text.lower()] = canonical_text
        protected = raw.get("protected_terms")
        if isinstance(protected, list):
            for term in protected:
                term_text = str(term).strip()
                if term_text:
                    replacements[term_text.lower()] = term_text
        return cls(replacements=replacements)

    def apply(self, text: str) -> str:
        updated = text
        for alias, canonical in sorted(self.replacements.items(), key=lambda item: len(item[0]), reverse=True):
            updated = re.sub(rf"\b{re.escape(alias)}\b", canonical, updated, flags=re.IGNORECASE)
        return updated

    def as_stage_metadata(self) -> dict[str, Any]:
        return {"terms": len(self.replacements), "warnings": list(self.warnings)}
