from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import groupby

from ..glossary import Glossary


@dataclass(frozen=True)
class EditOperation:
    edit_id: str
    stage_index: int
    start: int
    end: int
    before: str
    after: str
    reason: str
    confidence: float
    processor: str

    def as_dict(self) -> dict[str, object]:
        return {
            "edit_id": self.edit_id,
            "stage_index": self.stage_index,
            "start": self.start,
            "end": self.end,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
            "confidence": self.confidence,
            "processor": self.processor,
        }


@dataclass(frozen=True)
class PostprocessResult:
    text: str
    warnings: tuple[str, ...] = ()
    changed: bool = False
    original_text: str = ""
    proposed_text: str | None = None
    edits: tuple[EditOperation, ...] = ()
    review_required: bool = False
    conflict_reason: str | None = None

    def reconstructed_text(self) -> str:
        return apply_edit_operations(self.original_text, self.edits)


class RuleBasedPostprocessor:
    """Conservative deterministic text edits with a reconstructable audit log."""

    def __init__(
        self,
        *,
        backend: str = "rules",
        glossary: Glossary | None = None,
        max_edit_ratio: float = 1.0,
    ) -> None:
        if not 0 <= max_edit_ratio <= 2:
            raise ValueError("max_edit_ratio must be in [0, 2]")
        self._backend = backend
        self._glossary = glossary or Glossary(replacements={})
        self._max_edit_ratio = float(max_edit_ratio)

    def name(self) -> str:
        return self._backend

    def process(self, text: str) -> PostprocessResult:
        original = str(text or "")
        current = original
        edits: list[EditOperation] = []
        transformations = (
            ("whitespace_normalization", 1.0, _normalize_whitespace),
            ("punctuation_spacing", 1.0, _normalize_punctuation_spacing),
            ("number_unit_spacing", 0.95, _normalize_number_unit_spacing),
            ("glossary_term", 0.95, self._glossary.apply),
            ("sentence_case_and_terminal_punctuation", 0.95, _normalize_sentence_boundaries),
        )
        for stage_index, (reason, confidence, transformation) in enumerate(transformations):
            updated = transformation(current)
            edits.extend(
                _diff_edits(
                    current,
                    updated,
                    stage_index=stage_index,
                    reason=reason,
                    confidence=confidence,
                    processor=self._backend,
                )
            )
            current = updated

        edit_ratio = _edit_distance(original, current) / max(1, len(original))
        if edit_ratio > self._max_edit_ratio:
            return PostprocessResult(
                text=original,
                warnings=(*self._glossary.warnings, "postprocess_review_required:edit_distance"),
                changed=False,
                original_text=original,
                proposed_text=current,
                edits=tuple(edits),
                review_required=True,
                conflict_reason="edit_distance_limit",
            )
        return PostprocessResult(
            text=current,
            warnings=self._glossary.warnings,
            changed=current != original,
            original_text=original,
            proposed_text=current,
            edits=tuple(edits),
        )


class LLMPostprocessor(RuleBasedPostprocessor):
    """Compatibility boundary; generated changes never bypass deterministic limits."""

    def __init__(self, *, glossary: Glossary | None = None, max_change_ratio: float = 0.35) -> None:
        super().__init__(backend="llm", glossary=glossary, max_edit_ratio=2.0)
        self._max_change_ratio = max(0.0, min(1.0, max_change_ratio))

    def process(self, text: str) -> PostprocessResult:
        candidate = super().process(text)
        original_len = max(1, len(text or ""))
        edit_ratio = _edit_distance(str(text or ""), candidate.text) / original_len
        if edit_ratio > self._max_change_ratio:
            return PostprocessResult(
                text=str(text or ""),
                warnings=(*candidate.warnings, "llm_postprocess_rejected:edit_distance"),
                changed=False,
                original_text=str(text or ""),
                proposed_text=candidate.text,
                edits=candidate.edits,
                review_required=True,
                conflict_reason="edit_distance_limit",
            )
        return candidate


def apply_edit_operations(original: str, edits: tuple[EditOperation, ...]) -> str:
    current = str(original)
    ordered = sorted(edits, key=lambda item: (item.stage_index, item.start, item.end, item.edit_id))
    for _stage_index, stage_items in groupby(ordered, key=lambda item: item.stage_index):
        stage = tuple(stage_items)
        for edit in stage:
            if current[edit.start : edit.end] != edit.before:
                raise ValueError("edit operation no longer matches its source span")
        for edit in reversed(stage):
            current = f"{current[: edit.start]}{edit.after}{current[edit.end :]}"
    return current


def build_postprocessor(backend: str, *, glossary: Glossary | None = None) -> RuleBasedPostprocessor | None:
    normalized = str(backend or "none").strip().lower()
    if normalized in {"", "none", "off", "disabled"}:
        return None
    if normalized in {"rules", "rule_based", "glossary"}:
        return RuleBasedPostprocessor(backend="rules", glossary=glossary)
    if normalized in {"llm", "llm_corrector"}:
        return LLMPostprocessor(glossary=glossary)
    raise ValueError(f"unsupported VOICE_POSTPROCESS_BACKEND: {normalized}")


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _normalize_punctuation_spacing(text: str) -> str:
    updated = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return re.sub(r"([,;:!?])(?=[^\s,.;:!?])", r"\1 ", updated)


def _normalize_number_unit_spacing(text: str) -> str:
    units = r"%|°C|°F|kg|mg|g|km|cm|mm|m|ms|s|h|kW|W|V|A"
    return re.sub(rf"(?<=\d)\s*(?=({units})\b)", " ", text, flags=re.IGNORECASE)


def _normalize_sentence_boundaries(text: str) -> str:
    if not text:
        return text
    characters = list(text)
    capitalize_next = True
    for index, character in enumerate(characters):
        if capitalize_next and character.isalpha():
            characters[index] = character.upper()
            capitalize_next = False
        elif character in ".!?":
            capitalize_next = True
        elif not character.isspace() and capitalize_next:
            capitalize_next = False
    updated = "".join(characters)
    return updated if not updated or updated[-1] in ".!?" else f"{updated}."


def _diff_edits(
    before: str,
    after: str,
    *,
    stage_index: int,
    reason: str,
    confidence: float,
    processor: str,
) -> tuple[EditOperation, ...]:
    edits: list[EditOperation] = []
    matcher = SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        payload = {
            "stage_index": stage_index,
            "start": before_start,
            "end": before_end,
            "before": before[before_start:before_end],
            "after": after[after_start:after_end],
            "reason": reason,
            "confidence": confidence,
            "processor": processor,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        edits.append(
            EditOperation(
                edit_id=f"edit-{hashlib.sha256(canonical).hexdigest()[:20]}",
                stage_index=stage_index,
                start=before_start,
                end=before_end,
                before=before[before_start:before_end],
                after=after[after_start:after_end],
                reason=reason,
                confidence=confidence,
                processor=processor,
            )
        )
    return tuple(edits)


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            substitution = previous[right_index - 1] + (left_character != right_character)
            current.append(min(previous[right_index] + 1, current[-1] + 1, substitution))
        previous = current
    return previous[-1]
