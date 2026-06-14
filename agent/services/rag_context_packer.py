"""Budget-based initial context packing for iterative RAG."""
from __future__ import annotations

from dataclasses import dataclass
import pathlib as _pl
from typing import Any


@dataclass(frozen=True)
class PackedRagFile:
    path: str
    score: float
    content: str
    chars_read: int
    chars_included: int
    inclusion: str
    truncated: bool


@dataclass(frozen=True)
class RagContextPack:
    included_files: list[PackedRagFile]
    candidate_files: list[dict[str, Any]]
    file_budget_chars: int
    used_file_chars: int
    context_budget_chars: int
    reserved_chars: int

    @property
    def included_paths(self) -> list[str]:
        return [item.path for item in self.included_files]


def _resolve_repo_file(repo_root: _pl.Path, source: str) -> _pl.Path | None:
    path = _pl.Path(source) if source.startswith("/") else repo_root / source
    if path.exists() and path.is_file():
        return path
    if source.startswith("/app/"):
        path = repo_root / source[5:]
        if path.exists() and path.is_file():
            return path
    return None


def build_rag_context_pack(
    *,
    chunks: list[dict[str, Any]],
    repo_root: _pl.Path,
    context_budget_chars: int,
    reserved_chars: int,
    max_chars_per_file: int,
    min_initial_files: int,
    max_initial_files: int,
) -> RagContextPack:
    """Pack top-ranked CodeCompass files into the initial prompt within a char budget.

    The packer owns only deterministic file selection and sizing. It does not call
    an LLM and does not make orchestration decisions.
    """
    file_budget = max(0, context_budget_chars - reserved_chars)
    min_initial_files = max(0, min_initial_files)
    max_initial_files = max(min_initial_files, max_initial_files)
    max_chars_per_file = max(1000, max_chars_per_file)

    included: list[PackedRagFile] = []
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    used = 0

    for ch in chunks:
        source = str(ch.get("source") or "").strip()
        if not source or source in seen:
            continue
        seen.add(source)

        score = float(ch.get("score") or 0.0)
        candidate_info = {"source": source, "score": score}
        path = _resolve_repo_file(repo_root, source)
        if path is None:
            candidates.append({**candidate_info, "reason": "not_found"})
            continue

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            candidates.append({**candidate_info, "reason": f"read_failed:{exc}"})
            continue

        remaining = file_budget - used
        must_try_minimum = len(included) < min_initial_files
        can_include_more = len(included) < max_initial_files
        if not can_include_more:
            candidates.append({**candidate_info, "reason": "max_initial_files"})
            continue
        if remaining < 1000 and not must_try_minimum:
            candidates.append({**candidate_info, "reason": "budget_exhausted"})
            continue

        if must_try_minimum:
            per_file_budget = max(1000, min(max_chars_per_file, max(remaining, 1000)))
        else:
            per_file_budget = min(max_chars_per_file, remaining)
        if per_file_budget < 1000:
            candidates.append({**candidate_info, "reason": "file_budget_too_small"})
            continue

        clipped = raw[:per_file_budget]
        truncated = len(raw) > len(clipped)
        if truncated:
            clipped += f"\n... [abgeschnitten nach {per_file_budget} Zeichen]"
        inclusion = "full" if not truncated else "partial"
        rel = str(path.relative_to(repo_root)) if path.is_relative_to(repo_root) else source
        item = PackedRagFile(
            path=rel,
            score=score,
            content=clipped,
            chars_read=len(raw),
            chars_included=len(clipped),
            inclusion=inclusion,
            truncated=truncated,
        )
        included.append(item)
        used += item.chars_included + len(item.path) + 64

    return RagContextPack(
        included_files=included,
        candidate_files=candidates,
        file_budget_chars=file_budget,
        used_file_chars=used,
        context_budget_chars=context_budget_chars,
        reserved_chars=reserved_chars,
    )


def format_packed_files_section(pack: RagContextPack) -> str:
    if not pack.included_files:
        return ""
    blocks: list[str] = ["=== Bereits gelesene CodeCompass-Top-Treffer ==="]
    for idx, item in enumerate(pack.included_files, 1):
        meta = (
            f"{idx}. {item.path} "
            f"(relevanz: {item.score:.1f}, {item.inclusion}, "
            f"{item.chars_included}/{item.chars_read} Zeichen)"
        )
        blocks.append(f"{meta}\n```\n{item.content}\n```")
    return "\n\n".join(blocks)
