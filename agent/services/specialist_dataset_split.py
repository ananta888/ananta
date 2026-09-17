"""Leakage-safe train / validation / holdout split for specialists (GBMF-003).

* Splitting is group-wise: every example of one ``group_key`` (semantically
  near variants of the same task) lands in exactly one partition.
* The final holdout is frozen and hash-versioned before any training; the
  evolver and hyper-parameter search may only see train and validation.
* ``check_leakage`` is part of the admission gate for every training job.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ananta_contracts.specialist_decision import canonical_digest
from agent.services.specialist_training_examples import TrainingExample, dataset_digest

SPLIT_SCHEMA = "ananta.specialist-split-manifest.v1"
PARTITIONS = ("train", "validation", "holdout")


class SpecialistSplitError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class FrozenHoldout:
    """Immutable identity of the final benchmark set."""

    holdout_digest: str
    example_ids: tuple[str, ...]
    input_digests: frozenset[str]
    group_keys: frozenset[str]

    @classmethod
    def freeze(cls, examples: Iterable[TrainingExample]) -> "FrozenHoldout":
        items = sorted(examples, key=lambda example: example.example_id)
        if not items:
            raise SpecialistSplitError("specialist_holdout_empty")
        return cls(
            holdout_digest=canonical_digest([[e.example_id, e.input_digest, e.group_key] for e in items]),
            example_ids=tuple(e.example_id for e in items),
            input_digests=frozenset(e.input_digest for e in items),
            group_keys=frozenset(e.group_key for e in items),
        )


@dataclass(frozen=True)
class LeakageReport:
    exact_overlap: int
    input_overlap: int
    group_overlap: int

    @property
    def leaked(self) -> bool:
        return bool(self.exact_overlap or self.input_overlap or self.group_overlap)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "exact_overlap": self.exact_overlap,
            "input_overlap": self.input_overlap,
            "group_overlap": self.group_overlap,
            "leaked": self.leaked,
        }


def check_leakage(visible: Iterable[TrainingExample], holdout: FrozenHoldout) -> LeakageReport:
    """Overlap of anything training may see with the frozen holdout."""
    visible = list(visible)
    ids = frozenset(holdout.example_ids)
    return LeakageReport(
        exact_overlap=sum(1 for e in visible if e.example_id in ids),
        input_overlap=sum(1 for e in visible if e.example_id not in ids and e.input_digest in holdout.input_digests),
        group_overlap=sum(
            1
            for e in visible
            if e.example_id not in ids and e.input_digest not in holdout.input_digests and e.group_key in holdout.group_keys
        ),
    )


@dataclass(frozen=True)
class SplitManifest:
    dataset_digest: str
    seed: int
    validation_ratio: float
    holdout_ratio: float
    partitions: Mapping[str, tuple[str, ...]]
    holdout: FrozenHoldout
    group_count: int
    manifest_digest: str = field(default="")

    def __post_init__(self) -> None:
        if not self.manifest_digest:
            object.__setattr__(self, "manifest_digest", canonical_digest(self._identity()))

    def _identity(self) -> dict[str, Any]:
        return {
            "schema": SPLIT_SCHEMA,
            "dataset_digest": self.dataset_digest,
            "seed": self.seed,
            "validation_ratio": self.validation_ratio,
            "holdout_ratio": self.holdout_ratio,
            "partitions": {name: list(self.partitions[name]) for name in PARTITIONS},
            "holdout_digest": self.holdout.holdout_digest,
            "group_count": self.group_count,
        }

    def to_mapping(self) -> dict[str, Any]:
        return {**self._identity(), "manifest_digest": self.manifest_digest}


def _bucket(group_key: str, seed: int) -> float:
    digest = hashlib.sha256(f"specialist-split\0{seed}\0{group_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def split_examples(
    examples: Iterable[TrainingExample],
    *,
    seed: int,
    validation_ratio: float = 0.15,
    holdout_ratio: float = 0.2,
    frozen_holdout: FrozenHoldout | None = None,
) -> tuple[SplitManifest, dict[str, list[TrainingExample]]]:
    """Deterministic group-wise split; returns the manifest and the partitions.

    With ``frozen_holdout`` (a later dataset version) the holdout stays exactly
    the frozen set; new examples are only distributed over train/validation and
    anything overlapping the frozen holdout's inputs or groups is rejected.
    """
    items = sorted(examples, key=lambda example: example.example_id)
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise SpecialistSplitError("specialist_split_seed_invalid")
    for ratio in (validation_ratio, holdout_ratio):
        if not isinstance(ratio, (int, float)) or not 0.0 < float(ratio) < 1.0:
            raise SpecialistSplitError("specialist_split_ratio_invalid")
    if validation_ratio + holdout_ratio >= 1.0:
        raise SpecialistSplitError("specialist_split_ratio_invalid")
    partitions: dict[str, list[TrainingExample]] = {name: [] for name in PARTITIONS}
    if frozen_holdout is not None:
        frozen_ids = set(frozen_holdout.example_ids)
        partitions["holdout"] = [e for e in items if e.example_id in frozen_ids]
        if len(partitions["holdout"]) != len(frozen_ids):
            raise SpecialistSplitError("specialist_holdout_frozen_incomplete")
        remaining = [e for e in items if e.example_id not in frozen_ids]
        if check_leakage(remaining, frozen_holdout).leaked:
            raise SpecialistSplitError("specialist_split_leakage")
    else:
        remaining = items
    groups: dict[str, list[TrainingExample]] = {}
    for example in remaining:
        groups.setdefault(example.group_key, []).append(example)
    if len(groups) < 3:
        raise SpecialistSplitError("specialist_split_too_few_groups")
    # Rank groups by their seeded hash so every partition gets whole groups
    # and the smallest ratios still receive at least one group.
    ranked = sorted(groups, key=lambda key: _bucket(key, seed))
    holdout_count = 0 if frozen_holdout is not None else max(1, round(len(ranked) * holdout_ratio))
    validation_count = max(1, round(len(ranked) * validation_ratio))
    if holdout_count + validation_count >= len(ranked):
        raise SpecialistSplitError("specialist_split_too_few_groups")
    for position, key in enumerate(ranked):
        if position < holdout_count:
            name = "holdout"
        elif position < holdout_count + validation_count:
            name = "validation"
        else:
            name = "train"
        partitions[name].extend(groups[key])
    holdout = FrozenHoldout.freeze(partitions["holdout"])
    if frozen_holdout is not None and holdout.holdout_digest != frozen_holdout.holdout_digest:
        raise SpecialistSplitError("specialist_holdout_digest_mismatch")
    leakage = check_leakage(partitions["train"] + partitions["validation"], holdout)
    if leakage.leaked:  # defensive: group-wise split cannot leak, but never trust silently
        raise SpecialistSplitError("specialist_split_leakage")
    manifest = SplitManifest(
        dataset_digest=dataset_digest(items),
        seed=seed,
        validation_ratio=float(validation_ratio),
        holdout_ratio=float(holdout_ratio),
        partitions={name: tuple(e.example_id for e in partitions[name]) for name in PARTITIONS},
        holdout=holdout,
        group_count=len(groups) + (len(frozen_holdout.group_keys) if frozen_holdout is not None else 0),
    )
    return manifest, partitions


def admit_training_partitions(
    manifest: SplitManifest,
    partitions: Mapping[str, Iterable[TrainingExample]],
    *,
    expected_holdout_digest: str,
) -> dict[str, Any]:
    """Admission gate for a training job: frozen holdout, matching manifest, no leakage."""
    if manifest.holdout.holdout_digest != expected_holdout_digest:
        raise SpecialistSplitError("specialist_holdout_digest_mismatch")
    train = list(partitions.get("train") or ())
    validation = list(partitions.get("validation") or ())
    if not train:
        raise SpecialistSplitError("specialist_train_partition_empty")
    if tuple(e.example_id for e in train) != manifest.partitions["train"] or tuple(
        e.example_id for e in validation
    ) != manifest.partitions["validation"]:
        raise SpecialistSplitError("specialist_partition_manifest_mismatch")
    leakage = check_leakage(train + validation, manifest.holdout)
    if leakage.leaked:
        raise SpecialistSplitError("specialist_split_leakage")
    return {
        "admitted": True,
        "dataset_digest": manifest.dataset_digest,
        "holdout_digest": manifest.holdout.holdout_digest,
        "manifest_digest": manifest.manifest_digest,
        "leakage": leakage.to_mapping(),
        "train_examples": len(train),
        "validation_examples": len(validation),
    }
