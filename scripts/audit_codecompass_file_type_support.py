#!/usr/bin/env python3
"""Audit CodeCompass file-type declarations against a tracked repository.

The audit is read-only.  It validates evidence references, probes declared
runtime requirements, classifies a bounded prefix of every tracked file, and
exports the three capability dimensions without treating the registry as an
authorization list.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.file_type_support_service import (  # noqa: E402
    FileTypeSupportFilter,
    FileTypeSupportFilterError,
    FileTypeSupportService,
    parse_optional_boolean,
    probe_file_type_runtime_requirements,
)
from ananta_contracts.file_type_classifier import FileTypeClassifier, FileTypeMatchKind  # noqa: E402
from ananta_contracts.file_type_support import (  # noqa: E402
    CapabilityDimension,
    FileTypeSupportContractError,
    FileTypeSupportRegistry,
    load_file_type_support_registry,
)

_PROBE_BYTES = 4096


@dataclass(frozen=True, slots=True)
class FileTypeAuditResult:
    repository_root: str
    tracked_files: int
    classified_files: int
    unclassified_files: tuple[str, ...]
    text_fallback_files: tuple[str, ...]
    inventory: Mapping[str, int]
    match_kinds: Mapping[str, int]
    runtime_availability: Mapping[str, bool]
    evidence_errors: tuple[str, ...]
    warnings: tuple[str, ...]
    support_matrix: Mapping[str, object]

    @property
    def errors(self) -> tuple[str, ...]:
        return self.evidence_errors

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "codecompass.file-type-support-audit.v1",
            "repository_root": self.repository_root,
            "tracked_files": self.tracked_files,
            "classified_files": self.classified_files,
            "unclassified_files": list(self.unclassified_files),
            "text_fallback_files": list(self.text_fallback_files),
            "inventory": dict(sorted(self.inventory.items())),
            "match_kinds": dict(sorted(self.match_kinds.items())),
            "runtime_availability": dict(sorted(self.runtime_availability.items())),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "support_matrix": dict(self.support_matrix),
        }


def tracked_repository_paths(repository_root: Path) -> tuple[str, ...]:
    """Return deterministic Git-tracked paths without consulting ignored data."""

    completed = subprocess.run(
        ["git", "-C", str(repository_root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise FileTypeSupportContractError(f"git ls-files failed: {stderr or completed.returncode}")
    decoded = completed.stdout.decode("utf-8", errors="surrogateescape")
    return tuple(sorted(item for item in decoded.split("\0") if item))


def probe_runtime_requirements(registry: FileTypeSupportRegistry) -> dict[str, bool]:
    """Backward-compatible public wrapper around the shared runtime probe."""

    return probe_file_type_runtime_requirements(registry)


def audit_repository(
    repository_root: Path,
    registry: FileTypeSupportRegistry,
    *,
    tracked_paths: Sequence[str] | None = None,
    support_filter: FileTypeSupportFilter | None = None,
) -> FileTypeAuditResult:
    root = Path(repository_root).resolve()
    paths = tuple(tracked_paths) if tracked_paths is not None else tracked_repository_paths(root)
    classifier = FileTypeClassifier(registry)
    inventory: Counter[str] = Counter()
    match_kinds: Counter[str] = Counter()
    unclassified: list[str] = []
    fallbacks: list[str] = []

    for relative in sorted(dict.fromkeys(str(item).replace("\\", "/") for item in paths)):
        first_line, is_text = _bounded_text_probe(root / relative)
        classification = classifier.classify(relative, first_line=first_line, is_text=is_text)
        if classification is None:
            unclassified.append(relative)
            continue
        inventory[classification.format_id] += 1
        match_kinds[classification.match_kind.value] += 1
        if classification.match_kind is FileTypeMatchKind.TEXT_FALLBACK:
            fallbacks.append(relative)

    evidence_errors = _evidence_errors(root, registry)
    runtime = probe_runtime_requirements(registry)
    warnings = tuple(
        f"runtime requirement unavailable: {requirement}"
        for requirement, available in sorted(runtime.items())
        if not available
    )
    matrix = FileTypeSupportService(
        root,
        registry=registry,
        runtime_availability=runtime,
        runtime_scope="audit_process",
    ).support_matrix(support_filter)
    return FileTypeAuditResult(
        repository_root=str(root),
        tracked_files=len(paths),
        classified_files=sum(inventory.values()),
        unclassified_files=tuple(unclassified),
        text_fallback_files=tuple(fallbacks),
        inventory=dict(inventory),
        match_kinds=dict(match_kinds),
        runtime_availability=runtime,
        evidence_errors=evidence_errors,
        warnings=warnings,
        support_matrix=matrix,
    )


def _bounded_text_probe(path: Path) -> tuple[str | None, bool]:
    if path.is_symlink() or not path.is_file():
        return None, False
    try:
        with path.open("rb") as handle:
            raw = handle.read(_PROBE_BYTES)
    except OSError:
        return None, False
    if b"\0" in raw:
        return None, False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, False
    return (text.splitlines()[0] if text else ""), True


def _evidence_errors(repository_root: Path, registry: FileTypeSupportRegistry) -> tuple[str, ...]:
    refs = {
        evidence
        for descriptor in registry.descriptors
        for pipeline in registry.pipelines
        for dimension in CapabilityDimension
        for evidence in descriptor.support_for(pipeline).capability(dimension).evidence
    }
    return tuple(
        f"missing evidence: {ref}"
        for ref in sorted(refs)
        if not (repository_root / ref).is_file()
    )


def _table(result: FileTypeAuditResult) -> str:
    matrix_rows = list(result.support_matrix.get("rows") or [])
    effective_counts: Counter[tuple[str, str]] = Counter()
    for row in matrix_rows:
        if not isinstance(row, Mapping):
            continue
        pipeline = str(row.get("pipeline") or "")
        capabilities = row.get("capabilities")
        if not isinstance(capabilities, Mapping):
            continue
        for dimension in CapabilityDimension:
            value = capabilities.get(dimension.value)
            if isinstance(value, Mapping) and value.get("effective") is True:
                effective_counts[(pipeline, dimension.value)] += 1

    lines = [
        f"tracked={result.tracked_files} classified={result.classified_files} "
        f"fallback={len(result.text_fallback_files)} unclassified={len(result.unclassified_files)}",
        "",
        "Inventory:",
    ]
    lines.extend(f"  {format_id}: {count}" for format_id, count in sorted(result.inventory.items()))
    lines.extend(["", "Effective verified formats per pipeline:"])
    for pipeline in sorted({pipeline for pipeline, _dimension in effective_counts}):
        counts = " ".join(
            f"{dimension.value}={effective_counts[(pipeline, dimension.value)]}"
            for dimension in CapabilityDimension
        )
        lines.append(f"  {pipeline}: {counts}")
    if matrix_rows:
        lines.extend(["", "Support matrix:"])
        for row in matrix_rows:
            if not isinstance(row, Mapping):
                continue
            selectors = row.get("selectors") if isinstance(row.get("selectors"), Mapping) else {}
            parser = row.get("parser") if isinstance(row.get("parser"), Mapping) else {}
            selector_text = " ".join(
                f"{label}={','.join(str(item) for item in selectors.get(key) or ()) or '-'}"
                for label, key in (
                    ("ext", "extensions"),
                    ("exact", "exact_filenames"),
                    ("patterns", "patterns"),
                    ("shebangs", "shebangs"),
                )
            )
            lines.append(
                f"  {row.get('format_id')} pipeline={row.get('pipeline')} "
                f"priority={row.get('priority')} level={row.get('support_level')} "
                f"parser={'configured' if parser.get('configured') else 'missing'} "
                f"runtime={'missing' if row.get('missing_runtime') else 'available'} {selector_text}"
            )
    if result.runtime_availability:
        lines.extend(["", "Runtime requirements:"])
        lines.extend(
            f"  {requirement}: {'available' if available else 'missing'}"
            for requirement, available in sorted(result.runtime_availability.items())
        )
    if result.errors:
        lines.extend(["", "Errors:", *(f"  {item}" for item in result.errors)])
    if result.warnings:
        lines.extend(["", "Warnings:", *(f"  {item}" for item in result.warnings)])
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--output", choices=("table", "json"), default="table")
    parser.add_argument(
        "--priority",
        action="append",
        default=[],
        help="Filter by priority (repeat or use comma-separated values, for example P0,P1).",
    )
    parser.add_argument(
        "--support-level",
        "--level",
        dest="support_level",
        action="append",
        default=[],
        help="Filter by discovery, text_index, symbol_index, semantic_graph, domain_parser, or unsupported.",
    )
    parser.add_argument(
        "--dimension",
        action="append",
        default=[],
        help="Keep rows with a configured indexed, symbols, or relationships capability.",
    )
    parser.add_argument(
        "--pipeline",
        action="append",
        default=[],
        help="Filter by registry pipeline (repeat or use comma-separated values).",
    )
    parser.add_argument(
        "--missing-parser",
        nargs="?",
        const="true",
        choices=("true", "false"),
        help="Filter by absence of a configured parser; optionally pass false.",
    )
    parser.add_argument(
        "--missing-runtime",
        nargs="?",
        const="true",
        choices=("true", "false"),
        help="Filter by unavailable declared runtime requirements; optionally pass false.",
    )
    parser.add_argument(
        "--enabled",
        nargs="?",
        const="true",
        choices=("true", "false"),
        help="Filter enabled formats; pass false for disabled formats.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail when an optional declared runtime requirement is unavailable",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        support_filter = FileTypeSupportFilter.build(
            priorities=args.priority,
            support_levels=args.support_level,
            dimensions=args.dimension,
            pipelines=args.pipeline,
            missing_parser=parse_optional_boolean(args.missing_parser, field_name="missing_parser"),
            missing_runtime=parse_optional_boolean(args.missing_runtime, field_name="missing_runtime"),
            enabled=parse_optional_boolean(args.enabled, field_name="enabled"),
        )
        registry = load_file_type_support_registry(
            args.repository_root,
            registry_path=args.registry,
            schema_path=args.schema,
        )
        result = audit_repository(
            args.repository_root,
            registry,
            support_filter=support_filter,
        )
    except (FileTypeSupportContractError, FileTypeSupportFilterError) as exc:
        print(f"file-type audit failed: {exc}", file=sys.stderr)
        return 1
    if args.output == "json":
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(_table(result))
    if result.errors:
        return 1
    if args.strict and result.warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
