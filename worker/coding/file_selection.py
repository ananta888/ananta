from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker.core.execution_profile import file_selection_limits_for_profile, normalize_execution_profile


@dataclass(frozen=True)
class FileSelectionLimits:
    max_files: int = 12
    max_bytes: int = 120000

    def validate(self) -> None:
        if int(self.max_files) <= 0:
            raise ValueError("max_files_must_be_positive")
        if int(self.max_bytes) <= 0:
            raise ValueError("max_bytes_must_be_positive")


def select_candidate_files(
    *,
    context_envelope: dict[str, Any],
    explicit_files: list[str] | None = None,
    limits: FileSelectionLimits | None = None,
    execution_profile: str | None = "balanced",
) -> dict[str, Any]:
    normalized_profile = normalize_execution_profile(execution_profile)
    bounded_limits = limits
    if bounded_limits is None:
        profile_limits = file_selection_limits_for_profile(normalized_profile)
        bounded_limits = FileSelectionLimits(
            max_files=int(profile_limits["max_files"]),
            max_bytes=int(profile_limits["max_bytes"]),
        )
    bounded_limits.validate()
    explicit = [str(item).strip() for item in list(explicit_files or []) if str(item).strip()]
    retrieval_refs = [item for item in list(context_envelope.get("retrieval_refs") or []) if isinstance(item, dict)]
    file_sizes = {
        str(path).strip(): int(size)
        for path, size in dict(context_envelope.get("file_sizes") or {}).items()
        if str(path).strip()
    }

    if not retrieval_refs:
        selected_explicit = explicit[: bounded_limits.max_files]
        return {
            "status": "degraded",
            "reason": "rag_unavailable_explicit_files_fallback" if selected_explicit else "rag_unavailable_no_candidates",
            "selected_files": [
                {
                    "path": path,
                    "symbol": "",
                    "reason": "explicit_user_file",
                    "source_provenance": {
                        "source_id": "explicit",
                        "retrieval_kind": "manual",
                        "score": 1.0,
                    },
                }
                for path in selected_explicit
            ],
            "usage_limits": {"max_files": bounded_limits.max_files, "max_bytes": bounded_limits.max_bytes},
            "execution_profile": normalized_profile,
        }

    weighted_refs = sorted(
        retrieval_refs,
        key=lambda item: float(item.get("score") or 0.0),
        reverse=True,
    )

    selected_files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    for ref in weighted_refs:
        path = str(ref.get("path") or "").strip()
        if not path or path in seen_paths:
            continue
        estimated_size = int(file_sizes.get(path) or int(ref.get("estimated_bytes") or 0) or 0)
        if estimated_size < 0:
            estimated_size = 0
        projected_bytes = total_bytes + estimated_size
        if len(selected_files) >= bounded_limits.max_files:
            break
        if projected_bytes > bounded_limits.max_bytes:
            continue
        selected_files.append(
            {
                "path": path,
                "symbol": str(ref.get("symbol") or ""),
                "reason": str(ref.get("reason") or "rag_ranked"),
                "source_provenance": {
                    "source_id": str(ref.get("source_id") or "unknown"),
                    "retrieval_kind": str(ref.get("retrieval_kind") or "codecompass_rag_helper"),
                    "score": float(ref.get("score") or 0.0),
                },
            }
        )
        seen_paths.add(path)
        total_bytes = projected_bytes

    if not selected_files and explicit:
        selected_files = [
            {
                "path": path,
                "symbol": "",
                "reason": "explicit_user_file",
                "source_provenance": {"source_id": "explicit", "retrieval_kind": "manual", "score": 1.0},
            }
            for path in explicit[: bounded_limits.max_files]
        ]
        return {
            "status": "degraded",
            "reason": "rag_candidates_exceeded_limits_explicit_files_fallback",
            "selected_files": selected_files,
            "usage": {"selected_file_count": len(selected_files), "selected_total_bytes": total_bytes},
            "usage_limits": {"max_files": bounded_limits.max_files, "max_bytes": bounded_limits.max_bytes},
            "execution_profile": normalized_profile,
        }

    return {
        "status": "ok" if selected_files else "degraded",
        "reason": "rag_selected_files" if selected_files else "rag_candidates_exceeded_limits",
        "selected_files": selected_files,
        "usage": {"selected_file_count": len(selected_files), "selected_total_bytes": total_bytes},
        "usage_limits": {"max_files": bounded_limits.max_files, "max_bytes": bounded_limits.max_bytes},
        "execution_profile": normalized_profile,
    }
