"""Small task, renderer and scorer interfaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from ananta_contracts.research_training import canonical_digest, require_digest, require_id


@dataclass(frozen=True, slots=True)
class ResearchTaskDefinition:
    task_id: str
    task_version: str
    task_kind: str
    group: str
    mandatory: bool
    dataset_digest: str
    source_refs: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchTaskDefinition:
        if set(value) != {
            "task_id",
            "task_version",
            "task_kind",
            "group",
            "mandatory",
            "dataset_digest",
            "source_refs",
        }:
            raise ValueError("research_task_definition_fields_invalid")
        kind = str(value.get("task_kind") or "").strip().lower()
        if kind not in {"exact_match", "multiple_choice", "numeric_tolerance", "code_tests"}:
            raise ValueError("research_task_kind_invalid")
        refs = value.get("source_refs")
        if (
            not isinstance(refs, Sequence)
            or isinstance(refs, (str, bytes))
            or not refs
            or len(refs) > 128
            or any(not str(item).startswith("SRC_") for item in refs)
        ):
            raise ValueError("research_task_source_refs_invalid")
        if not isinstance(value.get("mandatory"), bool):
            raise ValueError("research_task_mandatory_invalid")
        return cls(
            task_id=require_id(value.get("task_id"), "task_id"),
            task_version=require_id(value.get("task_version"), "task_version"),
            task_kind=kind,
            group=require_id(value.get("group"), "task_group"),
            mandatory=bool(value["mandatory"]),
            dataset_digest=require_digest(value.get("dataset_digest"), "task_dataset_digest"),
            source_refs=tuple(sorted({str(item) for item in refs})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "source_refs": list(self.source_refs)}

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class TaskRenderer(Protocol):
    def render(self, example: Mapping[str, Any]) -> str: ...


class TaskScorer(Protocol):
    def score(self, *, prediction: str, example: Mapping[str, Any]) -> float: ...


__all__ = ["ResearchTaskDefinition", "TaskRenderer", "TaskScorer"]
