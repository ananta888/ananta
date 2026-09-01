#!/usr/bin/env python3
"""Generate the reproducible Visual Process Assistant source inventory.

The report is deliberately source-derived: it does not contain timestamps,
workspace-specific paths, generated source identifiers, or execution IDs.  It
audits the runtime registry, its aliases, the executable adapter boundary, the
Angular fallback, and the schema/compatibility inspector fields in one stable
projection.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.visual_process.node_definitions import list_node_definitions  # noqa: E402
from agent.visual_process.step_executor import get_step_executor  # noqa: E402
from agent.visual_process.task_kind_registry import (  # noqa: E402
    ALL_TASK_KINDS,
    LEGACY_MAP,
    canonical_task_kind_ids,
    list_task_kinds,
)

DEFAULT_OUTPUT = ROOT / "artifacts/domain/visual-process-assistant-baseline.json"

REGISTRY_PATH = "agent/visual_process/task_kind_registry.py"
ADAPTER_PATH = "agent/visual_process/step_adapters.py"
ADAPTER_SOURCE_PATHS = (
    ADAPTER_PATH,
    "agent/visual_process/query_rewrite_step_adapter.py",
)
EXECUTOR_PATH = "agent/visual_process/step_executor.py"
NODE_DEFINITION_PATH = "agent/visual_process/node_definitions.py"
INSPECTOR_HTML_PATH = "frontend-angular/src/app/features/visual-process/vp-step-inspector.component.html"
FALLBACK_PATH = "frontend-angular/src/app/features/visual-process/vp-editor-config.ts"
GENERATED_FALLBACK_PATH = "frontend-angular/src/app/features/visual-process/vp-node-definitions.generated.ts"
FACADE_PATH = "frontend-angular/src/app/features/visual-process/vp-editor-state.facade.ts"
EDITOR_PATH = "frontend-angular/src/app/features/visual-process/visual-process-editor.component.ts"
VALIDATOR_PATH = "agent/visual_process/validator.py"
BINDING_PATH = "agent/services/chat_process_binding.py"


@dataclass(frozen=True, slots=True)
class AdapterFact:
    kind: str
    class_name: str
    line: int
    consumed_paths: tuple[str, ...]


def _text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _line_for(relative_path: str, pattern: str, *, default: int = 1) -> int:
    regex = re.compile(pattern)
    for number, line in enumerate(_text(relative_path).splitlines(), start=1):
        if regex.search(line):
            return number
    return default


def _evidence(relative_path: str, symbol: str, line: int) -> dict[str, Any]:
    return {
        "path": relative_path,
        "symbol": symbol,
        "line_start": line,
        "line_end": line,
        "verification_status": "verified",
    }


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    cursor = node
    while isinstance(cursor, ast.Attribute):
        parts.append(cursor.attr)
        cursor = cursor.value
    if isinstance(cursor, ast.Name):
        parts.append(cursor.id)
    return tuple(reversed(parts))


def _literal_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        return node.value
    return None


def _consumed_paths(node: ast.ClassDef) -> tuple[str, ...]:
    """Return literal metadata/artifact/context paths consumed by an adapter."""

    found: set[str] = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "get"
            and child.args
        ):
            key = _literal_key(child.args[0])
            chain = _attribute_chain(child.func.value)
            if key and chain:
                if chain[-2:] == ("step", "metadata") or chain[-1] == "metadata":
                    found.add(f"/metadata/{key}")
                elif chain[-1] in {"artifacts", "context"}:
                    found.add(f"/{chain[-1]}/{key}")
        elif isinstance(child, ast.Subscript):
            key = _literal_key(child.slice)
            chain = _attribute_chain(child.value)
            if key and chain:
                if chain[-2:] == ("step", "metadata") or chain[-1] == "metadata":
                    found.add(f"/metadata/{key}")
                elif chain[-1] in {"artifacts", "context"}:
                    found.add(f"/{chain[-1]}/{key}")
    return tuple(sorted(found))


def _adapter_facts() -> dict[str, AdapterFact]:
    result: dict[str, AdapterFact] = {}
    for source_path in ADAPTER_SOURCE_PATHS:
        source = _text(source_path)
        tree = ast.parse(source, filename=source_path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {_attribute_chain(base)[-1] for base in node.bases if _attribute_chain(base)}
            if "StepAdapter" not in bases:
                continue
            kind: str | None = None
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) or item.name != "kind":
                    continue
                for child in ast.walk(item):
                    if isinstance(child, ast.Return):
                        kind = _literal_key(child.value)
                        if kind:
                            break
            if not kind:
                continue
            if kind in result:
                raise ValueError(f"duplicate_visual_process_adapter:{kind}")
            result[kind] = AdapterFact(
                kind=kind,
                class_name=node.name,
                line=node.lineno,
                consumed_paths=_consumed_paths(node),
            )
    return result


def _fallback_kinds() -> dict[str, int]:
    wiring = _text(FALLBACK_PATH)
    if not re.search(
        r"FALLBACK_KINDS[^=]*=\s*GENERATED_VISUAL_PROCESS_TASK_KINDS\.map\(",
        wiring,
    ):
        raise ValueError("frontend_generated_fallback_wiring_not_found")
    source = _text(GENERATED_FALLBACK_PATH)
    marker = "export const GENERATED_VISUAL_PROCESS_TASK_KINDS = "
    start = source.find(marker)
    if start < 0:
        raise ValueError("frontend_generated_fallback_kinds_not_found")
    payload_start = start + len(marker)
    try:
        payload, _end = json.JSONDecoder().raw_decode(source[payload_start:])
    except json.JSONDecodeError as exc:
        raise ValueError("frontend_generated_fallback_kinds_invalid") from exc
    if not isinstance(payload, list):
        raise ValueError("frontend_generated_fallback_kinds_invalid")
    result: dict[str, int] = {}
    search_offset = payload_start
    for item in payload:
        if not isinstance(item, dict) or not str(item.get("id") or ""):
            raise ValueError("frontend_generated_fallback_kind_invalid")
        kind = str(item["id"])
        if kind in result:
            raise ValueError(f"duplicate_frontend_fallback_kind:{kind}")
        encoded_id = json.dumps(kind, ensure_ascii=False)
        match = re.search(
            rf'"id"\s*:\s*{re.escape(encoded_id)}',
            source[search_offset:],
        )
        if match is None:
            raise ValueError(f"frontend_generated_fallback_kind_not_grounded:{kind}")
        absolute_offset = search_offset + match.start()
        result[kind] = source[:absolute_offset].count("\n") + 1
        search_offset += match.end()
    return result


def _inspector_compatibility() -> tuple[dict[str, int], dict[str, set[str]]]:
    source = _text(INSPECTOR_HTML_PATH)
    conditions = list(
        re.finditer(
            r"@if\s*\([^\n]*selectedStep\(\)!\.kind\s*===\s*'[^']+'[^\n]*\)\s*\{",
            source,
        )
    )
    branches: dict[str, int] = {}
    fields: dict[str, set[str]] = {}
    for index, condition in enumerate(conditions):
        end = conditions[index + 1].start() if index + 1 < len(conditions) else len(source)
        block = source[condition.start() : end]
        line = source[: condition.start()].count("\n") + 1
        kinds = re.findall(r"selectedStep\(\)!\.kind\s*===\s*'([^']+)'", condition.group(0))
        block_fields = set(re.findall(r"stepMeta\('([^']+)'\)", block))
        for token in kinds:
            canonical = LEGACY_MAP.get(token, token)
            branches.setdefault(canonical, line)
            fields.setdefault(canonical, set()).update(block_fields)
    return branches, fields


def _node_definition_fields() -> tuple[dict[str, set[str]], dict[str, dict[str, Any]]]:
    definitions = list_node_definitions()
    paths: dict[str, set[str]] = {}
    by_kind: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        kind = str(definition["kind"])
        if kind in by_kind:
            raise ValueError(f"duplicate_node_definition:{kind}")
        by_kind[kind] = definition
        paths[kind] = {str(field["path"]) for field in definition.get("fields") or []}
    return paths, by_kind


def _classification(kind: str, path: str, definition_paths: Mapping[str, set[str]]) -> str:
    if kind == "rerank" and path == "/metadata/reranker_weight":
        return "drift"
    if kind == "embed_api" and path == "/metadata/api_key":
        return "drift"
    if path in definition_paths.get(kind, set()):
        return "canonical"
    return "alias" if path.startswith("/metadata/") else "canonical"


def _field_inventory(
    canonical_kinds: Iterable[str],
    adapters: Mapping[str, AdapterFact],
    definition_paths: Mapping[str, set[str]],
    inspector_fields: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for kind in sorted(canonical_kinds):
        adapter_paths = set(adapters[kind].consumed_paths) if kind in adapters else set()
        frontend_paths = {f"/metadata/{field}" for field in inspector_fields.get(kind, set())}
        task_paths = {path for path in definition_paths.get(kind, set()) if path.startswith("/metadata/")}
        for path in sorted(adapter_paths | frontend_paths | task_paths):
            consumers: list[str] = []
            if path in task_paths:
                consumers.append("node_definition")
            if path in adapter_paths:
                consumers.append("backend_adapter")
            if path in frontend_paths:
                consumers.append("legacy_inspector")
            records.append(
                {
                    "canonical_kind": kind,
                    "path": path,
                    "classification": _classification(kind, path, definition_paths),
                    "consumers": consumers,
                }
            )
    return records


def _contract_findings() -> list[dict[str, Any]]:
    inspector = _text(INSPECTOR_HTML_PATH)
    adapters = _text(ADAPTER_PATH)
    node_definitions = _text(NODE_DEFINITION_PATH)
    validator = _text(VALIDATOR_PATH)
    facade = _text(FACADE_PATH)
    editor = _text(EDITOR_PATH)

    legacy_reranker = "stepMeta('reranker_weight')" in inspector
    canonical_reranker = "stepMeta('weight')" in inspector or '"/metadata/weight"' in node_definitions
    backend_reranker = bool(re.search(r"metadata\.get\(\s*[\"']weight[\"']", adapters))
    backend_reranker_alias = bool(re.search(r"metadata\.get\(\s*[\"']reranker_weight[\"']", adapters))
    plaintext_api_key = bool(re.search(r"metadata\.get\(\s*[\"']api_key[\"']", adapters))
    secret_reference = "/metadata/api_key_secret_ref" in node_definitions
    plaintext_quarantine = (
        'if "api_key" in step.metadata' in adapters and "legacy_plaintext_api_key_quarantined" in adapters
    )
    opaque_secret_resolution = "self._secret_resolver.resolve(secret_ref)" in adapters
    validation_path = bool(re.search(r"^\s+path:\s*str\s*\|\s*None", validator, flags=re.MULTILINE))
    atomic_drag = (
        "beginTransaction" in editor
        and "commitTransaction" in editor
        and "this.validation.set(null)" in facade
        and "refreshDirty" in facade
    )

    binding_source_line = next(
        (line for line in _text(BINDING_PATH).splitlines() if re.search(r"\bsource\s*=", line)),
        "",
    )
    binding_source_values = sorted(set(re.findall(r"[\"\']([^\"\']+)[\"\']", binding_source_line)))
    expected_binding_sources = ["global", "profile", "session_override"]

    reranker_frontend_line = _line_for(
        INSPECTOR_HTML_PATH,
        r"stepMeta\('(reranker_weight|weight)'\)",
    )
    api_adapter_line = _line_for(
        ADAPTER_PATH,
        r"class EmbedApiAdapter|metadata\.get\(\s*['\"]api_key['\"]",
    )

    return [
        {
            "finding_id": "reranker-weight-contract",
            "verification_status": "verified",
            "state": (
                "drift_detected"
                if legacy_reranker or not canonical_reranker or not backend_reranker
                else "aligned_with_read_only_alias"
                if backend_reranker_alias
                else "aligned"
            ),
            "finding": (
                "Der historische Feldpfad reranker_weight gegen den kanonischen Backendpfad weight wurde geprüft."
            ),
            "observed": {
                "backend_path": "/metadata/weight",
                "frontend_path": "/metadata/reranker_weight" if legacy_reranker else "/metadata/weight",
                "backend_compatibility_alias_read": backend_reranker_alias,
            },
            "evidence": [
                _evidence(
                    ADAPTER_PATH,
                    "RerankAdapter.execute",
                    _line_for(ADAPTER_PATH, r"weight\s*=\s*float\(step\.metadata\.get"),
                ),
                _evidence(INSPECTOR_HTML_PATH, "rerank inspector", reranker_frontend_line),
                _evidence(
                    NODE_DEFINITION_PATH, "_KIND_FIELDS.rerank", _line_for(NODE_DEFINITION_PATH, r'^\s+"rerank":')
                ),
            ],
        },
        {
            "finding_id": "embed-api-plaintext-secret",
            "verification_status": "verified",
            "state": (
                "plaintext_read_detected"
                if plaintext_api_key
                else "quarantined"
                if plaintext_quarantine and secret_reference and opaque_secret_resolution
                else "secret_boundary_incomplete"
            ),
            "finding": "Der frühere Klartextpfad metadata.api_key wurde gegen die opaque Secret-Referenz geprüft.",
            "observed": {
                "plaintext_adapter_read": plaintext_api_key,
                "plaintext_rejection_present": plaintext_quarantine,
                "secret_reference_defined": secret_reference,
                "opaque_secret_resolution_present": opaque_secret_resolution,
            },
            "evidence": [
                _evidence(ADAPTER_PATH, "EmbedApiAdapter", api_adapter_line),
                _evidence(
                    NODE_DEFINITION_PATH,
                    "_KIND_FIELDS.embed_api",
                    _line_for(NODE_DEFINITION_PATH, r"api_key_secret_ref"),
                ),
            ],
        },
        {
            "finding_id": "effective-process-source-enum",
            "verification_status": "verified",
            "state": "explicit" if binding_source_values == expected_binding_sources else "drift_detected",
            "finding": "Die Quelle der effektiven Prozessbindung besitzt drei explizite Werte.",
            "observed": {"values": binding_source_values},
            "evidence": [
                _evidence(
                    BINDING_PATH,
                    "resolve_effective_process_ref",
                    _line_for(BINDING_PATH, r'source\s*=\s*"session_override"'),
                ),
            ],
        },
        {
            "finding_id": "validation-issue-path",
            "verification_status": "verified",
            "state": "present" if validation_path else "missing",
            "finding": "Der historisch fehlende ValidationIssue.path-Vertrag wurde geprüft.",
            "observed": {"path_field_present": validation_path},
            "evidence": [
                _evidence(VALIDATOR_PATH, "ValidationIssue", _line_for(VALIDATOR_PATH, r"class ValidationIssue")),
            ],
        },
        {
            "finding_id": "drag-dirty-validation-invalidation",
            "verification_status": "verified",
            "state": "atomic_command_transaction" if atomic_drag else "unreliable",
            "finding": "Dragging wurde auf atomare Command-History, Dirty-State und Validierungsinvalidierung geprüft.",
            "observed": {"atomic_drag_transaction": atomic_drag},
            "evidence": [
                _evidence(
                    FACADE_PATH, "VpEditorStateFacade.afterMutation", _line_for(FACADE_PATH, r"private afterMutation")
                ),
                _evidence(
                    EDITOR_PATH,
                    "VisualProcessEditorComponent.onNodeMouseDown",
                    _line_for(EDITOR_PATH, r"onNodeMouseDown\("),
                ),
                _evidence(
                    EDITOR_PATH, "VisualProcessEditorComponent.onMouseUp", _line_for(EDITOR_PATH, r"onMouseUp\(")
                ),
            ],
        },
    ]


def build_baseline() -> dict[str, Any]:
    registry_rows = [dict(item) for item in list_task_kinds()]
    canonical = set(canonical_task_kind_ids())
    adapters = _adapter_facts()
    fallback = _fallback_kinds()
    compatibility_branches, inspector_fields = _inspector_compatibility()
    definition_paths, definitions = _node_definition_fields()
    executor = get_step_executor()

    validation_errors: list[str] = []
    registry_ids = [str(item["id"]) for item in registry_rows]
    if len(registry_ids) != len(set(registry_ids)):
        validation_errors.append("duplicate_registry_kind")
    if canonical != set(ALL_TASK_KINDS) or canonical != set(registry_ids):
        validation_errors.append("canonical_registry_set_mismatch")
    if set(definitions) != canonical:
        validation_errors.append("node_definition_registry_set_mismatch")
    if set(fallback) != canonical:
        validation_errors.append("frontend_fallback_registry_set_mismatch")
    if set(fallback) - canonical:
        validation_errors.append("unknown_frontend_fallback_kind")
    if set(adapters) - canonical:
        validation_errors.append("unknown_visual_process_adapter_kind")
    declared_aliases = {alias for item in registry_rows for alias in item.get("legacy_aliases") or []}
    if declared_aliases != set(LEGACY_MAP):
        validation_errors.append("legacy_alias_registry_mismatch")
    for alias, target in LEGACY_MAP.items():
        if target not in canonical or alias in canonical:
            validation_errors.append(f"invalid_legacy_mapping:{alias}")

    inventory: list[dict[str, Any]] = []
    by_id = {str(item["id"]): item for item in registry_rows}
    for kind in sorted(canonical):
        info = by_id[kind]
        mode = executor.execution_mode(kind)
        adapter = adapters.get(kind)
        if mode == "vp_adapter" and adapter is None:
            validation_errors.append(f"runtime_adapter_missing:{kind}")
        if mode != "vp_adapter" and adapter is not None and not bool(info["dispatch_capable"]):
            validation_errors.append(f"runtime_adapter_mode_mismatch:{kind}")
        registry_line = _line_for(REGISTRY_PATH, rf'^\s{{4}}"{re.escape(kind)}":\s*\{{')
        inspector_modes = ["schema_driven"]
        if kind in compatibility_branches:
            inspector_modes.append("legacy_compatibility")
        inventory.append(
            {
                "token": kind,
                "classification": "canonical",
                "canonical_kind": kind,
                "runtime": {
                    "implementation_status": info["implementation_status"],
                    "implementation_state": info["implementation_state"],
                    "backend_service": info["backend_service"],
                    "dispatch_capable": info["dispatch_capable"],
                    "execution_mode": mode,
                    "adapter": adapter.class_name if adapter else None,
                    "adapter_active": mode == "vp_adapter",
                },
                "frontend": {
                    "fallback_present": kind in fallback,
                    "inspector_modes": inspector_modes,
                },
                "consumed_field_paths": sorted(
                    set(adapters[kind].consumed_paths if kind in adapters else ())
                    | definition_paths.get(kind, set())
                    | {f"/metadata/{field}" for field in inspector_fields.get(kind, set())}
                ),
                "evidence": [
                    _evidence(REGISTRY_PATH, f"_KIND_INFO.{kind}", registry_line),
                    _evidence(
                        EXECUTOR_PATH, "StepExecutor.execution_mode", _line_for(EXECUTOR_PATH, r"def execution_mode")
                    ),
                    _evidence(
                        NODE_DEFINITION_PATH,
                        "compose_node_definition",
                        _line_for(NODE_DEFINITION_PATH, r"def compose_node_definition"),
                    ),
                    _evidence(
                        GENERATED_FALLBACK_PATH,
                        f"GENERATED_VISUAL_PROCESS_TASK_KINDS.{kind}",
                        fallback[kind],
                    ),
                    _evidence(
                        INSPECTOR_HTML_PATH,
                        "schema-driven field loop",
                        _line_for(INSPECTOR_HTML_PATH, r"@for \(field of definitionFields"),
                    ),
                ],
            }
        )
    for alias, target in sorted(LEGACY_MAP.items()):
        inventory.append(
            {
                "token": alias,
                "classification": "alias",
                "canonical_kind": target,
                "runtime": {
                    "implementation_status": by_id[target]["implementation_status"],
                    "implementation_state": "legacy_alias",
                    "backend_service": by_id[target]["backend_service"],
                    "dispatch_capable": by_id[target]["dispatch_capable"],
                    "execution_mode": "canonicalize_before_execution",
                    "adapter": None,
                    "adapter_active": False,
                },
                "frontend": {
                    "fallback_present": alias in fallback,
                    "inspector_modes": ["legacy_compatibility"] if target in compatibility_branches else [],
                },
                "consumed_field_paths": [],
                "evidence": [
                    _evidence(
                        REGISTRY_PATH, f"LEGACY_MAP.{alias}", _line_for(REGISTRY_PATH, rf'^\s+"{re.escape(alias)}"\s*:')
                    ),
                ],
            }
        )

    inventory.sort(key=lambda item: (item["token"], item["classification"]))
    fields = _field_inventory(canonical, adapters, definition_paths, inspector_fields)
    findings = _contract_findings()
    return {
        "schema": "ananta.visual-process-assistant-baseline.v1",
        "artifact_type": "domain_baseline",
        "verification_status": "verified" if not validation_errors else "failed",
        "source_grounding": {
            "repository_relative_evidence_only": True,
            "generated_source_identifiers": False,
            "generated_run_identifiers": False,
        },
        "summary": {
            "canonical_kind_count": len(canonical),
            "legacy_alias_count": len(LEGACY_MAP),
            "adapter_count": len(adapters),
            "frontend_fallback_count": len(fallback),
            "consumed_field_count": len(fields),
            "contract_finding_count": len(findings),
            "validation_error_count": len(set(validation_errors)),
        },
        "validation_errors": sorted(set(validation_errors)),
        "task_kind_inventory": inventory,
        "consumed_field_inventory": fields,
        "contract_findings": findings,
        "reproduce": [
            "python scripts/generate_visual_process_assistant_baseline.py --check",
            "python -m pytest -q tests/test_visual_process_assistant_baseline.py",
        ],
    }


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def write_baseline(path: Path = DEFAULT_OUTPUT) -> bytes:
    encoded = canonical_bytes(build_baseline())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return encoded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="Fail if the committed baseline differs or contains drift errors."
    )
    parser.add_argument("--stdout", action="store_true", help="Print the generated JSON instead of writing it.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    encoded = canonical_bytes(build_baseline())
    if arguments.stdout:
        sys.stdout.buffer.write(encoded)
    elif arguments.check:
        if not arguments.output.is_file() or arguments.output.read_bytes() != encoded:
            print("visual_process_assistant_baseline_drift", file=sys.stderr)
            return 1
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(encoded)
    if build_baseline()["validation_errors"]:
        print("visual_process_assistant_baseline_validation_failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
