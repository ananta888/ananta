"""CCRDS-008: hard chunk filtering against a ResolvedDomainScope.

Every context chunk is checked against ``allowed_read_paths`` before the
prompt is built. Chunk sources come in three shapes:

  - repository_map / semantic_search: a repo-relative or absolute file path
  - agentic_search: the executed command line (``rg -n ... .``); the
    *content* lines carry the paths (``path:line:text`` or bare paths)

Chunks without a safely recognizable path are dropped in strict mode and
dropped-with-warning otherwise (CCRDS-008 acceptance). The filter never
mutates scores; it only keeps/drops and reports statistics.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.codecompass.domain_scope import (
    ResolvedDomainScope,
    is_path_within,
    normalize_repo_relative_path,
)

DROP_REASON_OUT_OF_SCOPE = "out_of_scope"
DROP_REASON_UNRESOLVABLE_SOURCE = "unresolvable_source"
DROP_REASON_AGENTIC_NO_SCOPED_LINES = "agentic_no_scoped_lines"

# Sources containing whitespace, glob or shell-ish characters are not safely
# recognizable file paths — strict mode drops them (CCRDS-008).
_IMPLAUSIBLE_SOURCE_CHARS = re.compile(r"[\s?*<>|;]")


@dataclass
class DomainScopeFilterStats:
    kept: int = 0
    dropped: int = 0
    dropped_reasons: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def note_drop(self, reason: str) -> None:
        self.dropped += 1
        self.dropped_reasons[reason] = self.dropped_reasons.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "kept": self.kept,
            "dropped": self.dropped,
            "dropped_reasons": dict(self.dropped_reasons),
            "warnings": list(self.warnings),
        }


def _line_path_candidate(line: str) -> str | None:
    """Extract the path part of an agentic output line, if any.

    ``rg -n`` lines look like ``path:42:match text``; ``rg --files`` and
    ``ls`` lines are bare paths. Lines that do not start with a plausible
    path return None.
    """
    text = line.strip()
    if not text:
        return None
    head = text.split(":", 1)[0].strip()
    if not head or " " in head:
        return None
    return head


def filter_agentic_content(
    content: str,
    scope: ResolvedDomainScope,
    *,
    repo_root: Path,
) -> tuple[str, int, int]:
    """Filter agentic output line-by-line; returns (filtered, kept, dropped)."""
    kept_lines: list[str] = []
    dropped = 0
    for line in content.splitlines():
        candidate = _line_path_candidate(line)
        normalized = normalize_repo_relative_path(candidate, repo_root=repo_root) if candidate else None
        if normalized is not None and is_path_within(normalized, scope.allowed_read_paths):
            kept_lines.append(line)
        else:
            dropped += 1
    return "\n".join(kept_lines), len(kept_lines), dropped


def filter_chunks(
    chunks: list[Any],
    scope: ResolvedDomainScope,
    *,
    repo_root: str | Path,
    drop_unresolvable_non_strict: bool = True,
) -> tuple[list[Any], DomainScopeFilterStats]:
    """Apply the read scope to a list of ContextChunks.

    Returns the kept chunks (agentic chunks may come back with filtered
    content) plus kept/dropped statistics for explainability (CCRDS-DD-005).
    """
    stats = DomainScopeFilterStats()
    if not scope.active:
        stats.kept = len(chunks)
        return list(chunks), stats

    root = Path(repo_root).resolve()
    kept: list[Any] = []
    external_budget = (
        max(0, int(scope.max_external_reference_chunks or 0))
        if scope.allow_external_references
        else 0
    )
    for chunk in chunks:
        engine = str(getattr(chunk, "engine", "") or "")
        source = str(getattr(chunk, "source", "") or "")

        if engine == "agentic_search":
            filtered_content, kept_lines, dropped_lines = filter_agentic_content(
                str(getattr(chunk, "content", "") or ""), scope, repo_root=root
            )
            if kept_lines:
                chunk.content = filtered_content
                if dropped_lines:
                    chunk.metadata = {
                        **dict(getattr(chunk, "metadata", {}) or {}),
                        "domain_scope_dropped_lines": str(dropped_lines),
                    }
                kept.append(chunk)
                stats.kept += 1
            else:
                stats.note_drop(DROP_REASON_AGENTIC_NO_SCOPED_LINES)
            continue

        normalized = (
            None
            if _IMPLAUSIBLE_SOURCE_CHARS.search(source)
            else normalize_repo_relative_path(source, repo_root=root)
        )
        if normalized is None:
            if scope.strict or drop_unresolvable_non_strict:
                stats.note_drop(DROP_REASON_UNRESOLVABLE_SOURCE)
                if not scope.strict:
                    stats.warnings.append(f"unresolvable_source_dropped:{source[:120]}")
            else:
                stats.warnings.append(f"unresolvable_source_kept:{source[:120]}")
                kept.append(chunk)
                stats.kept += 1
            continue

        if is_path_within(normalized, scope.allowed_read_paths):
            kept.append(chunk)
            stats.kept += 1
        elif external_budget > 0:
            # CCRDS-019: controlled relation expansion — a limited number of
            # out-of-scope chunks may stay, explicitly marked as external
            # reference so prompt/UI can flag them.
            external_budget -= 1
            chunk.metadata = {
                **dict(getattr(chunk, "metadata", {}) or {}),
                "domain_scope_external_reference": "true",
            }
            kept.append(chunk)
            stats.kept += 1
            stats.warnings.append(f"external_reference_kept:{normalized}")
        else:
            stats.note_drop(DROP_REASON_OUT_OF_SCOPE)
    return kept, stats


def build_no_match_guidance(
    scope: ResolvedDomainScope,
    stats: DomainScopeFilterStats | None = None,
) -> dict[str, Any]:
    """CCRDS-014: user-facing guidance when a scoped search finds nothing.

    Instead of silently falling back to global retrieval (forbidden in
    strict mode), the result tells the user what happened and offers
    concrete next steps the UI/ai-snake can render as suggestions.
    """
    domains = ", ".join(scope.selected_domain_ids) or "-"
    dropped = stats.dropped if stats else 0
    suggestions = [
        {
            "action": "switch_domain",
            "label": f"Andere Domain waehlen (aktiv: {domains})",
        },
        {
            "action": "broaden_scope",
            "label": "Scope um weitere Domains erweitern (selected_domain_ids)",
        },
        {
            "action": "disable_scope",
            "label": "Domain-Scope deaktivieren (chat_retrieval_domain_hint leeren)",
        },
    ]
    if dropped:
        suggestions.insert(0, {
            "action": "review_dropped",
            "label": f"{dropped} Treffer lagen ausserhalb der Domain — Scope pruefen",
        })
    return {
        "no_match_in_scope": True,
        "message": (
            f"Keine Treffer innerhalb der Domain(s) {domains}. "
            "Es wurde nicht global gesucht, weil ein Domain-Scope aktiv ist."
        ),
        "suggestions": suggestions,
    }


def build_scope_banner(scope: ResolvedDomainScope, stats: DomainScopeFilterStats | None = None) -> str:
    """Prompt banner so the LLM sees that a hard domain scope is active."""
    lines = [
        "[DOMAIN-SCOPE AKTIV]",
        f"Aktive Domain(s): {', '.join(scope.selected_domain_ids) or '-'}",
        f"Erlaubte Pfade: {', '.join(scope.allowed_read_paths) or '-'}",
        "Es wurde ausschliesslich Kontext aus diesen Pfaden bereitgestellt; "
        "Aussagen ueber andere Projektbereiche sind nicht durch Kontext gedeckt.",
    ]
    if stats is not None and stats.dropped:
        lines.append(f"Gefilterte Treffer ausserhalb der Domain: {stats.dropped}")
    return "\n".join(lines)
