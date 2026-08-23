#!/usr/bin/env python3
"""Index the Ananta project codebase into the Knowledge/CodeCompass system.

Usage:
    python scripts/setup_codecompass_index.py
    python scripts/setup_codecompass_index.py --hub http://localhost:5000 --user admin --password test123
    python scripts/setup_codecompass_index.py --dry-run   # show what would be indexed

This script:
  1. Classifies repository files through the canonical file-type registry
  2. POSTs them as records to /knowledge/sources/index-records
  3. The snake chat (CodeCompass) can then retrieve real project context
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from agent.codecompass.parser_limits import ParserGuardViolation, ParserLimits
from ananta_contracts.file_type_classifier import FileTypeClassification, FileTypeClassifier, FileTypeMatchKind
from ananta_contracts.file_type_coverage import (
    CoverageOutcome,
    FileTypeCoverageReport,
    RequiredPathRule,
)
from ananta_contracts.file_type_rollout import FileTypeRolloutPolicy
from ananta_contracts.file_type_support import (
    FileTypeDescriptor,
    FileTypeSupportRegistry,
    load_file_type_support_registry,
)

ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {
    "node_modules", "__pycache__", ".git", "venv", ".venv",
    "dist", "build", ".angular", "test-results", ".claude",
    "migrations", "alembic", "project-workspaces", "data", "secrets",
}
EXCLUDE_FILE_PATTERNS = {"*.pyc", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
MAX_FILE_BYTES = 48_000  # skip files larger than this
MAX_RECORDS = 2000       # hard cap to avoid overwhelming the indexer
MAX_SNAPSHOT_HASH_BYTES = 8_000_000
SNAPSHOT_POLICY_PATH = ROOT / "config" / "codecompass" / "snapshot_policy.v1.json"
SOURCE_SCOPE = "repo_path"


@dataclass(frozen=True, slots=True)
class IndexCandidate:
    path: Path
    relative_path: str
    descriptor: FileTypeDescriptor | None
    classification: FileTypeClassification | None
    byte_size: int
    content_sha256: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(slots=True)
class IndexScanPlan:
    registry: FileTypeSupportRegistry
    selected: list[IndexCandidate]
    coverage: FileTypeCoverageReport
    truncated: bool
    limits: ParserLimits = field(default_factory=ParserLimits.from_environment)

# Semantic aliases: file path fragments → tags added to the content header.
# This bridges the gap between project-internal names and user-facing terms so
# embedding queries like "codecompass" find the right Python files.
_PATH_TAGS: list[tuple[str, str]] = [
    ("codecompass",          "CodeCompass RAG retrieval system knowledge index"),
    ("knowledge_index",      "CodeCompass knowledge index retrieval RAG"),
    ("retrieval_service",    "CodeCompass RAG retrieval orchestration"),
    ("context_bundle",       "CodeCompass context bundle RAG assembly grounded prompt"),
    ("worker_workspace",     "worker workspace preparation CodeCompass context files"),
    ("knowledge_routes",     "CodeCompass knowledge API routes"),
    ("snake",                "AI-Snake chat CodeCompass question answering"),
    ("goal_service",         "goal task planning execution service"),
    ("planning_strategies",  "planning strategy task decomposition"),
    ("task_scoped_execution","task execution worker proposal pipeline"),
    ("sgpt",                 "LLM CLI backend ananta-worker execution"),
]

# A synthetic glossary record injected first so the embedding index always
# contains the project terminology mapping even before any file is indexed.
_GLOSSARY_RECORD = {
    "file": "_project_glossary.md",
    "path": "_project_glossary.md",
    "content": """\
# Ananta Project Glossary — CodeCompass & RAG System

## CodeCompass
CodeCompass ist das RAG-System (Retrieval Augmented Generation) von Ananta.
Es indiziert Quellcode-Dateien und liefert bei Anfragen die semantisch relevantesten
Datei-Inhalte als Kontext für LLM-Anfragen (AI-Snake Chat, ananta-worker, OpenCode).

Kern-Python-Dateien von CodeCompass:
- agent/services/codecompass_retrieval_flag_service.py  — steuert aktive CodeCompass-Quellen
- agent/services/codecompass_output_reader.py            — liest CodeCompass-Ausgaben
- agent/services/wiki_codecompass_bridge.py              — Wiki-Integration
- agent/services/knowledge_index_retrieval_service.py    — Hauptlogik Knowledge-Index-Abfrage
- agent/services/knowledge_index_job_service.py          — Indizierungs-Jobs verwalten
- agent/services/retrieval_service.py                    — RAG-Orchestrierung aller Engines
- agent/services/context_bundle_service.py               — baut den RAG-Kontext-Bundle zusammen
- agent/services/prompt_context_bundle_service.py        — Prompt-Kontext-Aufbereitung
- agent/services/worker_workspace_service.py             — bereitet Workspace für Worker vor
- agent/routes/knowledge.py                              — REST-API für Knowledge/CodeCompass

## Knowledge Index
Der Knowledge Index speichert Datei-Inhalte als Vektoren (Embeddings).
Queries liefern die N ähnlichsten Chunks zurück (top_k).
Source scope "repo_path" = indizierte Projekt-Dateien samt Architekturgraph.

## RAG (Retrieval Augmented Generation)
Vor jeder LLM-Anfrage werden relevante Datei-Inhalte aus dem Index geholt
und in den Prompt eingebettet, damit das Modell projektspezifische Fragen
beantworten kann.

## AI-Snake Chat
Der AI-Snake Chat (/snake/ask) nutzt CodeCompass: Die Nutzeranfrage wird
gegen den Knowledge Index abgeglichen, relevante Dateien werden als Kontext
übergeben, dann antwortet das LLM.
""",
}


def _load_registry() -> FileTypeSupportRegistry:
    return load_file_type_support_registry(ROOT)


def _should_exclude_path(relative_path: str) -> str | None:
    normalized = str(relative_path or "").replace("\\", "/").lstrip("/")
    parts = Path(normalized).parts
    if any(part in EXCLUDE_DIRS for part in parts[:-1]):
        return "excluded_directory"
    name = parts[-1] if parts else ""
    lower_name = name.lower()
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in EXCLUDE_FILE_PATTERNS):
        return "excluded_file_pattern"
    if lower_name == ".env" or (
        lower_name.startswith(".env.") and lower_name not in {".env.example", ".env.sample"}
    ):
        return "secret_path"
    if lower_name in {"id_rsa", "id_ed25519", "credentials", "credentials.json"}:
        return "secret_path"
    if Path(lower_name).suffix in {".pem", ".key", ".p12", ".pfx"}:
        return "secret_path"
    return None


def _repository_paths() -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=False,
        capture_output=True,
    )
    if completed.returncode == 0:
        decoded = completed.stdout.decode("utf-8", errors="surrogateescape")
        return sorted({item for item in decoded.split("\0") if item})
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
    )


def _safe_candidate_path(relative_path: str) -> Path | None:
    normalized = str(relative_path or "").replace("\\", "/").lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        return None
    candidate = ROOT / normalized
    if candidate.is_symlink():
        return None
    try:
        path = candidate.resolve(strict=True)
        path.relative_to(ROOT.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    if not path.is_file():
        return None
    return path


def _probe_text(path: Path) -> tuple[str | None, bool]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(4096)
    except OSError:
        return None, False
    if b"\0" in raw:
        return None, False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, False
    return (text.splitlines()[0] if text else "", True)


def _content_sha256(path: Path, *, byte_size: int) -> str | None:
    """Hash bounded regular files without following symlinks."""

    if byte_size < 0 or byte_size > MAX_SNAPSHOT_HASH_BYTES or path.is_symlink():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _runtime_availability(registry: FileTypeSupportRegistry) -> dict[str, bool]:
    requirements = {
        requirement
        for descriptor in registry.descriptors
        for pipeline in registry.pipelines
        for capability in ("indexed", "symbols", "relationships")
        for requirement in getattr(descriptor.support_for(pipeline), capability).runtime_requirements
    }
    availability: dict[str, bool] = {}
    for requirement in sorted(requirements):
        kind, value = requirement.split(":", 1)
        if kind == "python-module":
            try:
                availability[requirement] = importlib.util.find_spec(value) is not None
            except (ImportError, ModuleNotFoundError, ValueError):
                availability[requirement] = False
        elif kind == "executable":
            availability[requirement] = shutil.which(value) is not None
        elif kind == "tree-sitter-language":
            try:
                from agent.repository_map_tree_sitter import resolve_tree_sitter_parser

                availability[requirement] = resolve_tree_sitter_parser(value).status.available
            except Exception:
                availability[requirement] = False
        else:
            availability[requirement] = False
    return availability


def _fair_candidate_order(candidates: list[IndexCandidate]) -> list[IndexCandidate]:
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}
    queues: dict[int, dict[str, deque[IndexCandidate]]] = defaultdict(lambda: defaultdict(deque))
    for candidate in sorted(candidates, key=lambda item: item.relative_path):
        descriptor = candidate.descriptor
        priority = priority_rank.get((descriptor.priority if descriptor else "P9").upper(), 99)
        family = descriptor.family if descriptor else "unknown"
        queues[priority][family].append(candidate)
    ordered: list[IndexCandidate] = []
    for priority in sorted(queues):
        family_queues = queues[priority]
        families = sorted(family_queues)
        while any(family_queues[family] for family in families):
            for family in families:
                if family_queues[family]:
                    ordered.append(family_queues[family].popleft())
    return ordered


def _reserve_required_candidates(
    ordered: list[IndexCandidate],
    *,
    max_records: int,
    required_path_rules: list[RequiredPathRule | dict | str] | None,
) -> list[IndexCandidate]:
    """Reserve the minimum required matches before filling the fair-share cap."""

    limit = max(0, int(max_records))
    if not required_path_rules or limit == 0:
        return ordered[:limit]
    rules = [RequiredPathRule.from_value(item) for item in required_path_rules]
    reserved: list[IndexCandidate] = []
    reserved_paths: set[str] = set()
    for rule in sorted(rules, key=lambda item: item.pattern):
        matches = [
            candidate
            for candidate in ordered
            if fnmatch.fnmatchcase(candidate.relative_path, rule.pattern)
            and candidate.relative_path not in reserved_paths
        ]
        for candidate in matches[: rule.minimum_indexed]:
            if len(reserved) >= limit:
                break
            reserved.append(candidate)
            reserved_paths.add(candidate.relative_path)
    remainder = [candidate for candidate in ordered if candidate.relative_path not in reserved_paths]
    return [*reserved, *remainder[: max(0, limit - len(reserved))]]


def _collect_index_plan(
    *,
    max_records: int = MAX_RECORDS,
    priorities: set[str] | None = None,
    enabled_formats: set[str] | None = None,
    disabled_formats: set[str] | None = None,
    required_path_rules: list[RequiredPathRule | dict | str] | None = None,
    limits: ParserLimits | None = None,
) -> IndexScanPlan:
    effective_limits = limits or ParserLimits.from_environment()
    registry = _load_registry()
    rollout = FileTypeRolloutPolicy.build(
        registry,
        priorities=priorities or {"P0", "P1", "P2"},
        enabled_format_ids=enabled_formats or (),
        disabled_format_ids=disabled_formats or (),
    )
    classifier = FileTypeClassifier(registry)
    coverage = FileTypeCoverageReport(
        registry,
        pipeline="setup_index",
        runtime_availability=_runtime_availability(registry),
    )
    indexable: list[IndexCandidate] = []

    for relative_path in _repository_paths():
        normalized = str(relative_path).replace("\\", "/").lstrip("/")
        exclusion_reason = _should_exclude_path(normalized)
        raw_path = ROOT / normalized
        byte_size = 0
        try:
            byte_size = max(0, raw_path.lstat().st_size)
        except OSError:
            pass
        if exclusion_reason:
            coverage.add(
                path=normalized,
                descriptor=None,
                detected_type="excluded_before_classification",
                outcome=CoverageOutcome.EXCLUDED,
                exclusion_reason=exclusion_reason,
                byte_size=byte_size,
            )
            continue
        path = _safe_candidate_path(normalized)
        if path is None:
            coverage.add(
                path=normalized,
                descriptor=None,
                detected_type="unsafe_or_missing",
                outcome=CoverageOutcome.EXCLUDED,
                exclusion_reason="unsafe_path_symlink_or_missing",
                diagnostics=("path_policy_blocked",),
                byte_size=byte_size,
            )
            continue
        content_sha256 = _content_sha256(path, byte_size=byte_size)
        first_line, is_text = _probe_text(path)
        classification = classifier.classify(normalized, first_line=first_line, is_text=is_text)
        if not is_text:
            coverage.add(
                path=normalized,
                descriptor=classification.descriptor if classification else None,
                detected_type="unclassified_binary",
                outcome=CoverageOutcome.UNSUPPORTED,
                diagnostics=("binary_content_blocked",),
                byte_size=byte_size,
                content_sha256=content_sha256,
            )
            continue
        if classification is None:
            coverage.add(
                path=normalized,
                descriptor=None,
                detected_type="unknown_text",
                outcome=CoverageOutcome.UNSUPPORTED,
                diagnostics=("unknown_text_type",),
                byte_size=byte_size,
                content_sha256=content_sha256,
            )
            continue
        descriptor = classification.descriptor
        detected_type = (
            "unknown_text" if classification.match_kind is FileTypeMatchKind.TEXT_FALLBACK else descriptor.format_id
        )
        if not rollout.allows(descriptor):
            coverage.add(
                path=normalized,
                descriptor=descriptor,
                detected_type=detected_type,
                outcome=CoverageOutcome.EXCLUDED,
                exclusion_reason="file_type_feature_disabled",
                byte_size=byte_size,
                content_sha256=content_sha256,
            )
            continue
        if not descriptor.support_for("setup_index").indexed.configured:
            coverage.add(
                path=normalized,
                descriptor=descriptor,
                detected_type=detected_type,
                outcome=CoverageOutcome.UNSUPPORTED,
                diagnostics=("setup_index_not_configured",),
                byte_size=byte_size,
                content_sha256=content_sha256,
            )
            continue
        if byte_size > min(MAX_FILE_BYTES, effective_limits.max_file_bytes):
            coverage.add(
                path=normalized,
                descriptor=descriptor,
                detected_type=detected_type,
                outcome=CoverageOutcome.EXCLUDED,
                exclusion_reason="file_size_limit",
                diagnostics=("file_size_limit_exceeded",),
                byte_size=byte_size,
                content_sha256=content_sha256,
            )
            continue
        indexable.append(
            IndexCandidate(
                path=path,
                relative_path=normalized,
                descriptor=descriptor,
                classification=classification,
                byte_size=byte_size,
                content_sha256=content_sha256,
                diagnostics=("unknown_text_type",) if detected_type == "unknown_text" else (),
            )
        )

    ordered = _fair_candidate_order(indexable)
    selected = _reserve_required_candidates(
        ordered,
        max_records=max_records,
        required_path_rules=required_path_rules,
    )
    selected_paths = {candidate.relative_path for candidate in selected}
    for candidate in ordered:
        if candidate.relative_path in selected_paths:
            continue
        coverage.add(
            path=candidate.relative_path,
            descriptor=candidate.descriptor,
            detected_type=(
                "unknown_text"
                if candidate.classification
                and candidate.classification.match_kind is FileTypeMatchKind.TEXT_FALLBACK
                else None
            ),
            outcome=CoverageOutcome.EXCLUDED,
            exclusion_reason="max_files_fair_share",
            diagnostics=("selection_limit",),
            byte_size=candidate.byte_size,
            content_sha256=candidate.content_sha256,
        )
    return IndexScanPlan(
        registry=registry,
        selected=selected,
        coverage=coverage,
        truncated=len(ordered) > len(selected),
        limits=effective_limits,
    )


def _collect_files() -> list[Path]:
    """Backward-compatible projection of the registry-driven scan plan."""

    return [candidate.path for candidate in _collect_index_plan().selected]


def _file_tags(rel: str) -> str:
    """Return extra semantic tag tokens for a file based on path keywords."""
    tags: list[str] = []
    for fragment, tag in _PATH_TAGS:
        if fragment in rel:
            tags.append(tag)
    return " | ".join(tags)


def _redact_sensitive_values(content: str) -> tuple[str, bool]:
    import re

    key_pattern = re.compile(
        r"(?im)^(?P<indent>\s*)(?P<quote>[\"']?)(?P<key>[a-z_][a-z0-9_.-]*)"
        r"(?P=quote)(?P<separator>\s*(?::(?!=)|(?<![=!<>])=(?!=))\s*)"
        r"(?P<value>[^\n(){}\[\]]+)$"
    )
    redaction_count = 0

    def redact_assignment(match: re.Match[str]) -> str:
        # A quoted placeholder remains valid in scalar Python assignments,
        # annotations, JSON, YAML and TOML. Container/call expressions are
        # deliberately excluded by the pattern so multiline source structure
        # cannot be orphaned. Preserve a mapping/list comma as well.
        nonlocal redaction_count
        key = match.group("key").lower()
        key_segments = {part for part in re.split(r"[_.-]+", key) if part}
        is_sensitive = (
            any(
                marker in key
                for marker in (
                    "password",
                    "passwd",
                    "api_key",
                    "api-key",
                    "private_key",
                    "private-key",
                    "secret",
                )
            )
            or "token" in key_segments
        )
        if not is_sensitive:
            return match.group(0)
        redaction_count += 1
        suffix = "," if match.group("value").rstrip().endswith(",") else ""
        prefix = (
            match.group("indent")
            + match.group("quote")
            + match.group("key")
            + match.group("quote")
            + match.group("separator")
        )
        return f'{prefix}"[REDACTED]"{suffix}'

    redacted = key_pattern.sub(redact_assignment, content)
    private_key_pattern = re.compile(
        r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
        re.DOTALL,
    )
    redacted, private_count = private_key_pattern.subn("[REDACTED PRIVATE KEY]", redacted)
    return redacted, bool(redaction_count or private_count)


def _build_records_from_plan(plan: IndexScanPlan) -> tuple[list[dict], FileTypeCoverageReport]:
    records: list[dict] = [dict(_GLOSSARY_RECORD)]
    for candidate in plan.selected:
        started = time.perf_counter()
        descriptor = candidate.descriptor
        detected_type = (
            "unknown_text"
            if candidate.classification
            and candidate.classification.match_kind is FileTypeMatchKind.TEXT_FALLBACK
            else descriptor.format_id if descriptor else "unknown_text"
        )
        try:
            content = candidate.path.read_text(encoding="utf-8", errors="replace").strip()
            plan.limits.preflight(path=candidate.relative_path, content=content)
            if not content:
                plan.coverage.add(
                    path=candidate.relative_path,
                    descriptor=descriptor,
                    detected_type=detected_type,
                    outcome=CoverageOutcome.EXCLUDED,
                    exclusion_reason="empty_file",
                    byte_size=candidate.byte_size,
                    duration_seconds=time.perf_counter() - started,
                    content_sha256=candidate.content_sha256,
                    extractor_id="setup_index.plain_text",
                    extractor_version="1",
                )
                continue
            content, was_redacted = _redact_sensitive_values(content)
            tags = _file_tags(candidate.relative_path)
            header = f"# {candidate.relative_path}"
            if tags:
                header += f"\n# Themen: {tags}"
            content_with_header = f"{header}\n{content}"
            records.append(
                {
                    "file": candidate.relative_path,
                    "path": candidate.relative_path,
                    "content": content_with_header,
                    "file_type": {
                        "detected_type": detected_type,
                        "family": descriptor.family if descriptor else "unknown",
                        "parser_strategy": descriptor.parser_strategy if descriptor else "plain_text",
                        "registry_version": plan.registry.registry_version,
                        "registry_digest": plan.registry.digest,
                    },
                }
            )
            diagnostics = [*candidate.diagnostics]
            if was_redacted:
                diagnostics.append("secret_value_redacted")
            plan.coverage.add(
                path=candidate.relative_path,
                descriptor=descriptor,
                detected_type=detected_type,
                outcome=CoverageOutcome.INDEXED,
                diagnostics=diagnostics,
                byte_size=candidate.byte_size,
                duration_seconds=time.perf_counter() - started,
                content_sha256=candidate.content_sha256,
                extractor_id="setup_index.plain_text",
                extractor_version="1",
            )
        except ParserGuardViolation as exc:
            plan.coverage.add(
                path=candidate.relative_path,
                descriptor=descriptor,
                detected_type=detected_type,
                outcome=CoverageOutcome.EXCLUDED,
                exclusion_reason=exc.reason_code,
                diagnostics=(exc.diagnostic_code, exc.reason_code),
                byte_size=candidate.byte_size,
                duration_seconds=time.perf_counter() - started,
                content_sha256=candidate.content_sha256,
                extractor_id="setup_index.plain_text",
                extractor_version="1",
            )
            continue
        except Exception as exc:
            plan.coverage.add(
                path=candidate.relative_path,
                descriptor=descriptor,
                detected_type=detected_type,
                outcome=CoverageOutcome.FAILED,
                diagnostics=(f"file_read_failed:{type(exc).__name__}",),
                byte_size=candidate.byte_size,
                duration_seconds=time.perf_counter() - started,
                content_sha256=candidate.content_sha256,
                extractor_id="setup_index.plain_text",
                extractor_version="1",
            )
            continue
    return records, plan.coverage


def _build_records(files: list[Path]) -> list[dict]:
    """Backward-compatible record builder using canonical classification."""

    registry = _load_registry()
    classifier = FileTypeClassifier(registry)
    coverage = FileTypeCoverageReport(
        registry,
        pipeline="setup_index",
        runtime_availability=_runtime_availability(registry),
    )
    candidates: list[IndexCandidate] = []
    for path in files:
        resolved = Path(path).resolve()
        try:
            relative = resolved.relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            continue
        first_line, is_text = _probe_text(resolved)
        classification = classifier.classify(relative, first_line=first_line, is_text=is_text)
        descriptor = classification.descriptor if classification else None
        candidates.append(
            IndexCandidate(
                path=resolved,
                relative_path=relative,
                descriptor=descriptor,
                classification=classification,
                byte_size=resolved.stat().st_size,
                content_sha256=_content_sha256(resolved, byte_size=resolved.stat().st_size),
            )
        )
    plan = IndexScanPlan(registry=registry, selected=candidates, coverage=coverage, truncated=False)
    records, _coverage = _build_records_from_plan(plan)
    return records


def _build_semantic_translation_records(files: list[Path]) -> tuple[list[dict], dict]:
    from agent.codecompass.semantic_translation.config import load_semantic_translation_config
    from agent.codecompass.semantic_translation.equivalence_registry import EquivalenceRuleRegistry
    from agent.codecompass.semantic_translation.registry import get_semantic_adapter_registry

    config = load_semantic_translation_config()
    if not config.enabled:
        return [], {"enabled": False, "warnings": list(config.diagnostics)}
    registry = get_semantic_adapter_registry()
    allowed_languages = set(config.source_languages)
    analyze_all = "all" in allowed_languages or "*" in allowed_languages
    records: list[dict] = []
    diagnostics: list[dict] = []
    analyzed_files = 0
    analyzed_by_language: dict[str, int] = defaultdict(int)
    parser_strategies: dict[str, str] = {}
    limit_reached = False
    for path in files:
        rel = str(path.relative_to(ROOT))
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            diagnostics.append({"code": "semantic_file_read_failed", "path": rel, "message": str(exc)})
            continue
        adapter = registry.find(rel, content)
        if adapter is None:
            continue
        language_aliases = {adapter.language}
        if adapter.language == "typescript" and path.suffix.lower() in {".js", ".jsx"}:
            language_aliases.add("javascript")
        if not analyze_all and not language_aliases.intersection(allowed_languages):
            continue
        analyzed_files += 1
        analyzed_by_language[adapter.language] += 1
        parser_strategies[adapter.language] = adapter.parser_strategy
        emitted = registry.emit_graph_records(rel, content)
        batch = [*emitted["nodes"], *emitted["edges"]]
        remaining = max(0, config.max_graph_records - len(records))
        records.extend(batch[:remaining])
        diagnostics.extend(emitted["diagnostics"])
        if len(batch) > remaining or len(records) >= config.max_graph_records:
            diagnostics.append({"code": "semantic_graph_record_limit_reached", "path": rel})
            limit_reached = True
            break
    if not limit_reached:
        rules = EquivalenceRuleRegistry().records()
        remaining = max(0, config.max_graph_records - len(records))
        records.extend(rules[:remaining])
        if len(rules) > remaining:
            diagnostics.append({"code": "semantic_graph_record_limit_reached", "path": ""})
    summary = {
        "enabled": True,
        "analyzed_files": analyzed_files,
        "analyzed_by_language": dict(sorted(analyzed_by_language.items())),
        "recognized_languages": sorted(analyzed_by_language),
        "parser_strategies": dict(sorted(parser_strategies.items())),
        "record_count": len(records),
        "node_count": sum(
            1
            for row in records
            if (row.get("_provenance") or {}).get("output_kind") == "semantic_nodes"
        ),
        "edge_count": sum(
            1
            for row in records
            if (row.get("_provenance") or {}).get("output_kind") == "semantic_edges"
        ),
        "rule_count": sum(
            1
            for row in records
            if (row.get("_provenance") or {}).get("output_kind") == "equivalence_rules"
        ),
        "warnings": [str(row.get("code") or row) for row in diagnostics],
        "diagnostics": diagnostics,
    }
    return records, summary


def _login(hub: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{hub.rstrip('/')}/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    token = str((data.get("data") or {}).get("access_token") or "")
    if not token:
        raise RuntimeError("Login failed — no access_token in response")
    return token


def _post_index(
    hub: str,
    token: str,
    records: list[dict],
    source_id: str,
    *,
    source_metadata: dict | None = None,
) -> dict:
    payload = json.dumps({
        "source_scope": SOURCE_SCOPE,
        "source_id": source_id,
        "records": records,
        # Index builds are delegated to the persistent Hub task queue.  The
        # field remains explicit for compatibility with older Hub versions.
        "async": True,
        "profile_name": "deep_code",
        "source_metadata": {
            "project": "ananta",
            "indexed_by": "setup_codecompass_index.py",
            **dict(source_metadata or {}),
        },
    }).encode()
    req = urllib.request.Request(
        f"{hub.rstrip('/')}/knowledge/sources/index-records",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:400]}") from exc


def _get_index_job(hub: str, token: str, job_id: str) -> dict:
    encoded_job_id = urllib.parse.quote(str(job_id), safe="")
    req = urllib.request.Request(
        f"{hub.rstrip('/')}/knowledge/index-jobs/{encoded_job_id}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:400]}") from exc


def _wait_for_index_job(
    hub: str,
    token: str,
    job_id: str,
    *,
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        response = _get_index_job(hub, token, job_id)
        job = dict((response.get("data") or {}).get("job") or {})
        if str(job.get("status") or "").strip().lower() in {
            "completed",
            "failed",
            "cancelled",
        }:
            return job
        if time.monotonic() >= deadline:
            return {
                **job,
                "job_id": str(job.get("job_id") or job_id),
                "status": "wait_timeout",
            }
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def _csv_environment(name: str) -> list[str]:
    return sorted(
        {
            item.strip()
            for item in str(os.environ.get(name) or "").split(",")
            if item.strip()
        }
    )


def _load_snapshot_policy(path: Path = SNAPSHOT_POLICY_PATH) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("schema") or "") != "codecompass.snapshot-policy.v1":
        raise ValueError("snapshot_policy_schema_invalid")
    rules = [RequiredPathRule.from_value(item).as_dict() for item in list(payload.get("required_paths") or [])]
    return {
        "schema": "codecompass.snapshot-policy.v1",
        "profile_id": str(payload.get("profile_id") or "default"),
        "required_paths": rules,
    }


def _git_source_revision() -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    revision = completed.stdout.strip() if completed.returncode == 0 else ""
    return revision or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Index Ananta codebase into CodeCompass")
    parser.add_argument("--hub", default=os.environ.get("ANANTA_HUB_URL", "http://localhost:5000"))
    parser.add_argument("--user", default=os.environ.get("INITIAL_ADMIN_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("INITIAL_ADMIN_PASSWORD", "test123"))
    parser.add_argument("--source-id", default="ananta-project")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be indexed, don't POST")
    parser.add_argument(
        "--coverage-json",
        type=Path,
        help="Write deterministic per-file coverage evidence to this JSON file.",
    )
    parser.add_argument(
        "--snapshot-manifest-json",
        type=Path,
        help="Write the immutable content snapshot and required-path gate to JSON.",
    )
    parser.add_argument(
        "--required-path",
        action="append",
        default=[],
        help="Add an exact path or glob that must have at least one indexed match.",
    )
    parser.add_argument(
        "--priorities",
        default=os.environ.get("ANANTA_CODECOMPASS_FILE_TYPE_PRIORITIES", "P0,P1,P2"),
        help="Comma-separated file-type rollout priorities (P0,P1,P2).",
    )
    parser.add_argument(
        "--enable-format",
        action="append",
        default=_csv_environment("ANANTA_CODECOMPASS_ENABLED_FORMATS"),
        help="Only enable this registry format id (repeatable).",
    )
    parser.add_argument(
        "--disable-format",
        action="append",
        default=_csv_environment("ANANTA_CODECOMPASS_DISABLED_FORMATS"),
        help="Disable this registry format id (repeatable).",
    )
    parser.add_argument("--max-records", type=int, default=MAX_RECORDS)
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=float(os.environ.get("ANANTA_CODECOMPASS_INDEX_WAIT_SECONDS", "0")),
        help="Wait this many seconds for the delegated index job (0 only queues it).",
    )
    args = parser.parse_args()

    print(f"Scanning {ROOT} for source files…")
    priorities = {item.strip().upper() for item in str(args.priorities).split(",") if item.strip()}
    if not priorities or priorities - {"P0", "P1", "P2"}:
        parser.error("--priorities must contain only P0, P1, P2")
    try:
        snapshot_policy = _load_snapshot_policy()
        required_rules = [*snapshot_policy["required_paths"], *list(args.required_path or [])]
        plan = _collect_index_plan(
            max_records=max(1, min(int(args.max_records), 100_000)),
            priorities=priorities,
            enabled_formats=set(args.enable_format),
            disabled_formats=set(args.disable_format),
            required_path_rules=required_rules,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    records, coverage_report = _build_records_from_plan(plan)
    files = [candidate.path for candidate in plan.selected]
    semantic_records, semantic_summary = _build_semantic_translation_records(files)
    if semantic_records:
        records.extend(semantic_records)
    coverage_payload = coverage_report.as_dict()
    manifest_coverage = coverage_report.manifest_coverage(truncated=plan.truncated)
    try:
        snapshot_manifest = coverage_report.snapshot_manifest(
            required_path_rules=required_rules,
            profile={
                "profile_id": snapshot_policy["profile_id"],
                "priorities": sorted(priorities),
                "enabled_formats": sorted(set(args.enable_format)),
                "disabled_formats": sorted(set(args.disable_format)),
                "max_records": max(1, min(int(args.max_records), 100_000)),
                "max_file_bytes": min(MAX_FILE_BYTES, plan.limits.max_file_bytes),
            },
            source_revision=_git_source_revision(),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        f"  {manifest_coverage['manifest_candidate_count']} candidates, "
        f"{manifest_coverage['indexed']} indexed files → {len(records)} records"
    )

    if args.coverage_json:
        args.coverage_json.parent.mkdir(parents=True, exist_ok=True)
        args.coverage_json.write_text(
            json.dumps(coverage_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"  Coverage: {args.coverage_json}")

    if args.snapshot_manifest_json:
        args.snapshot_manifest_json.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot_manifest_json.write_text(
            json.dumps(snapshot_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"  Snapshot manifest: {args.snapshot_manifest_json}")

    required_gate = dict(snapshot_manifest.get("required_paths") or {})
    if not bool(required_gate.get("passed")):
        failed = ", ".join(str(item) for item in list(required_gate.get("failed_patterns") or []))
        print(f"  ERROR: required-path gate failed: {failed}", file=sys.stderr)
        return 2

    if semantic_summary.get("enabled"):
        langs = ", ".join(semantic_summary.get("recognized_languages") or ["none"])
        warnings_count = len(semantic_summary.get("warnings") or [])
        print(
            "  Semantic Translation: "
            f"{semantic_summary['analyzed_files']} files, "
            f"languages=[{langs}], "
            f"{semantic_summary['node_count']} nodes, "
            f"{semantic_summary['edge_count']} edges, "
            f"{semantic_summary['rule_count']} rules, "
            f"{warnings_count} warnings"
        )

    if args.dry_run:
        for record in records[:20]:
            label = str(
                record.get("file")
                or (record.get("provenance") or {}).get("file")
                or record.get("id")
                or record.get("source")
                or record.get("rule_id")
                or record.get("kind")
                or "record"
            )
            size = len(str(record.get("content") or json.dumps(record, sort_keys=True)))
            print(f"  {label} ({size} chars)")
        if len(records) > 20:
            print(f"  … and {len(records) - 20} more")
        return 0

    print(f"Logging in to {args.hub}…")
    token = _login(args.hub, args.user, args.password)
    print("  OK")

    print(f"Posting {len(records)} records to /knowledge/sources/index-records…")
    result = _post_index(
        args.hub,
        token,
        records,
        args.source_id,
        source_metadata={
            "file_type_registry_version": plan.registry.registry_version,
            "file_type_registry_digest": plan.registry.digest,
            "file_type_priorities": sorted(priorities),
            "file_type_coverage": manifest_coverage,
            "file_type_metrics_snapshot": coverage_report.metrics_snapshot(),
            "codecompass_snapshot_revision": snapshot_manifest["snapshot_revision"],
            "codecompass_snapshot_manifest": snapshot_manifest,
            "semantic_parser_strategies": semantic_summary.get("parser_strategies") or {},
        },
    )
    status = result.get("status", "unknown")
    print(f"  Status: {status}")
    if status == "success":
        idx = (result.get("data") or {}).get("knowledge_index") or {}
        run = (result.get("data") or {}).get("run") or {}
        print(f"  Knowledge index ID : {idx.get('id', '?')}")
        print(f"  Run ID             : {run.get('id', '?')}")
        print(f"  Records indexed    : {(run.get('run_metadata') or {}).get('record_count', '?')}")
        print("\nDone. The snake chat will now have real Ananta project context.")
        return 0
    if status == "accepted":
        job = dict((result.get("data") or {}).get("job") or {})
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            print(f"  ERROR: accepted response omitted job_id: {result}", file=sys.stderr)
            return 1
        print(f"  Job ID             : {job_id}")
        print(f"  Job status         : {job.get('status', 'queued')}")
        if args.wait_seconds <= 0:
            print("\nQueued. Query /knowledge/index-jobs/<job-id> for persistent status.")
            return 0
        terminal_job = _wait_for_index_job(
            args.hub,
            token,
            job_id,
            timeout_seconds=args.wait_seconds,
        )
        terminal_status = str(terminal_job.get("status") or "unknown")
        print(f"  Final job status   : {terminal_status}")
        if terminal_status == "completed":
            run = dict(terminal_job.get("run") or {})
            print(f"  Run ID             : {run.get('id', '?')}")
            print("\nDone. The delegated CodeCompass index job completed.")
            return 0
        print(f"  ERROR: {terminal_job}", file=sys.stderr)
        return 1
    print(f"  ERROR: {result}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
