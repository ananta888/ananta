"""Small shared building blocks for non-executable structured extractors.

The extractors in this package intentionally emit the same relation and
diagnostic shape as the established Java/XML extractors.  This module owns
only record construction and conservative value classification; parsing stays
inside focused, format-specific modules.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id

SECRET_KEY_PATTERN = re.compile(
    r"(?:^|[._-])(?:api[_-]?key|client[_-]?secret|password|passwd|pwd|secret|token|private[_-]?key)(?:$|[._-])",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^(?P<prefix>\s*[^\n:=]*(?:api[_-]?key|client[_-]?secret|password|passwd|pwd|secret|token|private[_-]?key)"
    r"[^\n:=]*\s*[:=]\s*)(?P<value>[^\n]+)$"
)
_PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)


def is_secret_key(path: str) -> bool:
    """Return whether a configuration path is likely to contain a secret.

    Matching is deliberately key-based.  Values are never copied into
    records, so an unusual secret key still cannot leak through value fields.
    """

    normalized = re.sub(r"\[\d+\]", "", path).replace(" ", "_")
    return bool(SECRET_KEY_PATTERN.search(normalized))


def redact_sensitive_text(text: str) -> tuple[str, int]:
    """Redact common assignment and private-key values from retained inert text."""

    redacted, assignment_count = _SECRET_ASSIGNMENT_PATTERN.subn(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        str(text),
    )
    redacted, private_key_count = _PRIVATE_KEY_BLOCK_PATTERN.subn(
        "[REDACTED PRIVATE KEY]",
        redacted,
    )
    return redacted, assignment_count + private_key_count


def scalar_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def line_number(text: str, offset: int) -> int:
    """Translate a character offset into a stable one-based line number."""

    return text.count("\n", 0, max(0, offset)) + 1


_POSITIONAL_FIELDS = {
    "column",
    "content_hash",
    "end_line",
    "evidence",
    "id",
    "line",
    "line_end",
    "line_start",
    "parent_id",
    "record_id",
    "source_id",
    "target_resolved",
}


def normalize_extraction_records(
    record_groups: Sequence[list[dict]],
    *,
    rel_path: str,
    source_text: str,
    extractor: str,
    trust_level: str = "extracted",
    verification_status: str = "unverified",
) -> None:
    """Add the common CodeCompass record/evidence projection in-place.

    The established ``kind``, ``id``, ``line`` and ``end_line`` fields are
    deliberately retained.  Their normalized aliases are additive so older
    consumers keep working while retrieval consumers get one stable shape.

    ``record_id`` is based on the non-positional semantic projection and an
    occurrence counter.  Consequently inserting blank lines updates the range
    and ``content_hash`` without changing the semantic record identity.
    ``content_hash`` binds the exact source evidence *and* its range; it does
    not serialize the raw evidence into metadata.
    """

    lines = source_text.splitlines()
    source_lines = source_text.splitlines(keepends=True)
    line_count = max(1, len(lines))
    occurrence_by_projection: dict[str, int] = {}

    for records in record_groups:
        for record in records:
            kind = str(record.get("record_kind") or record.get("kind") or "record")
            record.setdefault("record_kind", kind)
            record.setdefault("trust_level", trust_level)
            record.setdefault(
                "verification_status",
                "failed" if kind == "diagnostic" else verification_status,
            )
            record.setdefault(
                "provenance",
                {
                    "source": "tool-output",
                    "provider_id": "rag-helper",
                    "extractor_id": extractor,
                    "extractor_version": "structured-record.v1",
                },
            )

            is_file_record = kind.endswith("_file")
            raw_start = record.get("line_start", record.get("line", 1))
            raw_end = record.get("line_end", record.get("end_line"))
            try:
                line_start = max(1, int(raw_start or 1))
            except (TypeError, ValueError):
                line_start = 1
            if raw_end is None:
                line_end = line_count if is_file_record else line_start
            else:
                try:
                    line_end = max(line_start, int(raw_end))
                except (TypeError, ValueError):
                    line_end = line_start
            line_start = min(line_start, line_count)
            line_end = min(max(line_start, line_end), line_count)
            record.setdefault("line_start", line_start)
            record.setdefault("line_end", line_end)

            semantic_projection = {
                key: value
                for key, value in record.items()
                if key not in _POSITIONAL_FIELDS
            }
            projection_json = json.dumps(
                semantic_projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            projection_hash = hashlib.sha256(projection_json.encode("utf-8")).hexdigest()
            occurrence = occurrence_by_projection.get(projection_hash, 0) + 1
            occurrence_by_projection[projection_hash] = occurrence
            record.setdefault(
                "record_id",
                f"{kind}:{safe_id(rel_path, projection_hash, str(occurrence))}",
            )

            evidence_text = "".join(source_lines[line_start - 1 : line_end])
            hash_material = (
                f"{rel_path}\0{line_start}\0{line_end}\0{evidence_text}"
            ).encode("utf-8")
            content_hash = hashlib.sha256(hash_material).hexdigest()
            record.setdefault("content_hash", content_hash)

            existing_evidence = record.get("evidence")
            evidence = dict(existing_evidence) if isinstance(existing_evidence, dict) else {}
            evidence.setdefault("path", rel_path)
            evidence.setdefault("source_file", rel_path)
            evidence.setdefault("source_kind", "tool_output")
            evidence.setdefault("source_record_id", record["record_id"])
            evidence.setdefault("extractor", extractor)
            evidence.setdefault("line_start", line_start)
            evidence.setdefault("line_end", line_end)
            evidence.setdefault("content_hash", content_hash)
            evidence.setdefault("verification_status", record["verification_status"])
            record["evidence"] = evidence


@dataclass(frozen=True, slots=True)
class StructuredRecordFactory:
    rel_path: str
    format_name: str
    embedding_text_mode: str = "verbose"

    @property
    def file_id(self) -> str:
        return f"{self.format_name}_file:{safe_id(self.rel_path)}"

    def file_record(
        self,
        *,
        summary: dict,
        labels: Iterable[str] = (),
        parser_mode: str = "structured",
        confidence: float = 1.0,
    ) -> dict:
        visible_labels = list(dict.fromkeys(str(item) for item in labels if str(item)))
        return {
            "kind": f"{self.format_name}_file",
            "file": self.rel_path,
            "id": self.file_id,
            "parser_mode": parser_mode,
            "confidence": confidence,
            "labels": visible_labels[:100],
            "summary": summary,
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                (
                    f"{self.format_name.upper()} file {self.rel_path}. "
                    f"Labels: {', '.join(visible_labels[:30]) or 'none'}."
                ),
                (f"{self.format_name.upper()} {self.rel_path}. Labels {compact_list(visible_labels, limit=8)}."),
            ),
        }

    def symbol(
        self,
        *,
        kind: str,
        name: str,
        line: int,
        column: int = 1,
        parent_id: str | None = None,
        ordinal: int = 0,
        **fields: object,
    ) -> dict:
        symbol_id = f"{kind}:{safe_id(self.rel_path, name, str(line), str(column), str(ordinal))}"
        return {
            "kind": kind,
            "file": self.rel_path,
            "id": symbol_id,
            "parent_id": parent_id or self.file_id,
            "name": name,
            "line": max(1, int(line)),
            "column": max(1, int(column)),
            **fields,
        }

    def relation(
        self,
        *,
        source_id: str,
        source_kind: str,
        source_name: str,
        relation: str,
        target: str,
        target_resolved: str | None = None,
        line: int | None = None,
        resolution_status: str | None = None,
        **fields: object,
    ) -> dict:
        result = {
            "kind": "relation",
            "file": self.rel_path,
            "id": f"relation:{safe_id(self.rel_path, source_id, relation, target, str(line or ''))}",
            "source_id": source_id,
            "source_kind": source_kind,
            "source_name": source_name,
            "relation": relation,
            "target": target,
            "target_resolved": target_resolved,
            "resolution_status": resolution_status or ("resolved" if target_resolved else "unresolved"),
            "weight": 1,
            **fields,
        }
        if line is not None:
            result["line"] = max(1, int(line))
        return result

    def diagnostic(
        self,
        code: str,
        message: str,
        *,
        line: int = 1,
        column: int = 1,
        severity: str = "warning",
        fallback: str | None = None,
    ) -> dict:
        result = {
            "kind": "diagnostic",
            "file": self.rel_path,
            "id": f"diagnostic:{safe_id(self.rel_path, code, str(line), str(column))}",
            "parent_id": self.file_id,
            "code": code,
            "message": message,
            "severity": severity,
            "line": max(1, int(line)),
            "column": max(1, int(column)),
        }
        if fallback:
            result["fallback"] = fallback
        return result


def stats_for(
    format_name: str,
    rel_path: str,
    index_records: list[dict],
    detail_records: list[dict],
    relation_records: list[dict],
    *,
    parser_mode: str = "structured",
    diagnostics: Iterable[dict] = (),
    **counts: object,
) -> dict:
    diagnostics_list = list(diagnostics)
    return {
        "kind": format_name,
        "file": rel_path,
        "parser_mode": parser_mode,
        "index_count": len(index_records),
        "detail_count": len(detail_records),
        "relation_count": len(relation_records),
        "diagnostic_count": len(diagnostics_list),
        "diagnostic_codes": sorted({str(item.get("code")) for item in diagnostics_list if item.get("code")}),
        **counts,
    }
