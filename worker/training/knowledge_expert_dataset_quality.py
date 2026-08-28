"""Bounded and contamination-aware dataset materialization for one expert."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from worker.training.knowledge_unit_compiler import CompiledKnowledgeUnit

_SECRET = re.compile(r"(?i)(?:api[_-]?key|secret|password|private[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{8,}")
_INSTRUCTION = re.compile(r"(?i)(?:ignore\s+(?:all|previous)|system\s+prompt|developer\s+message|execute\s+tool)")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeAugmentationPort(Protocol):
    @property
    def model_digest(self) -> str: ...

    def augment(self, *, unit: Mapping[str, Any], source_text: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class KnowledgeExpertDataset:
    records: tuple[Mapping[str, str], ...]
    held_out_records: tuple[Mapping[str, str], ...]
    dataset_digest: str
    augmentation_model_digest: str
    rejection_counts: Mapping[str, int]

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": "ananta.knowledge-expert-dataset.v1",
            "dataset_digest": self.dataset_digest,
            "augmentation_model_digest": self.augmentation_model_digest,
            "train_record_count": len(self.records),
            "held_out_record_count": len(self.held_out_records),
            "rejection_counts": dict(self.rejection_counts),
        }


class KnowledgeExpertDatasetBuilder:
    def __init__(self, *, augmenter: KnowledgeAugmentationPort, max_source_chars: int = 16_000) -> None:
        if (
            isinstance(max_source_chars, bool)
            or not isinstance(max_source_chars, int)
            or not 256 <= max_source_chars <= 64_000
            or not _DIGEST.fullmatch(str(augmenter.model_digest or ""))
        ):
            raise ValueError("knowledge_expert_dataset_configuration_invalid")
        self._augmenter = augmenter
        self._max_source_chars = max_source_chars

    def build(self, units: Sequence[CompiledKnowledgeUnit]) -> KnowledgeExpertDataset:
        records: list[dict[str, str]] = []
        held_out: list[dict[str, str]] = []
        seen: set[str] = set()
        instruction_outputs: dict[str, str] = {}
        conflicting_instructions: set[str] = set()
        rejected: dict[str, int] = {}
        for index, compiled in enumerate(units):
            source = compiled.training_text[: self._max_source_chars]
            unsafe = self._unsafe_reason(source)
            if unsafe:
                rejected[unsafe] = rejected.get(unsafe, 0) + 1
                continue
            raw = self._augmenter.augment(unit=compiled.unit.to_dict(), source_text=source)
            if not isinstance(raw, Mapping) or set(raw).difference({"rewrite", "qa_pairs"}):
                rejected["augmentation_schema_invalid"] = rejected.get("augmentation_schema_invalid", 0) + 1
                continue
            candidates = self._records(compiled, raw)
            for candidate in candidates:
                instruction_key = f"{candidate['unit_id']}\0{candidate['instruction'].casefold()}"
                output_key = candidate["output"].casefold()
                if instruction_key in conflicting_instructions:
                    rejected["contradictory_sample"] = rejected.get("contradictory_sample", 0) + 1
                    continue
                prior_output = instruction_outputs.get(instruction_key)
                if prior_output is not None and prior_output != output_key:
                    conflicting_instructions.add(instruction_key)
                    instruction_outputs.pop(instruction_key, None)
                    records = [
                        item
                        for item in records
                        if f"{item['unit_id']}\0{item['instruction'].casefold()}" != instruction_key
                    ]
                    held_out = [
                        item
                        for item in held_out
                        if f"{item['unit_id']}\0{item['instruction'].casefold()}" != instruction_key
                    ]
                    rejected["contradictory_sample"] = rejected.get("contradictory_sample", 0) + 2
                    continue
                instruction_outputs[instruction_key] = output_key
                fingerprint = hashlib.sha256(
                    json.dumps(
                        {"instruction": candidate["instruction"], "output": candidate["output"]},
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest()
                if fingerprint in seen:
                    rejected["duplicate_sample"] = rejected.get("duplicate_sample", 0) + 1
                    continue
                seen.add(fingerprint)
                (held_out if index % 5 == 0 else records).append(candidate)
        if not records or not held_out:
            raise ValueError("knowledge_expert_dataset_split_insufficient")
        canonical = {"train": records, "held_out": held_out, "model": self._augmenter.model_digest}
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return KnowledgeExpertDataset(
            records=tuple(records),
            held_out_records=tuple(held_out),
            dataset_digest=digest,
            augmentation_model_digest=str(self._augmenter.model_digest),
            rejection_counts=rejected,
        )

    def _records(self, compiled: CompiledKnowledgeUnit, raw: Mapping[str, Any]) -> list[dict[str, str]]:
        unit_id = compiled.unit.unit_id
        result: list[dict[str, str]] = []
        rewrite = self._clean(raw.get("rewrite"))
        if rewrite:
            result.append({"unit_id": unit_id, "instruction": "Explain the approved knowledge.", "output": rewrite})
        pairs = raw.get("qa_pairs")
        if isinstance(pairs, list):
            for item in pairs[:8]:
                if not isinstance(item, Mapping) or set(item).difference({"question", "answer"}):
                    continue
                question = self._clean(item.get("question"), maximum=1_000)
                answer = self._clean(item.get("answer"), maximum=4_000)
                if question and answer and question.casefold() != answer.casefold():
                    result.append({"unit_id": unit_id, "instruction": question, "output": answer})
        return result

    def _clean(self, value: Any, *, maximum: int = 4_000) -> str:
        text = " ".join(str(value or "").split())[:maximum]
        return "" if self._unsafe_reason(text) else text

    @staticmethod
    def _unsafe_reason(text: str) -> str:
        if _SECRET.search(text):
            return "secret_detected"
        if _INSTRUCTION.search(text):
            return "prompt_injection_detected"
        return ""


__all__ = ["KnowledgeAugmentationPort", "KnowledgeExpertDataset", "KnowledgeExpertDatasetBuilder"]
