"""Context-block collection for the ananta-worker architecture scan.

Turns the ``repo_scope_refs`` / ``architecture_scope.refs`` entries of a
``research-context.json`` into scored source blocks. Each ref is resolved by
the first matching source strategy (line range, embedded chunks, file
beginning, bare snippet); the collector owns de-duplication so the strategies
stay independent (SRP) and new strategies can be appended without touching
the others (OCP).
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass
from typing import Callable

TRUNCATION_MARKER = "\n# [… gekürzt]"

EXT_LANG: dict[str, str] = {
    "py": "python", "ts": "typescript", "tsx": "typescript",
    "js": "javascript", "jsx": "javascript",
    "yaml": "yaml", "yml": "yaml", "json": "json",
    "md": "markdown", "html": "html", "css": "css",
    "sh": "bash", "bash": "bash",
}

# CCSH-004: Accepted alias names for line-range and snippet fields
LR_START_ALIASES: tuple[str, ...] = ("start_line", "line_start", "start", "from_line")
LR_END_ALIASES: tuple[str, ...] = ("end_line", "line_end", "end", "to_line")
SNIPPET_FIELD_ALIASES: tuple[str, ...] = ("snippet", "content", "excerpt")

MAX_LINE_SPAN: int = 5000
MAX_LINE_WINDOW: int = 200


def get_ref_alias(ref: dict, aliases: tuple[str, ...]) -> object:
    for k in aliases:
        v = ref.get(k)
        if v is not None:
            return v
    return None


def normalize_line_range(ref: dict) -> "tuple[int, int] | None":
    start = get_ref_alias(ref, LR_START_ALIASES)
    end = get_ref_alias(ref, LR_END_ALIASES)
    if start is None or end is None:
        return None
    try:
        s, e = int(start), int(end)
    except (TypeError, ValueError):
        return None
    if s < 1 or e < s or (e - s) > MAX_LINE_SPAN:
        return None
    return (s, e)


def read_line_window(
    full_path: pathlib.Path,
    start: int,
    end: int,
    context_lines: int,
    per_file_chars: int,
) -> "tuple[str, int, int]":
    """Read lines [start..end] + context_lines margin from file (1-indexed)."""
    try:
        raw = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", 0, 0
    lines = raw.splitlines()
    total = len(lines)
    if total == 0:
        return "", 0, 0
    context_lines = max(0, min(context_lines, MAX_LINE_WINDOW))
    lo = max(0, start - 1 - context_lines)
    hi = min(total, end + context_lines)
    excerpt = "\n".join(lines[lo:hi])
    if len(excerpt) > per_file_chars:
        excerpt = excerpt[:per_file_chars].rstrip() + TRUNCATION_MARKER
    return excerpt, lo + 1, min(hi, total)


def _lang_for_suffix(ext: str) -> str:
    return EXT_LANG.get(ext, ext or "text")


def _clip(text: str, limit: int) -> str:
    clipped = text[:limit]
    if len(text) > limit:
        clipped = clipped.rstrip() + TRUNCATION_MARKER
    return clipped


def _optional_int_pair(start: object, end: object) -> "tuple[int | None, int | None]":
    try:
        return (
            int(start) if start is not None else None,  # type: ignore[call-overload]
            int(end) if end is not None else None,  # type: ignore[call-overload]
        )
    except (TypeError, ValueError):
        return None, None


def dedup_key(rel: str, s: "int | None", e: "int | None", content: str = "") -> str:
    if s is None and e is None and content:
        suffix = hashlib.md5(content[:200].encode(), usedforsecurity=False).hexdigest()[:8]
        return f"{rel}:h:{suffix}"
    return f"{rel}:{s}:{e}"


@dataclass(frozen=True)
class BlockBudget:
    per_file_chars: int
    context_lines: int
    max_snippet_chars: int


@dataclass(frozen=True)
class ResolvedRef:
    """A research-context ref with its path resolved inside the repository root."""

    ref: dict
    rel_path: str
    full: "pathlib.Path | None"
    line_range: "tuple[int, int] | None"
    score: "float | None"
    reason: "str | None"
    symbol: "str | None"

    @classmethod
    def from_ref(cls, ref: dict, repo_root: pathlib.Path, resolved_root: pathlib.Path) -> "ResolvedRef":
        rel_path = str(ref.get("path") or "").strip()
        score_raw = ref.get("score")
        return cls(
            ref=ref,
            rel_path=rel_path,
            full=_resolve_ref_file(rel_path, repo_root, resolved_root),
            line_range=normalize_line_range(ref),
            score=float(score_raw) if score_raw is not None else None,
            reason=str(ref.get("reason") or "").strip() or None,
            symbol=str(ref.get("symbol") or "").strip() or None,
        )


def _resolve_ref_file(rel_path: str, repo_root: pathlib.Path, resolved_root: pathlib.Path) -> "pathlib.Path | None":
    if not rel_path:
        return None
    try:
        candidate = (repo_root / rel_path).resolve()
        candidate.relative_to(resolved_root)
        if candidate.is_file():
            return candidate
    except (ValueError, OSError):
        pass
    return None


class ContextBlockCollector:
    """Accumulates de-duplicated context blocks."""

    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self._seen_keys: set[str] = set()

    def claim(self, key: str) -> bool:
        """Reserve ``key``; False when a block with that key was already collected."""
        if key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        return True

    def add(
        self,
        resolved: ResolvedRef,
        *,
        rel_path: str,
        lang: str,
        content: str,
        source_kind: str,
        start_line: "int | None",
        end_line: "int | None",
        score: "float | None" = None,
    ) -> None:
        self.blocks.append({
            "rel_path": rel_path,
            "lang": lang,
            "content": content,
            "source_kind": source_kind,
            "start_line": start_line,
            "end_line": end_line,
            "score": resolved.score if score is None else score,
            "reason": resolved.reason,
            "symbol": resolved.symbol,
        })


# A source strategy returns True when it consumed the ref (later strategies are skipped).
SourceStrategy = Callable[[ResolvedRef, ContextBlockCollector, BlockBudget], bool]


def collect_line_range(resolved: ResolvedRef, collector: ContextBlockCollector, budget: BlockBudget) -> bool:
    """Priority 1: path + line-range → read window from current file."""
    if resolved.full is None or resolved.line_range is None:
        return False
    full = resolved.full
    content, actual_start, actual_end = read_line_window(
        full, resolved.line_range[0], resolved.line_range[1], budget.context_lines, budget.per_file_chars
    )
    if not content:
        return False
    if collector.claim(dedup_key(resolved.rel_path, actual_start, actual_end)):
        collector.add(
            resolved,
            rel_path=resolved.rel_path,
            lang=_lang_for_suffix(full.suffix.lstrip(".")),
            content=content,
            source_kind="line_range",
            start_line=actual_start,
            end_line=actual_end,
        )
    return True


def _collect_chunk(chunk: dict, resolved: ResolvedRef, collector: ContextBlockCollector, budget: BlockBudget) -> None:
    chunk_content = str(chunk.get("content") or chunk.get("excerpt") or "").strip()
    if not chunk_content:
        return
    chunk_source = str(chunk.get("source") or resolved.rel_path or "").strip()
    chunk_meta = dict(chunk.get("metadata") or {})
    c_start, c_end = _optional_int_pair(chunk_meta.get("start_line"), chunk_meta.get("end_line"))
    if not collector.claim(dedup_key(chunk_source, c_start, c_end, chunk_content)):
        return
    c_score_raw = chunk.get("score")
    collector.add(
        resolved,
        rel_path=chunk_source,
        lang=_lang_for_suffix(pathlib.Path(chunk_source).suffix.lstrip(".")),
        content=_clip(chunk_content, budget.per_file_chars),
        source_kind="chunk",
        start_line=c_start,
        end_line=c_end,
        score=float(c_score_raw) if c_score_raw is not None else resolved.score,
    )


def collect_embedded_chunks(resolved: ResolvedRef, collector: ContextBlockCollector, budget: BlockBudget) -> bool:
    """Priority 2: ref.chunks[] — use embedded chunk content."""
    ref_chunks = [dict(c or {}) for c in list(resolved.ref.get("chunks") or []) if c]
    if not ref_chunks:
        return False
    for chunk in ref_chunks:
        _collect_chunk(chunk, resolved, collector, budget)
    return True


def collect_file_beginning(resolved: ResolvedRef, collector: ContextBlockCollector, budget: BlockBudget) -> bool:
    """Priority 3: path only → file beginning (legacy fallback)."""
    if resolved.full is None:
        return False
    full = resolved.full
    try:
        raw = full.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        raw = ""
    if not raw:
        return False
    if collector.claim(dedup_key(resolved.rel_path, None, None)):
        collector.add(
            resolved,
            rel_path=resolved.rel_path,
            lang=_lang_for_suffix(full.suffix.lstrip(".")),
            content=_clip(raw, budget.per_file_chars),
            source_kind="file_excerpt",
            start_line=None,
            end_line=None,
        )
    return True


def collect_bare_snippet(resolved: ResolvedRef, collector: ContextBlockCollector, budget: BlockBudget) -> bool:
    """Priority 4: snippet without valid path."""
    snippet_raw = get_ref_alias(resolved.ref, SNIPPET_FIELD_ALIASES)
    if not snippet_raw:
        return False
    snippet_text = str(snippet_raw).strip()[: budget.max_snippet_chars]
    if not snippet_text:
        return False
    rel_path = resolved.rel_path
    s_start = resolved.line_range[0] if resolved.line_range else None
    s_end = resolved.line_range[1] if resolved.line_range else None
    if collector.claim(dedup_key(rel_path or "(snippet)", s_start, s_end)):
        ext = pathlib.Path(rel_path).suffix.lstrip(".") if rel_path else ""
        collector.add(
            resolved,
            rel_path=rel_path or "(codecompass_snippet)",
            lang=_lang_for_suffix(ext),
            content=snippet_text,
            source_kind="codecompass_snippet",
            start_line=s_start,
            end_line=s_end,
        )
    return True


DEFAULT_SOURCE_STRATEGIES: tuple[SourceStrategy, ...] = (
    collect_line_range,
    collect_embedded_chunks,
    collect_file_beginning,
    collect_bare_snippet,
)


def select_scope_refs(data: dict) -> list[dict]:
    """Pick architecture_scope refs for a full scan, repo_scope_refs otherwise."""
    profile = dict(data.get("retrieval_profile") or {})
    full_scan = str(profile.get("analysis_mode") or data.get("analysis_mode") or "").strip() == "architecture_full_scan"
    architecture_scope = dict(data.get("architecture_scope") or {})
    use_architecture_refs = full_scan and architecture_scope.get("refs")
    raw_refs = architecture_scope.get("refs") if use_architecture_refs else data.get("repo_scope_refs")
    return [dict(r or {}) for r in list(raw_refs or []) if r]


def collect_ref_blocks(
    refs: list[dict],
    repo_root: pathlib.Path,
    budget: BlockBudget,
    collector: ContextBlockCollector,
    strategies: tuple[SourceStrategy, ...] = DEFAULT_SOURCE_STRATEGIES,
) -> None:
    """Resolve every ref with the first strategy that consumes it."""
    resolved_root = repo_root.resolve()
    for ref in refs:
        resolved = ResolvedRef.from_ref(ref, repo_root, resolved_root)
        for strategy in strategies:
            if strategy(resolved, collector, budget):
                break


def hub_context_fallback_block(root: pathlib.Path) -> "dict | None":
    """Priority 5: hub-context.md fallback when nothing else loaded."""
    hub_path = root / ".ananta" / "hub-context.md"
    if not hub_path.exists():
        return None
    try:
        content = hub_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not content:
        return None
    return {
        "rel_path": "hub-context.md",
        "lang": "markdown",
        "content": content[:12_000],
        "source_kind": "hub_context",
        "start_line": None,
        "end_line": None,
        "score": None,
        "reason": None,
        "symbol": None,
    }
