"""CTA-008: n8n-Verifier mit Reuse der Extractor-Primitives.

Importiert is_n8n_workflow und die Secret-Patterns aus dem fertigen
n8n-Track (rag_helper.extractors.n8n_workflow_extractor) statt eigene
Regexe zu duplizieren. rag-helper ist kein installiertes Paket, daher
wird das Verzeichnis beim Import auf sys.path gelegt (Repo-Layout ist
stabil; siehe archivierter n8n-Track).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_RAG_HELPER_DIR = Path(__file__).resolve().parents[3] / "rag-helper"
if _RAG_HELPER_DIR.is_dir() and str(_RAG_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_HELPER_DIR))

from rag_helper.extractors.n8n_workflow_extractor import (  # noqa: E402
    _SECRET_KEY_PATTERN,
    _SECRET_VALUE_PATTERNS,
    is_n8n_workflow,
)

STATUS_PASSED = "passed"
STATUS_WARNING = "warning"
STATUS_FAILED = "failed"

REASON_INVALID_JSON = "invalid_json"
REASON_NOT_N8N_WORKFLOW = "not_n8n_workflow"
REASON_DANGLING_CONNECTION = "dangling_connection"
REASON_UNKNOWN_NODE_TYPE = "unknown_node_type"
REASON_SECRET = "hardcoded_secret_candidate"
REASON_NO_TASK_RELATION = "no_task_relation"

_REDACTED = "<redacted>"


def _looks_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def _redact_secrets(value: Any, *, key_hint: str = "") -> tuple[Any, int]:
    """Redigiert Secret-Kandidaten rekursiv; Rueckgabe (Wert, Anzahl)."""
    count = 0
    key_is_secret = bool(_SECRET_KEY_PATTERN.search(key_hint)) if key_hint else False
    if isinstance(value, str):
        if key_is_secret or _looks_secret_value(value):
            return _REDACTED, 1
        return value, 0
    if isinstance(value, dict):
        cleaned: dict = {}
        for key, nested in value.items():
            cleaned[key], nested_count = _redact_secrets(nested, key_hint=str(key))
            count += nested_count
        return cleaned, count
    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            cleaned_item, nested_count = _redact_secrets(item, key_hint=key_hint)
            cleaned_list.append(cleaned_item)
            count += nested_count
        return cleaned_list, count
    return value, count


def verify_workflow_part(
    part: str | dict,
    *,
    part_kind: str = "full_workflow",
    known_node_types: set[str] | None = None,
    task_terms: list[str] | None = None,
) -> dict:
    """Prueft ein n8n-Teil vor Anzeige/Export.

    Rueckgabe: {status, reasons, redactions, verified_part}. failed bei
    Struktur- oder Secret-Blockern; warning u.a. bei unbekanntem
    node_type oder fehlendem Aufgabenbezug.
    """
    reasons: list[str] = []
    blockers: list[str] = []

    if isinstance(part, str):
        try:
            payload = json.loads(part)
        except (json.JSONDecodeError, ValueError):
            return {"status": STATUS_FAILED, "reasons": [REASON_INVALID_JSON], "redactions": 0, "verified_part": None}
    else:
        payload = part
    if not isinstance(payload, dict):
        return {"status": STATUS_FAILED, "reasons": [REASON_INVALID_JSON], "redactions": 0, "verified_part": None}

    if part_kind == "full_workflow" and not is_n8n_workflow(payload):
        blockers.append(REASON_NOT_N8N_WORKFLOW)

    nodes = [n for n in payload.get("nodes") or [] if isinstance(n, dict)]
    node_names = {str(n.get("name")) for n in nodes}
    connections = payload.get("connections") if isinstance(payload.get("connections"), dict) else {}
    for source, groups in connections.items():
        if node_names and str(source) not in node_names:
            blockers.append(REASON_DANGLING_CONNECTION)
            break
        if not isinstance(groups, dict):
            continue
        for outputs in groups.values():
            if not isinstance(outputs, list):
                continue
            for targets in outputs:
                for target in targets if isinstance(targets, list) else []:
                    if isinstance(target, dict) and node_names and str(target.get("node")) not in node_names:
                        blockers.append(REASON_DANGLING_CONNECTION)

    if known_node_types:
        vocabulary = {str(t).lower() for t in known_node_types}
        for node in nodes or ([payload] if part_kind == "single_node" else []):
            node_type = str(node.get("type") or "").lower()
            if node_type and node_type not in vocabulary:
                reasons.append(REASON_UNKNOWN_NODE_TYPE)
                break

    verified_part, redactions = _redact_secrets(payload)
    if redactions:
        blockers.append(REASON_SECRET)

    if task_terms:
        haystack = json.dumps(payload).lower()
        if not any(str(term).lower() in haystack for term in task_terms if str(term).strip()):
            reasons.append(REASON_NO_TASK_RELATION)

    all_reasons = sorted(set(blockers)) + sorted(set(reasons) - set(blockers))
    if blockers:
        status = STATUS_FAILED
    elif reasons:
        status = STATUS_WARNING
    else:
        status = STATUS_PASSED
    return {"status": status, "reasons": all_reasons, "redactions": redactions, "verified_part": verified_part}


def known_node_types_from_examples(workflows: list[dict]) -> set[str]:
    """node_type-Vokabular aus geladenen Beispiel-Workflows ableiten."""
    vocabulary: set[str] = set()
    for entry in workflows:
        workflow = entry.get("workflow") if isinstance(entry, dict) else None
        for node in (workflow or {}).get("nodes") or []:
            if isinstance(node, dict) and node.get("type"):
                vocabulary.add(str(node["type"]).lower())
    return vocabulary
