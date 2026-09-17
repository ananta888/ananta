"""Curated retraining candidates from verified error cases (GBMF-010).

Production/benchmark failures never train a model directly. They enter a
candidate queue with their error class, are admitted through the same
redaction/deduplication/label-source gate as any example, are checked against
the frozen holdout (no contamination), and only then become the next dataset
version — which documents which error classes were added.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ananta_contracts.specialist_decision import canonical_digest
from agent.services.specialist_dataset_split import FrozenHoldout, check_leakage
from agent.services.specialist_training_examples import (
    ERROR_CLASSES,
    SpecialistExampleAdmission,
    TrainingExample,
    dataset_digest,
)

DATASET_VERSION_SCHEMA = "ananta.specialist-dataset-version.v1"
DEFAULT_PRIORITY = {
    "false_allow": 4,
    "false_deny": 3,
    "abstention_error": 2,
    "parser_error": 2,
    "schema_error": 2,
    "counterexample": 1,
    "none": 0,
}


class SpecialistRetrainingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RetrainingCandidate:
    raw: Mapping[str, Any]
    error_class: str
    verified: bool  # label re-verified by a reproducible source or reviewer
    weight: int = 1

    def __post_init__(self) -> None:
        if self.error_class not in ERROR_CLASSES:
            raise SpecialistRetrainingError("specialist_retraining_error_class_invalid")
        if type(self.weight) is not int or not 1 <= self.weight <= 16:
            raise SpecialistRetrainingError("specialist_retraining_weight_invalid")


@dataclass(frozen=True)
class DatasetVersion:
    specialist_id: str
    version: int
    dataset_digest: str
    example_ids: tuple[str, ...]
    parent_digest: str | None
    added_error_classes: Mapping[str, int]
    excluded: Mapping[str, int]
    changelog: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        body = {
            "schema": DATASET_VERSION_SCHEMA,
            "specialist_id": self.specialist_id,
            "version": self.version,
            "dataset_digest": self.dataset_digest,
            "examples": len(self.example_ids),
            "parent_digest": self.parent_digest,
            "added_error_classes": dict(self.added_error_classes),
            "excluded": dict(self.excluded),
            "changelog": list(self.changelog),
        }
        return {**body, "version_digest": canonical_digest(body)}


class RetrainingCandidateQueue:
    """Collects error cases; ``curate`` produces the next dataset version."""

    def __init__(self, admission: SpecialistExampleAdmission, *, priority: Mapping[str, int] | None = None) -> None:
        self._admission = admission
        self._priority = dict(DEFAULT_PRIORITY, **dict(priority or {}))
        self._queue: list[RetrainingCandidate] = []
        self._class_counts: dict[str, int] = {}

    def enqueue(self, candidate: RetrainingCandidate) -> None:
        self._queue.append(candidate)
        self._class_counts[candidate.error_class] = self._class_counts.get(candidate.error_class, 0) + 1

    def enqueue_errors(self, cases: Iterable[Mapping[str, Any]]) -> int:
        """Convenience for benchmark/gate mismatches: ``error_class`` drives priority."""
        count = 0
        for case in cases:
            self.enqueue(RetrainingCandidate(raw=case, error_class=str(case.get("error_class") or "counterexample"), verified=bool(case.get("verified", False))))
            count += 1
        return count

    def __len__(self) -> int:
        return len(self._queue)

    def priority_of(self, error_class: str) -> int:
        """Repeated errors of one class are prioritized higher (bounded)."""
        return self._priority.get(error_class, 0) + min(4, self._class_counts.get(error_class, 0) // 5)

    def curate(
        self,
        *,
        current: Iterable[TrainingExample],
        holdout: FrozenHoldout,
        version: int,
        max_new: int = 1_000,
    ) -> tuple[DatasetVersion, list[TrainingExample], list[TrainingExample]]:
        """Admit verified candidates into dataset ``version``; returns (version, examples, hard_negatives)."""
        current = list(current)
        excluded: dict[str, int] = {}
        ordered = sorted(self._queue, key=lambda c: (-self.priority_of(c.error_class), -c.weight))
        raws = []
        for candidate in ordered:
            if not candidate.verified:
                excluded["unverified_label"] = excluded.get("unverified_label", 0) + 1
                continue
            raws.append({**dict(candidate.raw), "error_class": candidate.error_class})
        report = self._admission.admit_batch(raws[:max_new], existing=current)
        for _index, reason in report.rejected:
            excluded[reason] = excluded.get(reason, 0) + 1
        for record in report.quarantined:
            excluded[record.reason_code] = excluded.get(record.reason_code, 0) + 1
        if report.duplicates:
            excluded["duplicate"] = len(report.duplicates)
        # Holdout contamination: anything overlapping the frozen benchmark is dropped.
        clean: list[TrainingExample] = []
        for example in report.admitted:
            if check_leakage([example], holdout).leaked:
                excluded["holdout_contamination"] = excluded.get("holdout_contamination", 0) + 1
                continue
            clean.append(example)
        added_classes: dict[str, int] = {}
        for example in clean:
            added_classes[example.error_class] = added_classes.get(example.error_class, 0) + 1
        hard_negatives = [example for example in clean if example.error_class in {"false_allow", "false_deny", "counterexample"}]
        examples = current + clean
        parent = dataset_digest(current) if current else None
        changelog = tuple(
            [f"v{version}: +{len(clean)} verified examples"]
            + [f"error_class {name}: +{count}" for name, count in sorted(added_classes.items())]
            + [f"excluded {name}: {count}" for name, count in sorted(excluded.items())]
        )
        self._queue = [c for c in self._queue if not c.verified]  # unverified stay queued for review
        return (
            DatasetVersion(
                specialist_id=self._admission.contract.specialist_id,
                version=version,
                dataset_digest=dataset_digest(examples),
                example_ids=tuple(sorted(e.example_id for e in examples)),
                parent_digest=parent,
                added_error_classes=added_classes,
                excluded=excluded,
                changelog=changelog,
            ),
            examples,
            hard_negatives,
        )
