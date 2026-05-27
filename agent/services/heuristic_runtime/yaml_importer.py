"""HeuristicYamlImporter — import YAML authoring drafts to canonical candidate JSON.

YAML is authoring-only. This importer:
  - Parses .heuristic.yaml files from heuristics/authoring/
  - Normalizes to canonical JSON via HeuristicNormalizer
  - Writes candidate JSON to heuristics/candidates/
  - Always sets status=candidate (never active)
  - Computes and embeds content_hash
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer, NormalizeResult


@dataclass
class ImportResult:
    success: bool
    heuristic_id: str = ""
    candidate_path: str = ""
    content_hash: str = ""
    warnings: list[str] = field(default_factory=list)
    reason_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "heuristic_id": self.heuristic_id,
            "candidate_path": self.candidate_path,
            "content_hash": self.content_hash,
            "warnings": list(self.warnings),
            "reason_code": self.reason_code,
        }


class HeuristicYamlImporter:
    """Imports YAML authoring drafts into heuristics/candidates/ as canonical JSON."""

    def __init__(
        self,
        base_path: str | None = None,
        normalizer: HeuristicNormalizer | None = None,
    ) -> None:
        self._base_path = base_path or self._default_base_path()
        self._normalizer = normalizer or HeuristicNormalizer()

    @staticmethod
    def _default_base_path() -> str:
        here = os.path.dirname(__file__)
        return os.path.normpath(os.path.join(here, "..", "..", "..", "heuristics"))

    def import_file(self, yaml_path: str, *, dry_run: bool = False) -> ImportResult:
        """Read a .heuristic.yaml file and write normalized candidate JSON.

        Args:
            yaml_path: Absolute or relative path to the YAML file.
            dry_run: If True, validate and normalize but do not write to disk.
        """
        if not os.path.isfile(yaml_path):
            return ImportResult(success=False, reason_code=f"file_not_found:{yaml_path}")

        try:
            with open(yaml_path, encoding="utf-8") as f:
                yaml_text = f.read()
        except OSError as exc:
            return ImportResult(success=False, reason_code=f"read_error:{exc}")

        norm_result = self._normalizer.normalize_from_yaml(yaml_text)
        if not norm_result.success:
            return ImportResult(success=False, reason_code=norm_result.reason_code)

        normalized = norm_result.normalized
        assert normalized is not None
        heuristic_id = str(normalized.get("heuristic_id") or "")

        # YAML imports are always candidates — never active
        normalized["status"] = "candidate"

        if dry_run:
            return ImportResult(
                success=True,
                heuristic_id=heuristic_id,
                candidate_path="",
                content_hash=norm_result.content_hash,
                warnings=list(norm_result.warnings),
            )

        candidates_dir = os.path.join(self._base_path, "candidates")
        os.makedirs(candidates_dir, exist_ok=True)
        filename = f"{heuristic_id}.heuristic.json"
        candidate_path = os.path.join(candidates_dir, filename)

        with open(candidate_path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)

        return ImportResult(
            success=True,
            heuristic_id=heuristic_id,
            candidate_path=candidate_path,
            content_hash=norm_result.content_hash,
            warnings=list(norm_result.warnings),
        )

    def import_directory(self, yaml_dir: str | None = None) -> list[ImportResult]:
        """Import all .heuristic.yaml files from a directory (default: heuristics/authoring/)."""
        if yaml_dir is None:
            yaml_dir = os.path.join(self._base_path, "authoring")

        if not os.path.isdir(yaml_dir):
            return []

        results: list[ImportResult] = []
        for fname in sorted(os.listdir(yaml_dir)):
            if fname.endswith(".heuristic.yaml") or fname.endswith(".yaml"):
                results.append(self.import_file(os.path.join(yaml_dir, fname)))
        return results

    def import_text(self, yaml_text: str, *, heuristic_id_hint: str = "") -> ImportResult:
        """Normalize YAML text without reading from disk. Does not write to disk."""
        norm_result = self._normalizer.normalize_from_yaml(yaml_text)
        if not norm_result.success:
            return ImportResult(success=False, reason_code=norm_result.reason_code)

        normalized = norm_result.normalized
        assert normalized is not None
        heuristic_id = str(normalized.get("heuristic_id") or heuristic_id_hint)
        normalized["status"] = "candidate"

        return ImportResult(
            success=True,
            heuristic_id=heuristic_id,
            candidate_path="",
            content_hash=norm_result.content_hash,
            warnings=list(norm_result.warnings),
        )
