"""Deterministic leakage clustering and immutable split-lock construction."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ananta_contracts.spreadsheet_studio import canonical_digest

_TOKEN = re.compile(r"[A-Za-z_]+|\d+(?:\.\d+)?")
_CELL_REFERENCE = re.compile(r"(?i)(?:'[^']+'|[A-Za-z_][\w .-]*)?!?\$?[A-Z]{1,4}\$?\d+")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


@dataclass(frozen=True, slots=True)
class SpreadsheetDatasetPreparation:
    rows: tuple[dict[str, Any], ...]
    split_lock: dict[str, Any]
    excluded_feedback_ids: tuple[str, ...]


class SpreadsheetDatasetSplitService:
    """Clusters lineage/template/formula/near-duplicates before assigning splits."""

    ALGORITHM_VERSION = "spreadsheet-connected-leakage-clusters.v1"
    NEAR_DUPLICATE_VERSION = "spreadsheet-simhash-lsh.v1"
    MAX_LSH_REPRESENTATIVES = 8
    MAX_HAMMING_DISTANCE = 3

    def prepare(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        seed: str,
        split_percent: Mapping[str, int],
    ) -> SpreadsheetDatasetPreparation:
        ordered, exclusions = self._deduplicate(rows)
        if not ordered:
            return SpreadsheetDatasetPreparation(
                rows=(),
                split_lock=self._split_lock((), {}, seed, split_percent, exclusions),
                excluded_feedback_ids=tuple(exclusions),
            )
        parent = list(range(len(ordered)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            a, b = find(left), find(right)
            if a != b:
                parent[max(a, b)] = min(a, b)

        fingerprints: list[dict[str, str | int]] = []
        exact_groups: dict[tuple[str, str], int] = {}
        lsh_buckets: dict[tuple[int, int], list[int]] = {}
        for index, row in enumerate(ordered):
            instruction_template = self._template_fingerprint(str(row.get("instruction") or ""))
            formula_family = self._formula_fingerprint(row)
            simhash = self._simhash(self._near_duplicate_text(row))
            fingerprints.append(
                {
                    "record_digest": str(row["record_digest"]),
                    "lineage": str(row["lineage_root_id"]),
                    "template": instruction_template,
                    "formula": formula_family,
                    "simhash": simhash,
                }
            )
            for kind, value in (
                ("lineage", str(row["lineage_root_id"])),
                ("template", instruction_template),
                ("formula", formula_family),
            ):
                if not value:
                    continue
                previous = exact_groups.setdefault((kind, value), index)
                union(index, previous)
            for band in range(4):
                key = (band, (simhash >> (band * 16)) & 0xFFFF)
                representatives = lsh_buckets.setdefault(key, [])
                for previous in representatives:
                    if (simhash ^ int(fingerprints[previous]["simhash"])).bit_count() <= self.MAX_HAMMING_DISTANCE:
                        union(index, previous)
                if len(representatives) < self.MAX_LSH_REPRESENTATIVES:
                    representatives.append(index)

        components: dict[int, list[int]] = {}
        for index in range(len(ordered)):
            components.setdefault(find(index), []).append(index)
        record_clusters: dict[str, str] = {}
        cluster_members: dict[str, list[str]] = {}
        for indexes in components.values():
            members = sorted(str(ordered[index]["record_digest"]) for index in indexes)
            cluster_id = canonical_digest(
                {
                    "algorithm_version": self.ALGORITHM_VERSION,
                    "record_digests": members,
                }
            )
            cluster_members[cluster_id] = members
            for member in members:
                record_clusters[member] = cluster_id
        assignments = {
            cluster_id: self._assign_split(cluster_id, seed, split_percent) for cluster_id in sorted(cluster_members)
        }
        prepared = []
        for row in ordered:
            value = dict(row)
            cluster_id = record_clusters[str(value["record_digest"])]
            value["split"] = assignments[cluster_id]
            prepared.append(value)
        prepared.sort(key=lambda row: (row["split"], record_clusters[row["record_digest"]], row["record_digest"]))
        return SpreadsheetDatasetPreparation(
            rows=tuple(prepared),
            split_lock=self._split_lock(
                prepared,
                cluster_members,
                seed,
                split_percent,
                exclusions,
                assignments=assignments,
                fingerprints=fingerprints,
            ),
            excluded_feedback_ids=tuple(exclusions),
        )

    @staticmethod
    def _deduplicate(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        unique: dict[str, dict[str, Any]] = {}
        exclusions: list[str] = []
        for raw in sorted(rows, key=lambda value: (str(value["record_digest"]), str(value["feedback_id"]))):
            row = dict(raw)
            digest = str(row["record_digest"])
            if digest in unique:
                exclusions.append(str(row["feedback_id"]))
            else:
                unique[digest] = row
        return list(unique.values()), sorted(exclusions)

    def _split_lock(
        self,
        rows: Sequence[Mapping[str, Any]],
        clusters: Mapping[str, Sequence[str]],
        seed: str,
        split_percent: Mapping[str, int],
        exclusions: Sequence[str],
        *,
        assignments: Mapping[str, str] | None = None,
        fingerprints: Sequence[Mapping[str, str | int]] = (),
    ) -> dict[str, Any]:
        cluster_assignments = dict(assignments or {})
        record_assignments = {str(row["record_digest"]): str(row["split"]) for row in rows}
        counts = {name: sum(value == name for value in record_assignments.values()) for name in self._split_names()}
        warnings = [
            f"spreadsheet_dataset_{name}_split_empty"
            for name in self._split_names()
            if int(split_percent[name]) > 0 and counts[name] == 0
        ]
        cluster_manifest = {
            cluster_id: sorted(str(value) for value in members) for cluster_id, members in sorted(clusters.items())
        }
        value = {
            "schema": "ananta.spreadsheet-dataset-split-lock.v1",
            "algorithm_version": self.ALGORITHM_VERSION,
            "near_duplicate_version": self.NEAR_DUPLICATE_VERSION,
            "split_seed": seed,
            "split_percent": dict(split_percent),
            "cluster_digest": canonical_digest(cluster_manifest),
            "fingerprint_digest": canonical_digest(list(fingerprints)),
            "cluster_assignments": cluster_assignments,
            "record_assignments": record_assignments,
            "excluded_feedback_ids": list(exclusions),
            "split_counts": counts,
            "distribution_warnings": warnings,
        }
        value["reproducibility_digest"] = canonical_digest(value)
        value["split_lock_digest"] = canonical_digest(value)
        return value

    @classmethod
    def _template_fingerprint(cls, instruction: str) -> str:
        normalized = " ".join(_NUMBER.sub("<NUMBER>", instruction.casefold()).split())
        return canonical_digest({"template": normalized}) if normalized else ""

    @classmethod
    def _formula_fingerprint(cls, row: Mapping[str, Any]) -> str:
        formulas: list[str] = []
        for field in ("input", "output"):
            try:
                value = json.loads(str(row.get(field) or ""))
            except ValueError:
                continue
            cls._collect_formulas(value, formulas)
        if not formulas:
            return ""
        normalized = sorted(_CELL_REFERENCE.sub("<CELL>", formula.upper()) for formula in formulas)
        return canonical_digest({"formulas": normalized})

    @classmethod
    def _collect_formulas(cls, value: Any, formulas: list[str]) -> None:
        if isinstance(value, str) and value.startswith("="):
            formulas.append(value)
        elif isinstance(value, Mapping):
            for child in value.values():
                cls._collect_formulas(child, formulas)
        elif isinstance(value, list):
            for child in value:
                cls._collect_formulas(child, formulas)

    @staticmethod
    def _near_duplicate_text(row: Mapping[str, Any]) -> str:
        return "\n".join(str(row.get(field) or "") for field in ("instruction", "input", "output")).casefold()

    @staticmethod
    def _simhash(value: str) -> int:
        weights = [0] * 64
        tokens = _TOKEN.findall(_NUMBER.sub("<NUMBER>", value))
        for token in tokens:
            digest = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
            for bit in range(64):
                weights[bit] += 1 if digest & (1 << bit) else -1
        result = 0
        for bit, weight in enumerate(weights):
            if weight >= 0:
                result |= 1 << bit
        return result

    @staticmethod
    def _assign_split(cluster_id: str, seed: str, split: Mapping[str, int]) -> str:
        point = int(hashlib.sha256(f"{seed}\0{cluster_id}".encode()).hexdigest()[:8], 16) % 100
        boundary = 0
        for name in SpreadsheetDatasetSplitService._split_names():
            boundary += int(split[name])
            if point < boundary:
                return name
        raise RuntimeError("spreadsheet_dataset_split_unreachable")

    @staticmethod
    def _split_names() -> tuple[str, ...]:
        return "train", "validation", "eval", "test"


__all__ = ["SpreadsheetDatasetPreparation", "SpreadsheetDatasetSplitService"]
