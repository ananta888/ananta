from __future__ import annotations

from dataclasses import dataclass

from ..glossary import Glossary


@dataclass(frozen=True)
class PostprocessResult:
    text: str
    warnings: tuple[str, ...] = ()
    changed: bool = False


class RuleBasedPostprocessor:
    def __init__(self, *, backend: str = "rules", glossary: Glossary | None = None) -> None:
        self._backend = backend
        self._glossary = glossary or Glossary(replacements={})

    def name(self) -> str:
        return self._backend

    def process(self, text: str) -> PostprocessResult:
        original = text
        updated = " ".join(str(text or "").split())
        if updated:
            updated = updated[0].upper() + updated[1:]
            if updated[-1] not in ".!?":
                updated += "."
        updated = self._glossary.apply(updated)
        return PostprocessResult(text=updated, warnings=self._glossary.warnings, changed=updated != original)


class LLMPostprocessor(RuleBasedPostprocessor):
    def __init__(self, *, glossary: Glossary | None = None, max_change_ratio: float = 0.35) -> None:
        super().__init__(backend="llm", glossary=glossary)
        self._max_change_ratio = max(0.0, min(1.0, max_change_ratio))

    def process(self, text: str) -> PostprocessResult:
        candidate = super().process(text)
        original_len = max(1, len(text or ""))
        delta = abs(len(candidate.text) - len(text or "")) / original_len
        if delta > self._max_change_ratio:
            return PostprocessResult(text=text, warnings=(*candidate.warnings, "llm_postprocess_rejected:change_ratio"), changed=False)
        return candidate


def build_postprocessor(backend: str, *, glossary: Glossary | None = None) -> RuleBasedPostprocessor | None:
    normalized = str(backend or "none").strip().lower()
    if normalized in {"", "none", "off", "disabled"}:
        return None
    if normalized in {"rules", "rule_based", "glossary"}:
        return RuleBasedPostprocessor(backend="rules", glossary=glossary)
    if normalized in {"llm", "llm_corrector"}:
        return LLMPostprocessor(glossary=glossary)
    raise ValueError(f"unsupported VOICE_POSTPROCESS_BACKEND: {normalized}")
