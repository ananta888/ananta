"""Fail-closed feature and resource policy for Knowledge Hygiene."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class KnowledgeHygieneConfig:
    enabled: bool = False
    mode: str = "disabled"
    auto_run_enabled: bool = False
    source_writeback_enabled: bool = False
    require_dual_approval: bool = False
    max_claims_per_run: int = 10_000
    max_candidate_pairs: int = 50_000
    max_pages_per_run: int = 500
    max_patch_bytes: int = 1_000_000
    semantic_similarity_threshold: float = 0.92
    projection_dir: Path = Path("artifacts/domain/knowledge-hygiene/wiki")
    allowed_obsidian_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"disabled", "observe", "manual"}:
            raise ValueError("invalid_knowledge_hygiene_mode")
        for value in (
            self.max_claims_per_run,
            self.max_candidate_pairs,
            self.max_pages_per_run,
            self.max_patch_bytes,
        ):
            if int(value) <= 0:
                raise ValueError("invalid_knowledge_hygiene_limit")
        if not 0.0 < float(self.semantic_similarity_threshold) <= 1.0:
            raise ValueError("invalid_knowledge_hygiene_similarity_threshold")
        if self.source_writeback_enabled and (not self.enabled or self.mode != "manual"):
            raise ValueError("knowledge_hygiene_writeback_requires_manual_mode")

    @classmethod
    def from_mapping(cls, settings: Mapping[str, object] | None) -> "KnowledgeHygieneConfig":
        raw = dict((settings or {}).get("knowledge_hygiene") or {})
        roots = tuple(Path(str(item)).expanduser() for item in raw.get("allowed_obsidian_roots") or ())
        return cls(
            enabled=bool(raw.get("enabled", False)),
            mode=str(raw.get("mode") or "disabled"),
            auto_run_enabled=bool(raw.get("auto_run_enabled", False)),
            source_writeback_enabled=bool(raw.get("source_writeback_enabled", False)),
            require_dual_approval=bool(raw.get("require_dual_approval", False)),
            max_claims_per_run=int(raw.get("max_claims_per_run", 10_000)),
            max_candidate_pairs=int(raw.get("max_candidate_pairs", 50_000)),
            max_pages_per_run=int(raw.get("max_pages_per_run", 500)),
            max_patch_bytes=int(raw.get("max_patch_bytes", 1_000_000)),
            semantic_similarity_threshold=float(raw.get("semantic_similarity_threshold", 0.92)),
            projection_dir=Path(str(raw.get("projection_dir") or "artifacts/domain/knowledge-hygiene/wiki")),
            allowed_obsidian_roots=roots,
        )
