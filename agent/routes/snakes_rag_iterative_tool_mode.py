"""Tool-call mode of the rag_iterative chat path.

Builds the initial research prompt (CodeCompass catalog overview,
architecture and symbol context, packed top files, ranked file list) and
hands it to the agentic tool loop in :mod:`.snakes_rag_tool_loop`.
"""
from __future__ import annotations

import pathlib as _pl
from dataclasses import dataclass
from typing import Any, Callable

from agent.services.codecompass_symbol_context_service import (
    build_codecompass_symbol_context,
    format_symbol_context_section,
)
from agent.services.rag_context_packer import (
    build_rag_context_pack,
    format_packed_files_section,
    packed_file_memory_summary,
)

_FALSEY = {"false", "0", "off", "no", ""}


def _configured(cfg: dict[str, Any], key: str, fallback: Any) -> Any:
    value = cfg.get(key)
    return value if value is not None else fallback


def _flag(cfg: dict[str, Any], key: str, fallback: bool) -> bool:
    value = cfg.get(key)
    if value is None:
        return bool(fallback)
    return str(value).lower() not in _FALSEY


def tool_calls_enabled(cfg: dict[str, Any], settings: Any) -> bool:
    return _flag(cfg, "rag_iterative_tool_calls_enabled", settings.rag_iterative_tool_calls_enabled)


@dataclass(frozen=True)
class ToolModeSettings:
    """Tool-loop budgets and initial-context sizes resolved from config and settings."""

    max_tool_calls: int
    max_search_calls: int
    tool_chars_per_file: int
    catalog_max_chars: int
    summarize_reads: bool
    summary_chars: int
    initial_min_files: int
    initial_max_files: int
    symbol_max_snippets: int
    symbol_max_lines: int

    @classmethod
    def resolve(
        cls,
        cfg: dict[str, Any],
        settings: Any,
        *,
        window_tokens: int,
        window_chars: Callable[..., int],
        max_tool_calls_override: int | None,
        max_search_calls_override: int | None,
    ) -> "ToolModeSettings":
        max_tool_calls = max(
            0, int(_configured(cfg, "rag_iterative_max_tool_calls", settings.rag_iterative_max_tool_calls))
        )
        if max_tool_calls_override is not None:
            max_tool_calls = max(0, int(max_tool_calls_override))
        max_search_calls = max(
            0, int(_configured(cfg, "rag_iterative_max_search_calls", settings.rag_iterative_max_search_calls))
        )
        if max_search_calls_override is not None:
            max_search_calls = max(0, int(max_search_calls_override))
        initial_min_files = max(0, min(5, int(_configured(
            cfg, "rag_iterative_initial_min_files", getattr(settings, "rag_iterative_initial_min_files", 3)
        ))))
        return cls(
            max_tool_calls=max_tool_calls,
            max_search_calls=max_search_calls,
            tool_chars_per_file=window_chars(
                _configured(cfg, "rag_iterative_tool_chars_per_file", settings.rag_iterative_tool_chars_per_file),
                window_tokens=window_tokens, share="snake_tool_file", legacy="snake_tool_file_chars", minimum=4000,
            ),
            catalog_max_chars=window_chars(
                _configured(
                    cfg, "rag_iterative_catalog_chars", getattr(settings, "rag_iterative_catalog_chars", 20000)
                ),
                window_tokens=window_tokens, share="snake_catalog", legacy="snake_catalog_chars", minimum=5000,
            ),
            summarize_reads=_flag(
                cfg, "rag_iterative_summarize_reads", getattr(settings, "rag_iterative_summarize_reads", False)
            ),
            summary_chars=max(200, min(2000, int(_configured(
                cfg, "rag_iterative_summary_chars", getattr(settings, "rag_iterative_summary_chars", 600)
            )))),
            initial_min_files=initial_min_files,
            initial_max_files=max(initial_min_files, min(16, int(_configured(
                cfg, "rag_iterative_initial_max_files", getattr(settings, "rag_iterative_initial_max_files", 8)
            )))),
            symbol_max_snippets=max(0, min(24, int(_configured(
                cfg,
                "rag_iterative_symbol_context_max_snippets",
                getattr(settings, "rag_iterative_symbol_context_max_snippets", 8),
            )))),
            symbol_max_lines=max(5, min(160, int(_configured(
                cfg,
                "rag_iterative_symbol_context_max_lines",
                getattr(settings, "rag_iterative_symbol_context_max_lines", 80),
            )))),
        )


def catalog_section(catalog_path: _pl.Path, chunks: list[dict[str, Any]], max_chars: int, filter_catalog) -> str:
    """Load the CodeCompass component catalog as a codebase overview."""
    if not catalog_path.exists():
        return ""
    catalog_text = filter_catalog(
        catalog_path.read_text(encoding="utf-8", errors="replace"), [ch["source"] for ch in chunks]
    )
    truncated = len(catalog_text) > max_chars
    return (
        "=== CodeCompass Codebase-Übersicht (relevante Module) ===\n"
        + catalog_text[:max_chars]
        + ("\n[... abgeschnitten nach {:,} Zeichen ...]\n".format(max_chars) if truncated else "\n")
    )


def symbol_context_refs(snippets, *, with_relation: bool) -> list[dict[str, Any]]:
    refs = []
    for item in snippets:
        ref = {
            "path": item.path,
            "symbol": item.symbol,
            "kind": item.kind,
            "line_start": item.line_start,
            "line_end": item.line_end,
            "source": item.source,
        }
        if with_relation:
            ref["relation"] = item.relation
        refs.append(ref)
    return refs


def initial_summary_mode(symbol_snippets, summarize_reads: bool, context_pack) -> str:
    if symbol_snippets:
        return "skipped_symbol_context_primary"
    return "enabled" if summarize_reads and context_pack.included_files else "not_applicable"


def _file_list_section(chunks: list[dict[str, Any]], packed_paths: set[str]) -> str:
    """Scored file list; files already packed into the prompt are marked as read."""
    lines = [
        "{:3d}. {}  (relevanz: {:.1f}{})".format(
            i,
            ch["source"],
            ch.get("score", 0),
            ", bereits im Initialkontext" if ch["source"] in packed_paths else "",
        )
        for i, ch in enumerate(chunks, 1)
    ]
    return "=== Verfügbare Dateien ({} gefunden, nach Relevanz) ===\n".format(len(chunks)) + "\n".join(lines)


@dataclass(frozen=True)
class ToolModePrompt:
    """The initial research prompt and the context it was built from."""

    user_message: str
    effective_question: str
    catalog_section: str
    architecture_section: str
    architecture_trace: Any
    symbol_snippets: list
    context_pack: Any


def build_tool_mode_prompt(
    *,
    question: str,
    retrieval_question: str,
    effective_question: str,
    budget_instruction: str,
    catalog: str,
    architecture_section: str,
    architecture_trace: Any,
    chunks: list[dict[str, Any]],
    repo_root: _pl.Path,
    reserved_chars: int,
    context_budget_chars: int,
    settings: ToolModeSettings,
    overview_question: bool,
) -> ToolModePrompt:
    symbol_snippets = build_codecompass_symbol_context(
        repo_root=repo_root,
        query=retrieval_question,
        ranked_sources=chunks,
        max_snippets=settings.symbol_max_snippets,
        max_lines_per_snippet=settings.symbol_max_lines,
    )
    symbol_section = format_symbol_context_section(symbol_snippets)
    skip_packing = bool(symbol_snippets) or overview_question
    context_pack = build_rag_context_pack(
        chunks=chunks,
        repo_root=repo_root,
        context_budget_chars=context_budget_chars,
        reserved_chars=reserved_chars + len(symbol_section),
        max_chars_per_file=settings.tool_chars_per_file,
        min_initial_files=0 if skip_packing else settings.initial_min_files,
        max_initial_files=0 if skip_packing else settings.initial_max_files,
    )
    packed_files_section = format_packed_files_section(context_pack)
    packed_paths = set(context_pack.included_paths)
    first_unread = next((ch["source"] for ch in chunks if ch["source"] not in packed_paths), None)
    first_read_hint = (
        "\nNaechster Schritt: Beginne mit read_file('{}') — lies diese Datei als erstes.".format(first_unread)
        if first_unread and not architecture_section else ""
    )
    symbol_instruction = (
        "1. Nutze den Architektur- und Symbol-Kontext als Einstieg. Verwende fuer offene "
        "Architekturfragen zuerst codecompass_architecture_overview oder "
        "codecompass_retrieve und expandiere relevante Handles gezielt.\n"
        if symbol_section else
        "1. Nutze zuerst codecompass_retrieve oder codecompass_architecture_overview; "
        "lies danach nur die fachlich relevanten Dateien aus der Dateiliste.\n"
    )
    user_message = (
        "Frage: {}\n\n".format(effective_question)
        + ("{}\n\n".format(budget_instruction) if budget_instruction else "")
        + catalog
        + "\n"
        + (architecture_section + "\n\n" if architecture_section else "")
        + (symbol_section + "\n\n" if symbol_section else "")
        + (packed_files_section + "\n\n" if packed_files_section else "")
        + _file_list_section(chunks, packed_paths)
        + "\n\n"
        "Anweisung:\n"
        + symbol_instruction
        + "2. Die als 'bereits im Initialkontext' markierten Top-Treffer gelten als gelesen.\n"
        "3. Nutze EXAKT die Pfade wie in der Dateiliste angegeben.\n"
        "4. Wenn eine Datei nicht gefunden wird: Nutze den im Fehler angezeigten korrekten Pfad, "
        "oder versuche die naechste Datei aus der Liste — gib NICHT auf.\n"
        "5. Nutze search_codebase() NUR fuer Begriffe die NICHT in der Dateiliste stehen.\n"
        "6. Jede Folgeaktion muss an den bisherigen Recherche-Stand anschliessen."
        + first_read_hint
    )
    return ToolModePrompt(
        user_message=user_message,
        effective_question=effective_question,
        catalog_section=catalog,
        architecture_section=architecture_section,
        architecture_trace=architecture_trace,
        symbol_snippets=symbol_snippets,
        context_pack=context_pack,
    )


def initial_evidence(context_pack, summary_chars: int) -> list[dict[str, Any]]:
    return [
        {
            "path": item.path,
            "summary": packed_file_memory_summary(item, max_chars=summary_chars),
            "content": item.content,
            "chars": item.chars_included,
            "score": item.score,
            "source": "initial_context",
        }
        for item in context_pack.included_files
    ]


def tool_loop_done_title(tool_loop_trace: dict[str, Any]) -> str:
    return "Tool-Loop abgeschlossen ({} Tool-Calls{})".format(
        tool_loop_trace.get("tool_calls_made", 0),
        ", {} textuell".format(tool_loop_trace["textual_tool_calls_detected"])
        if tool_loop_trace.get("textual_tool_calls_detected") else "",
    )
