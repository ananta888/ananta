"""Fail-closed normalization of public scalar values (statuses, codes, identities,
references, numbers) for runtime status projection.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_runtime_status_vocabulary import (
    _MAX_SOURCE_STATUS_CHARS,
    _PUBLIC_EVENT_TYPES,
    _PUBLIC_RUNTIME_REASON_CODES,
    _PUBLIC_SOURCE_STATUSES,
    _PUBLIC_STATUS_ALIASES,
    _PUBLIC_STRUCTURED_ENUM_VALUES,
    _REDACTED_PUBLIC_TEXT,
    _REDACTED_REASON_CODE,
    _REFERENCE_RE,
)


def _canonical_step_ids(binding: WorkflowControlRunBinding) -> tuple[str, ...]:
    plan = binding.execution_plan or {}
    if not (plan.get("metadata") or {}).get("bpmn_definition_hash"):
        return tuple(step.step_id for step in binding.request.steps)
    # Expanded BPMN activations are identified by the persisted Hub plan.
    # The submitted source graph cannot define the runtime's node namespace.
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("workflow_runtime_canonical_nodes_required")
    ids = tuple(_identity(node.get("node_id"), field_name="canonical_step_id") for node in nodes)
    if len(ids) != len(set(ids)):
        raise ValueError("workflow_runtime_canonical_nodes_invalid")
    return ids


def _normalized_public_status(raw: Any, *, field_name: str) -> str:
    value = _required_bounded_text(
        raw,
        field_name=field_name,
        maximum=_MAX_SOURCE_STATUS_CHARS,
    ).lower()
    if value not in _PUBLIC_SOURCE_STATUSES:
        raise ValueError(f"workflow_runtime_source_{field_name}_unsupported")
    return _PUBLIC_STATUS_ALIASES.get(value, value)


def _normalized_public_step_status(raw: Any, *, field_name: str) -> str:
    status = _normalized_public_status(raw, field_name=field_name)
    if status == "not_found":
        raise ValueError(f"workflow_runtime_source_{field_name}_unsupported")
    return status


def _observed_timestamp(observed_at: float | None, *, previous: Mapping[str, Any]) -> float:
    candidate = time.time() if observed_at is None else observed_at
    timestamp = _nonnegative_number(candidate, field_name="observed_at")
    previous_raw = previous.get("updated_at")
    if previous_raw is None:
        return timestamp
    previous_timestamp = _nonnegative_number(
        previous_raw,
        field_name="previous_updated_at",
    )
    return max(timestamp, previous_timestamp)


def _required_bounded_text(value: Any, *, field_name: str, maximum: int) -> str:
    return _bounded_text(
        value,
        field_name=field_name,
        maximum=maximum,
        allow_empty=False,
    )


def _bounded_text(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    if (
        normalized != value
        or len(value) > maximum
        or any(not character.isprintable() or character in {"\x7f", "\x00"} for character in value)
    ):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return value


def _redacted_public_text(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=maximum,
        allow_empty=allow_empty,
    )
    return "" if not bounded else _REDACTED_PUBLIC_TEXT


def _contains_sensitive_public_scalar(value: str) -> bool:
    """Reject secret/PII-shaped values before typed public projection.

    Identity, reference, and code grammars intentionally permit opaque text.
    Syntax validation alone therefore cannot be the public-data boundary.
    This conservative predicate is shared by every typed scalar projector and
    by nested structured evidence so an attacker cannot move the same value
    between fields to bypass redaction.
    """

    snake_value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", snake_value).strip("_")
    parts = frozenset(part for part in normalized.split("_") if part)
    sensitive_parts = {
        "authorization",
        "cookie",
        "credential",
        "password",
        "prompt",
    }
    secret_payload_parts = {"content", "data", "key", "payload", "value"}
    sensitive_compounds = {
        "api_key",
        "private_key",
        "raw_content",
    }
    if parts.intersection(sensitive_parts) or any(compound in normalized for compound in sensitive_compounds):
        return True
    if parts.intersection({"secret", "token"}) and parts.intersection(secret_payload_parts):
        return True
    if re.search(r"(?i)(?:^|[^a-z0-9])(?:bearer\s+|github_pat_|gh[pousr]_|sk-|xox[baprs]-)", value):
        return True
    if re.search(r"(?:^|[^0-9])\d{3}[-_]\d{2}[-_]\d{4}(?:$|[^0-9])", value):
        return True
    return False


def _public_reason_code(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=512,
        allow_empty=allow_empty,
    )
    if not bounded:
        return ""
    normalized = bounded.lower()
    if normalized not in _PUBLIC_RUNTIME_REASON_CODES:
        return _REDACTED_REASON_CODE
    return normalized


def _public_event_type(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(value, field_name=field_name, maximum=160)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", bounded):
        return "workflow.runtime.observed"
    normalized = bounded.lower()
    return normalized if normalized in _PUBLIC_EVENT_TYPES else "workflow.runtime.observed"


def _public_event_status(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=64,
        allow_empty=True,
    )
    if not bounded:
        return ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", bounded):
        return "unknown"
    normalized = bounded.lower()
    return _PUBLIC_STATUS_ALIASES.get(normalized, normalized) if normalized in _PUBLIC_SOURCE_STATUSES else "unknown"


def _public_code(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=maximum,
        allow_empty=allow_empty,
    )
    if not bounded:
        return ""
    if _contains_sensitive_public_scalar(bounded):
        raise ValueError(f"workflow_runtime_source_{field_name}_sensitive")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", bounded):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return bounded


def _public_structured_enum(
    value: Any,
    *,
    structured_key: str,
    field_name: str,
) -> str:
    """Project only explicitly catalogued structured enum values.

    Worker-owned identifiers and open-ended codes are not evidence of public
    provenance merely because they satisfy a lexical grammar. Unknown fields
    therefore retain presence only; adding a new clear-text value requires an
    explicit contract entry here.
    """

    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=160,
        allow_empty=True,
    )
    if not bounded:
        return ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", bounded):
        return _REDACTED_PUBLIC_TEXT
    allowed = _PUBLIC_STRUCTURED_ENUM_VALUES.get(structured_key)
    normalized = bounded.lower()
    return normalized if allowed is not None and normalized in allowed else _REDACTED_PUBLIC_TEXT


def _local_public_route(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=2048,
    )
    if _contains_sensitive_public_scalar(bounded):
        return _REDACTED_PUBLIC_TEXT
    parsed = urlsplit(bounded)
    if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/"):
        return _REDACTED_PUBLIC_TEXT
    if parsed.path == "/model-training":
        allowed_query_keys = {"dataset_id", "job_id", "tab"}
        try:
            query = parse_qsl(parsed.query, keep_blank_values=False, strict_parsing=True) if parsed.query else []
        except ValueError:
            return _REDACTED_PUBLIC_TEXT
        if len(query) > 3 or any(key not in allowed_query_keys for key, _ in query):
            return _REDACTED_PUBLIC_TEXT
        for key, item in query:
            if key == "tab" and item not in {"datasets", "jobs"}:
                return _REDACTED_PUBLIC_TEXT
            if key != "tab":
                try:
                    _identity(item, field_name=field_name)
                except ValueError:
                    return _REDACTED_PUBLIC_TEXT
        return bounded
    if (
        re.fullmatch(
            r"/api/ml-intern-training/jobs/[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}(?:/events)?",
            parsed.path,
        )
        and not parsed.query
    ):
        return bounded
    return _REDACTED_PUBLIC_TEXT


def _identity(value: Any, *, field_name: str) -> str:
    result = _identity_syntax(value, field_name=field_name)
    if _contains_sensitive_public_scalar(result):
        raise ValueError(f"workflow_runtime_source_{field_name}_sensitive")
    return result


def _identity_syntax(value: Any, *, field_name: str) -> str:
    result = _bounded_text(value, field_name=field_name, maximum=256)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}", result):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return result


def _optional_identity(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=256,
        allow_empty=True,
    )
    return "" if not bounded else _identity(bounded, field_name=field_name)


def _reference(value: Any, *, field_name: str) -> str:
    result = _reference_syntax(value, field_name=field_name)
    if _contains_sensitive_public_scalar(result):
        raise ValueError(f"workflow_runtime_source_{field_name}_sensitive")
    return result


def _reference_syntax(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not _REFERENCE_RE.fullmatch(value):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return value


def _optional_reference(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=512,
        allow_empty=True,
    )
    return "" if not bounded else _reference(bounded, field_name=field_name)


def _nonnegative_integer(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return value


def _positive_integer(value: Any, *, field_name: str) -> int:
    result = _nonnegative_integer(value, field_name=field_name)
    if result < 1:
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return result


def _nonnegative_number(value: Any, *, field_name: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return value


def _cursor(value: Any) -> str:
    if not isinstance(value, str) or not value.isdigit() or len(value) > 20:
        raise ValueError("workflow_runtime_event_cursor_invalid")
    return str(int(value))
